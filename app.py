
from pprint import pprint
import os
import re
import time
import unicodedata
import uuid
import json
import datetime
import io
from urllib.parse import urlencode
from typing import Dict, Any
from flask import (
	Flask,
	render_template,
	request,
	redirect,
	url_for,
	jsonify,
	send_from_directory,
	flash,
	abort,
	session,
	make_response,
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from jinja2 import Environment, FileSystemLoader, ChoiceLoader, BaseLoader, TemplateNotFound, select_autoescape
from utils.theme_loader import load_theme_functions,copy_theme_static_files
from functools import wraps
import pyotp
from dotenv import load_dotenv
from data_store import (
	get_pages,
	get_draft_pages,
	get_posts,
	get_draft_posts,
	get_categories,
	get_tags,
	get_site_settings,
	add_page,
	add_draft_page,
	delete_page,
	add_post,
	add_draft_post,
	update_site_settings,
	update_tag_counts,
	db_manager,
)
from PIL import Image
import io
import secrets
from flask import send_file
import xml.etree.ElementTree as ET


from sqlalchemy.exc import SQLAlchemyError


from data_store import Page, Post, Category, Tag, SiteSetting
from data_store import ROLES, ROLE_ADMINISTRATOR, ROLE_EDITOR, ROLE_AUTHOR, ROLE_CONTRIBUTOR, get_latest_posts
from auth import (
	login_required,
	capability_required,
	current_user,
	can,
	login_user,
	logout_user,
	is_locked_out,
	record_failure,
	clear_failures,
	verify_password,
	hash_password,
	validate_password,
	validate_role,
	ROLE_LABELS,
	ROLE_DESCRIPTIONS,
)
import content_store
from content_store import post_url, list_sections, get_section_by_path, get_section_by_slug
from content_views import bp as content_blueprint, section_index_view
from extension_manager import manager as extensions, ExtensionError, CORE_VERSION
from page_templates import (
	load_page_templates,
	parse_fields,
	fill_defaults,
	default_values,
	dump_fields,
	DEFAULT_TEMPLATE_KEY,
)
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")


"""
Application Configuration and Initialization
-----------------------------------------

This section handles the core Flask application setup and configuration.

Features:
- Environment variable loading
- Flask app initialization
- Security configuration
- Template engine setup
- Static file handling
- Upload configuration
- Theme initialization

Security:
- Secret key management
- File upload restrictions
- Secure default settings
- Environment-based config

Configuration Sources:
1. Environment variables (.env file)
2. Default secure values
3. Runtime settings
4. Theme configuration
"""


load_dotenv()
app = Flask(__name__, static_folder="static")
app.url_map.strict_slashes = False
def _load_secret_key():
	"""
	Use SECRET_KEY from the environment when set. Otherwise keep a random key in
	instance/secret_key so sessions survive restarts (and are not shared by accident).
	"""
	env_key = os.getenv("SECRET_KEY")
	if env_key:
		return env_key
	instance_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance")
	os.makedirs(instance_dir, exist_ok=True)
	key_path = os.path.join(instance_dir, "secret_key")
	if not os.path.exists(key_path):
		with open(key_path, "w", encoding="utf-8") as fh:
			fh.write(secrets.token_hex(32))
		try:
			os.chmod(key_path, 0o600)
		except OSError:
			pass
	with open(key_path, encoding="utf-8") as fh:
		return fh.read().strip()


app.config["SECRET_KEY"] = _load_secret_key()
app.config.update(
	SESSION_COOKIE_HTTPONLY=True,
	SESSION_COOKIE_SAMESITE="Lax",
	SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "0") == "1",
	PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=8),
)

class _ExtensionTemplateLoader(BaseLoader):
	"""Serves "<extension-slug>/<file>" from extensions/<slug>/templates/ while that extension is active."""

	def get_source(self, environment, template):
		slug, _, rest = template.partition("/")
		folder = extensions.template_dir(slug) if rest else None
		if not folder:
			raise TemplateNotFound(template)
		path = os.path.normpath(os.path.join(folder, rest))
		if not path.startswith(os.path.normpath(folder) + os.sep) or not os.path.isfile(path):
			raise TemplateNotFound(template)
		mtime = os.path.getmtime(path)
		with open(path, encoding="utf-8") as fh:
			source = fh.read()
		return source, path, lambda: os.path.isfile(path) and os.path.getmtime(path) == mtime


def _theme_loader(theme_name):
	return ChoiceLoader(
		[
			FileSystemLoader(os.path.join(base_dir, "templates")),
			_ExtensionTemplateLoader(),
			FileSystemLoader(os.path.join(base_dir, "themes", theme_name, "templates")),
			FileSystemLoader(os.path.join(base_dir, "themes", "default", "templates")),
		]
	)


def available_themes():
	"""Theme folders that contain a templates/ directory."""
	root = os.path.join(base_dir, "themes")
	if not os.path.isdir(root):
		return ["default"]
	return sorted(
		name for name in os.listdir(root)
		if os.path.isdir(os.path.join(root, name, "templates"))
	)


def apply_theme(theme_name):
	"""Switch the active theme immediately: templates, page templates and static files."""
	global THEME_NAME, PAGE_TEMPLATES, theme_functions
	if theme_name not in available_themes():
		return False
	THEME_NAME = theme_name
	loader = _theme_loader(theme_name)
	app.jinja_loader = loader
	app.jinja_env.loader = loader
	app.jinja_env.cache.clear()
	PAGE_TEMPLATES = load_page_templates(theme_name)
	app.jinja_env.globals["page_templates"] = PAGE_TEMPLATES
	copy_theme_static_files(theme_name)
	theme_functions = load_theme_functions(theme_name)
	return True


base_dir = os.path.dirname(os.path.abspath(__file__))
site_settings = get_site_settings()
THEME_NAME = site_settings.get("theme", "default")

app.jinja_loader = _theme_loader(THEME_NAME)
PAGE_TEMPLATES = load_page_templates(THEME_NAME)
app.jinja_env.globals.update(
	latest_posts=get_latest_posts,
	post_url=post_url,
	directory_entries=content_store.list_entries,
	section_href=lambda cat_slug: (
		f"/{(get_section_by_slug(cat_slug) or {}).get('section_path')}/"
		if get_section_by_slug(cat_slug) else f"/category/{cat_slug}"
	),
	ENTRY_KINDS=content_store.ENTRY_KINDS,
	hook=lambda name, *a, **kw: extensions.do_action(name, *a, **kw),
	apply_filters=lambda name, value, *a: extensions.apply_filters(name, value, *a),
	ROLE_LABELS=ROLE_LABELS,
	ROLES=ROLES,
	page_templates=PAGE_TEMPLATES,
)
app.jinja_env.filters["pretty_date"] = content_store.pretty_date
UPLOAD_DIR = "uploads"

IMAGE_SIZES = {"thumbnail": (150, 150), "medium": (300, 300), "large": (1024, 1024)}

if not os.path.exists(UPLOAD_DIR):
	os.makedirs(UPLOAD_DIR)

if not os.path.exists(os.path.join(UPLOAD_DIR, "thumbnails")):
	os.makedirs(os.path.join(UPLOAD_DIR, "thumbnails"))

theme_functions = load_theme_functions(THEME_NAME)
copy_theme_static_files(THEME_NAME)

app.config["UPLOAD_FOLDER"] = UPLOAD_DIR
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024


def slugify(title: str) -> str:
    if not title:
        return ""

    title = unicodedata.normalize("NFKC", title)

    slug = re.sub(r"[^\w\s\-\u0980-\u09FF]", "", title)

    slug = slug.strip().lower()
    slug = re.sub(r"[-\s]+", "-", slug)

    return slug


def create_image_thumbnails(image_path: str, filename: str) -> Dict[str, str]:

	"""
	Create image thumbnails in multiple sizes and save them.

	Args:
		image_path (str): Path to the original image file.
		filename (str): The base filename for the image.

	Returns:
		Dict[str, str]: Mapping of size name to thumbnail filename.
	"""

	thumbnails = {}
	try:
		with Image.open(image_path) as img:
			if img.mode == "RGBA":
				bg = Image.new("RGB", img.size, "white")
				bg.paste(img, mask=img.split()[3])
				img = bg

			for size_name, dimensions in IMAGE_SIZES.items():
				thumb = img.copy()
				thumb.thumbnail(dimensions, Image.Resampling.LANCZOS)

				name, ext = os.path.splitext(filename)
				thumb_filename = f"{name}-{size_name}{ext}"
				thumb_path = os.path.join(
					app.config["UPLOAD_FOLDER"], "thumbnails", thumb_filename
				)

				thumb.save(thumb_path, quality=90, optimize=True)
				thumbnails[size_name] = thumb_filename

	except Exception as e:
		print(f"Error creating thumbnails: {str(e)}")

	return thumbnails


def get_page_by_slug(slug: str):
	"""
	Retrieves a page by its URL slug from the database.
	
	Args:
		slug (str): The URL-friendly slug of the page
		
	Returns:
		dict: Page data if found, including:
			- title: Page title
			- content: Page content
			- description: Page description
			- status: Published/draft status
			- created_at: Creation timestamp
			- updated_at: Last update timestamp
		None: If page is not found
		
	Raises:
		SQLAlchemyError: If database query fails
	"""
	db = db_manager.get_session()
	try:
		page = db.query(Page).filter(Page.slug == slug).first()
		return page.to_dict() if page else None
	except SQLAlchemyError as e:
		print(f"Error getting page by slug: {str(e)}")
		return None
	finally:
		db.close()


def get_post_by_slug(slug: str):
	"""
	Retrieves a post by its URL slug from either published or draft posts.

	Features:
	- Database query optimization
	- Error handling
	- Post state independence
	- Safe return value

	Args:
		slug (str): The URL-friendly slug of the post

	Returns:
		dict: Post data if found, including:
			- title: Post title
			- content: Post content
			- excerpt: Post excerpt
			- status: Published/draft status
			- created_at: Creation timestamp
			- updated_at: Last update timestamp
		None: If post is not found or error occurs

	Raises:
		SQLAlchemyError: Database query errors (caught internally)
	"""
	db = db_manager.get_session()
	try:
		post = db.query(Post).filter(Post.slug == slug).first()
		return post.to_dict() if post else None
	except SQLAlchemyError as e:
		print(f"Error getting post by slug: {str(e)}")
		return None
	finally:
		db.close()
def delete_post_by_slug(slug: str) -> bool:
	"""
	Deletes a post from the database using its URL slug.

	Features:
	- Safe database transaction
	- Error handling with rollback
	- Post state independence
	- Logging of errors

	Processing Steps:
	1. Query for post by slug
	2. Delete if found
	3. Commit transaction
	4. Rollback on error

	Args:
		slug (str): The URL-friendly slug of the post to delete

	Returns:
		bool: True if post was found and deleted
			  False if post not found or error occurred

	Raises:
		SQLAlchemyError: Database operation errors (caught internally)
	"""
	db = db_manager.get_session()
	try:
		post = db.query(Post).filter(Post.slug == slug).first()
		if post:
			db.delete(post)
			db.commit()
			return True
		return False
	except SQLAlchemyError as e:
		db.rollback()
		print(f"Error deleting post: {str(e)}")
		return False
	finally:
		db.close()
def update_page_by_slug(slug: str, page_data: dict) -> bool:
	"""Update a page's content, permalink, status, template and custom fields."""
	db = db_manager.get_session()
	try:
		page = db.query(Page).filter(Page.slug == slug).first()
		if not page:
			return False
		new_slug = page_data.get("new_slug") or slug
		if new_slug != slug:
			if db.query(Page).filter(Page.slug == new_slug).first():
				return False
			page.slug = new_slug
		page.title = page_data.get("title", page.title)
		page.content = page_data.get("content", page.content)
		page.description = page_data.get("description", page.description)
		page.status = page_data.get("status", page.status)
		if "template" in page_data:
			page.template = page_data["template"] or "default"
		if "fields" in page_data:
			page.fields = dump_fields(page_data["fields"] or {})
		page.updated_at = datetime.datetime.utcnow()
		db.commit()
		return True
	except SQLAlchemyError as e:
		db.rollback()
		print(f"Error updating page: {str(e)}")
		return False
	finally:
		db.close()


def update_post_by_slug(slug: str, post_data: dict) -> bool:
	"""
	Updates an existing post's content and metadata.
	
	Features:
	- Updates basic post data (title, content, excerpt)
	- Manages post status (published/draft)
	- Handles category assignment
	- Manages tag relationships
	- Updates timestamps appropriately
	- Sets published_at for newly published posts
	
	Args:
		slug (str): The post's URL slug
		post_data (dict): Updated post data including:
			- title: Post title
			- content: Post content
			- excerpt: Post excerpt
			- status: Publication status
			- category: Category slug
			- tags: List of tag names
			
	Returns:
		bool: True if update successful, False otherwise
		
	Raises:
		SQLAlchemyError: On database operation failure
	"""
	db = db_manager.get_session()
	try:
		post = db.query(Post).filter(Post.slug == slug).first()
		if post:
			post.title = post_data.get("title", post.title)
			post.content = post_data.get("content", post.content)
			post.excerpt = post_data.get("excerpt", post.excerpt)
			post.status = post_data.get("status", post.status)
			post.updated_at = datetime.datetime.utcnow()
			if "cover_image" in post_data:
				post.cover_image = post_data["cover_image"] or None
			if "meta" in post_data:
				post.meta = json.dumps(post_data["meta"] or {}, ensure_ascii=False)
			if post_data.get("published_at"):
				post.published_at = post_data["published_at"]

			if "category" in post_data:
				category = (
					db.query(Category)
					.filter(Category.slug == post_data["category"])
					.first()
				)
				post.category_id = category.id if category else None

			if "tags" in post_data:
				post.tags.clear()
				for tag_name in post_data["tags"]:
					tag = db.query(Tag).filter(Tag.name == tag_name).first()
					if not tag:
						tag = Tag(name=tag_name)
						db.add(tag)
					post.tags.append(tag)

			if post_data.get("status") == "published" and not post.published_at:
				post.published_at = datetime.datetime.utcnow()

			db.commit()
			return True
		return False
	except SQLAlchemyError as e:
		db.rollback()
		print(f"Error updating post: {str(e)}")
		return False
	finally:
		db.close()


def add_category_to_db(name: str) -> bool:
	"""
	Adds a new category to the database with validation.
	
	Features:
	- Name uniqueness validation
	- Automatic slug generation
	- Database transaction handling
	- Error handling and logging
	
	Processing Steps:
	1. Sanitize input name
	2. Generate URL-friendly slug
	3. Check for existing category
	4. Create new category if unique
	5. Commit transaction
	
	Args:
		name (str): Category name to add
		
	Returns:
		bool: True if category added successfully, False if:
			- Category already exists
			- Database error occurs
			- Invalid name provided
			
	Raises:
		SQLAlchemyError: On database operation failure
	"""
	db = db_manager.get_session()
	try:
		slug = name.strip().lower().replace(" ", "-")
		existing = (
			db.query(Category)
			.filter((Category.name == name) | (Category.slug == slug))
			.first()
		)
		if existing:
			return False
		category = Category(name=name, slug=slug)
		db.add(category)
		db.commit()
		return True
	except Exception as e:
		db.rollback()
		print(f"Error adding category: {str(e)}")
		return False
	finally:
		db.close()


def allowed_file(filename):
	"""
	Check if the uploaded file has an allowed extension.

	Args:
		filename (str): The name of the file.

	Returns:
		bool: True if allowed, False otherwise.
	"""
	ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
	return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def validate_csrf_token():
	"""
	Validate the CSRF token from the request headers or form data.

	Returns:
		bool: True if the CSRF token is valid, False otherwise.
	"""	
	token = request.headers.get("X-CSRF-Token")
	if not token:
		token = request.form.get("csrf_token")
	return token and token == session.get("csrf_token")


def generate_and_store_sitemap():
	"""
	Generates the sitemap XML and stores it in the dedicated sitemaps table.
	"""
	try:

		site_settings = get_site_settings()
		base_url = site_settings.get("site_domain") or ""
		base_url = base_url.strip()
		print("base url:", base_url)
		if not base_url:
			return

		if base_url.endswith('/'):
			base_url = base_url[:-1]
		
		pages_data = []

		posts = get_posts()
		for slug, post in posts.items():
			if post.get("status") == "published":
				pages_data.append({
					"loc": f"{base_url}{post_url(post)}",
					"lastmod": post.get('updated_at', '').split('T')[0]
				})

		for section in list_sections():
			pages_data.append({"loc": f"{base_url}/{section['section_path']}/", "lastmod": ""})
		for kind, cfg in content_store.ENTRY_KINDS.items():
			pages_data.append({"loc": f"{base_url}{cfg['base_path']}", "lastmod": ""})
			for rec in content_store.list_entries(kind):
				pages_data.append({"loc": f"{base_url}{cfg['base_path']}{rec['slug']}", "lastmod": ""})

		public_pages = get_pages()
		for slug, page in public_pages.items():
			if page.get("status") == "published":
				pages_data.append({
					"loc": f"{base_url}/{slug}",
					"lastmod": page.get('updated_at', '').split('T')[0]
				})

		sitemap_xml = render_template("sitemap.xml", pages=pages_data)

		
		db_manager.update_sitemap_content(sitemap_xml)
		print("Sitemap successfully updated in the database.")
		return True
	except Exception as e:
		print(f"Error generating sitemap: {str(e)}")
		return False


@app.errorhandler(404)
def page_not_found(e):
	"""
	Handles 404 Not Found errors.
	
	Features:
	- Custom 404 error page
	- Maintains site theme
	
	Args:
		e: Error instance
		
	Returns:
		tuple: (Rendered template, HTTP status code)
	"""	
	return render_template("404.html", page={"title": "404 Not Found"}), 404


@app.errorhandler(500)
def server_error(e):
	"""
	Handles 500 Internal Server Error.
	
	Features:
	- Custom error page
	- Generic error messaging
	- Maintains site theme
	- Prevents exposure of system details
	
	Args:
		e: Error instance
		
	Returns:
		tuple: (Rendered template, HTTP status code)
	"""	
	return render_template("404.html", page={"title": "Error"}), 500


@app.context_processor
def inject_csrf_token():
	"""
	Injects CSRF token into all templates automatically.
	
	Features:
	- Automatic token generation
	- Session-based storage
	- Cryptographically secure tokens
	- Consistent token across requests
	
	Token Generation:
	- Uses secrets.token_hex for secure random values
	- 16 bytes (32 hex characters) length
	- Generated only if not present
	
	Template Usage:
	- Available as csrf_token variable
	- Used in forms via hidden input
	- Available for JavaScript via meta tag
	
	Returns:
		dict: Context containing CSRF token
		
	Security:
		- Crypto-secure random generation
		- Session-based storage
		- Automatic injection
	"""	
	if "csrf_token" not in session:
		session["csrf_token"] = secrets.token_hex(16)
	return {"csrf_token": session["csrf_token"]}


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RESERVED_SLUGS = {
	"admin", "login", "logout", "search", "post", "page", "category", "tag",
	"api", "uploads", "static", "sitemap.xml", "setup", "upload_image",
	"ext",
}


def _safe_next(target):
	"""Only allow same-site relative redirects after login (prevents open redirects)."""
	if target and target.startswith("/") and not target.startswith("//"):
		return target
	return None


@app.route("/login", methods=["GET", "POST"])
def login():
	"""
	Username and password login, followed by a TOTP code when the account has 2FA.

	Accounts are created from the command line (python manage.py createsuperuser),
	never from the web. Repeated failures for one username lock it for 15 minutes.
	"""
	if db_manager.count_users() == 0:
		return render_template("no_users.html")

	next_url = _safe_next(request.values.get("next"))

	if request.method == "GET":
		session.pop("pending_2fa_user_id", None)
		return render_template("login.html", show_2fa=False, username="", next_url=next_url or "")

	pending_id = session.get("pending_2fa_user_id")
	if pending_id:
		return _finish_two_factor(pending_id, next_url)

	username = (request.form.get("username") or "").strip()
	password = request.form.get("password") or ""
	if not username or not password:
		flash("Username and password are required!", "error")
		return render_template("login.html", show_2fa=False, username=username, next_url=next_url or "")

	if is_locked_out(username):
		flash("Too many failed attempts. Please wait 15 minutes and try again.", "error")
		return render_template("login.html", show_2fa=False, username=username, next_url=next_url or "")

	user = db_manager.get_user_by_username(username, include_secrets=True)
	if not user or not user["is_active"] or not verify_password(user["password_hash"], password):
		record_failure(username)
		flash("Invalid credentials!", "error")
		return render_template("login.html", show_2fa=False, username=username, next_url=next_url or "")

	clear_failures(username)
	if user["two_fa_enabled"] and user["two_fa_secret"]:
		session["pending_2fa_user_id"] = user["id"]
		return render_template("login.html", show_2fa=True, username=username, next_url=next_url or "")

	login_user(user["id"])
	flash("Login successful!", "success")
	return redirect(next_url or url_for("admin"))


def _finish_two_factor(user_id, next_url):
	"""Second login step: verify the TOTP code for the user who passed the password check."""
	user = db_manager.get_user(user_id, include_secrets=True)
	if not user or not user["is_active"] or not user["two_fa_secret"]:
		session.pop("pending_2fa_user_id", None)
		flash("Login session expired. Please log in again.", "error")
		return redirect(url_for("login"))

	if is_locked_out(user["username"]):
		flash("Too many failed attempts. Please wait 15 minutes and try again.", "error")
		return render_template("login.html", show_2fa=True, username=user["username"], next_url=next_url or "")

	code = (request.form.get("2fa_code") or "").strip()
	if code and pyotp.TOTP(user["two_fa_secret"]).verify(code, valid_window=1):
		clear_failures(user["username"])
		login_user(user["id"])
		flash("Login successful!", "success")
		return redirect(next_url or url_for("admin"))

	record_failure(user["username"])
	flash("Invalid 2FA code!", "error")
	return render_template("login.html", show_2fa=True, username=user["username"], next_url=next_url or "")


@app.route("/logout")
@login_required
def logout():
	"""End the session and return to the login page."""
	logout_user()
	flash("You have been logged out.", "info")
	return redirect(url_for("login"))


@app.route("/admin/profile", methods=["GET", "POST"])
@login_required
def profile():
	"""
	The signed-in user's own profile: display name, email, password and 2FA.
	Changing the password or disabling 2FA requires the current password.
	"""
	me = current_user()
	errors = []
	if request.method == "POST":
		action = request.form.get("action", "save")
		stored = db_manager.get_user(me["id"], include_secrets=True)

		if action == "disable_2fa":
			if not verify_password(stored["password_hash"], request.form.get("current_password") or ""):
				errors.append("Current password is incorrect.")
			else:
				db_manager.update_user(me["id"], two_fa_enabled=False, two_fa_secret=None)
				flash("Two-factor authentication has been disabled.", "success")
				return redirect(url_for("profile"))
		else:
			display_name = (request.form.get("display_name") or "").strip()[:150]
			email = (request.form.get("email") or "").strip()
			changes = {"display_name": display_name or None, "email": email}
			if not EMAIL_RE.match(email):
				errors.append("Enter a valid email address.")
			new_password = request.form.get("new_password") or ""
			if new_password:
				if not verify_password(stored["password_hash"], request.form.get("current_password") or ""):
					errors.append("Current password is incorrect.")
				elif new_password != request.form.get("confirm_password"):
					errors.append("New passwords do not match.")
				else:
					problem = validate_password(new_password, me["username"])
					if problem:
						errors.append(problem)
					else:
						changes["password_hash"] = hash_password(new_password)
			if not errors:
				try:
					db_manager.update_user(me["id"], **changes)
					flash("Profile updated.", "success")
					return redirect(url_for("profile"))
				except ValueError as e:
					errors.append(str(e))

	return render_template(
		"user_form.html",
		mode="profile",
		user=current_user(),
		form=None,
		errors=errors,
		roles=ROLES,
		role_labels=ROLE_LABELS,
	)


@app.context_processor
def inject_auth_context():
	return {"current_user": current_user(), "can": can}


@app.context_processor
def inject_globals():
	"""
	Variables every template can use: year, the active theme's helper functions,
	and the site settings (settings.site_title, settings.menu_items, ...).
	"""
	data = {"year": datetime.datetime.now().year}
	for name, func in theme_functions.items():
		data[name] = func
	data["settings"] = get_site_settings()
	data["nav_sections"] = list_sections()
	data["ext_admin_menus"] = extensions.admin_menus() if request and request.path.startswith(("/admin", "/page", "/post")) else []
	return data


@app.errorhandler(403)
def forbidden(e):
	return render_template("error.html", code=403, message="You do not have permission to do that."), 403


@app.before_request
def csrf_protect():
	"""
	Every state-changing request (POST, PUT, PATCH, DELETE) must carry the session's
	CSRF token, either as the X-CSRF-Token header (fetch/XHR) or a csrf_token form field.
	"""
	if request.method in ("GET", "HEAD", "OPTIONS") or request.endpoint in (None, "static"):
		return None
	expected = session.get("csrf_token")
	sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
	if not expected or not sent or not secrets.compare_digest(sent, expected):
		abort(403)
	return None


def _user_form_from_request():
	return {
		"username": (request.form.get("username") or "").strip(),
		"email": (request.form.get("email") or "").strip(),
		"display_name": (request.form.get("display_name") or "").strip()[:150],
		"role": request.form.get("role") or ROLE_CONTRIBUTOR,
		"is_active": request.form.get("is_active") == "on",
	}


@app.route("/admin/users")
@capability_required("manage_users")
def users_index():
	"""List all accounts."""
	return render_template(
		"users.html",
		users=db_manager.list_users(),
		role_labels=ROLE_LABELS,
		role_descriptions=ROLE_DESCRIPTIONS,
	)


@app.route("/admin/users/new", methods=["GET", "POST"])
@capability_required("manage_users")
def user_new():
	"""Create an account from the web UI (administrators only). The first account is made with the CLI."""
	errors = []
	form = {"username": "", "email": "", "display_name": "", "role": ROLE_CONTRIBUTOR, "is_active": True}
	if request.method == "POST":
		form = _user_form_from_request()
		password = request.form.get("password") or ""
		confirm = request.form.get("confirm_password") or ""
		if not USERNAME_RE.match(form["username"]):
			errors.append("Username must be 3-64 characters: letters, digits, '.', '_' or '-'.")
		if not EMAIL_RE.match(form["email"]):
			errors.append("Enter a valid email address.")
		if validate_role(form["role"]):
			errors.append(validate_role(form["role"]))
		if password != confirm:
			errors.append("Passwords do not match.")
		problem = validate_password(password, form["username"])
		if problem:
			errors.append(problem)
		if not errors:
			try:
				db_manager.create_user(
					username=form["username"],
					email=form["email"],
					password_hash=hash_password(password),
					role=form["role"],
					display_name=form["display_name"] or None,
				)
				flash(f"User '{form['username']}' created.", "success")
				return redirect(url_for("users_index"))
			except ValueError as e:
				errors.append(str(e))
	return render_template(
		"user_form.html", mode="create", user=None, form=form, errors=errors,
		roles=ROLES, role_labels=ROLE_LABELS, role_descriptions=ROLE_DESCRIPTIONS,
	)


@app.route("/admin/users/<int:user_id>", methods=["GET", "POST"])
@capability_required("manage_users")
def user_edit(user_id):
	"""Edit another account: role, status, email, display name, and optionally reset its password."""
	target = db_manager.get_user(user_id)
	if not target:
		abort(404)
	me = current_user()
	is_self = target["id"] == me["id"]
	errors = []
	form = dict(target)
	if request.method == "POST":
		posted = _user_form_from_request()
		form.update({"email": posted["email"], "display_name": posted["display_name"]})
		if is_self:
			posted["role"] = target["role"]
			posted["is_active"] = True
		form.update({"role": posted["role"], "is_active": posted["is_active"]})

		if not EMAIL_RE.match(posted["email"]):
			errors.append("Enter a valid email address.")
		if validate_role(posted["role"]):
			errors.append(validate_role(posted["role"]))
		demoting = target["role"] == ROLE_ADMINISTRATOR and (
			posted["role"] != ROLE_ADMINISTRATOR or not posted["is_active"]
		)
		if demoting and db_manager.count_active_administrators() <= 1:
			errors.append("This is the last active administrator. Make another administrator first.")

		changes = {
			"email": posted["email"],
			"display_name": posted["display_name"] or None,
			"role": posted["role"],
			"is_active": posted["is_active"],
		}
		new_password = request.form.get("new_password") or ""
		if new_password:
			if new_password != (request.form.get("confirm_password") or ""):
				errors.append("Passwords do not match.")
			else:
				problem = validate_password(new_password, target["username"])
				if problem:
					errors.append(problem)
				else:
					changes["password_hash"] = hash_password(new_password)

		if not errors:
			try:
				db_manager.update_user(user_id, **changes)
				flash("User updated.", "success")
				return redirect(url_for("user_edit", user_id=user_id))
			except ValueError as e:
				errors.append(str(e))

	return render_template(
		"user_form.html", mode="edit", user=target, form=form, errors=errors,
		roles=ROLES, role_labels=ROLE_LABELS, role_descriptions=ROLE_DESCRIPTIONS,
		is_self=is_self,
	)


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@capability_required("manage_users")
def user_delete(user_id):
	"""Delete an account. Its pages and posts are handed to the administrator doing the deletion."""
	me = current_user()
	target = db_manager.get_user(user_id)
	if not target:
		abort(404)
	if target["id"] == me["id"]:
		flash("You cannot delete your own account.", "error")
	elif target["role"] == ROLE_ADMINISTRATOR and db_manager.count_active_administrators() <= 1:
		flash("This is the last active administrator and cannot be deleted.", "error")
	elif db_manager.delete_user(user_id, reassign_to_id=me["id"]):
		flash(f"User '{target['username']}' deleted. Their content now belongs to you.", "success")
	else:
		flash("Could not delete that user.", "error")
	return redirect(url_for("users_index"))


@app.route("/admin/enable-2fa")
@login_required
def enable_2fa():
	"""Start (or restart) 2FA enrolment: create a pending secret, then show the QR code."""
	session["pending_2fa_secret"] = pyotp.random_base32()
	flash("Scan the QR code with your authenticator app, then enter the code to confirm.", "success")
	return redirect(url_for("setup_2fa"))


@app.route("/admin/2fa-setup", methods=["GET", "POST"])
@login_required
def setup_2fa():
	"""Confirm 2FA enrolment for the current user. Saved only after a correct code is entered."""
	me = current_user()
	secret = session.get("pending_2fa_secret")
	if not secret:
		return redirect(url_for("enable_2fa"))

	if request.method == "POST":
		if request.form.get("cancel_2fa") == "1":
			session.pop("pending_2fa_secret", None)
			flash("Two-factor authentication setup was canceled.", "info")
			return redirect(url_for("profile"))
		code = (request.form.get("verify_2fa_code") or "").strip()
		if pyotp.TOTP(secret).verify(code, valid_window=1):
			db_manager.update_user(me["id"], two_fa_secret=secret, two_fa_enabled=True)
			session.pop("pending_2fa_secret", None)
			flash("Two-factor authentication is now active.", "success")
			return redirect(url_for("profile"))
		flash("Invalid code. Please try again.", "danger")

	provisioning_uri = pyotp.TOTP(secret).provisioning_uri(
		name=me["email"], issuer_name="FlexaFlow CMS"
	)
	return render_template("setup_2fa.html", provisioning_uri=provisioning_uri, secret=secret)


@app.route("/admin")
@login_required
def admin():
	"""
	Admin dashboard view that displays all pages and posts.
	
	Shows both published and draft content. Requires user to be logged in.
	
	Returns:
		str: Rendered admin dashboard template with list of all pages and posts
	"""	
	all_pages = []
	pages = get_pages()
	draft_pages = get_draft_pages()

	for slug, page in pages.items():
		page_copy = page.copy()
		page_copy["slug"] = slug
		all_pages.append(page_copy)

	for slug, page in draft_pages.items():
		page_copy = page.copy()
		page_copy["slug"] = slug
		all_pages.append(page_copy)

	all_posts = []
	posts = get_posts()
	draft_posts = get_draft_posts()

	for slug, post in posts.items():
		post_copy = post.copy()
		post_copy["slug"] = slug
		all_posts.append(post_copy)

	for slug, post in draft_posts.items():
		post_copy = post.copy()
		post_copy["slug"] = slug
		all_posts.append(post_copy)

	me = current_user()
	if not can("edit_others_content"):
		all_posts = [p for p in all_posts if p.get("author_id") == me["id"]]
	if not can("manage_pages"):
		all_pages = []
	users_by_id = {u["id"]: u for u in db_manager.list_users()}
	for p in all_posts:
		p["public_url"] = post_url(p)
	all_posts.sort(key=lambda p: str(p.get("created_at") or ""), reverse=True)
	per_page = 10
	total_posts = len(all_posts)
	total_pages = max(1, (total_posts + per_page - 1) // per_page)
	try:
		post_page = int(request.args.get("ppage", 1))
	except (TypeError, ValueError):
		post_page = 1
	post_page = max(1, min(post_page, total_pages))
	post_rows = all_posts[(post_page - 1) * per_page: post_page * per_page]
	return render_template(
		"admin.html", pages=all_pages, posts=all_posts, post_rows=post_rows,
		post_page=post_page, post_pages=total_pages, post_per_page=per_page,
		users_by_id=users_by_id, role_labels=ROLE_LABELS,
	)


@app.route("/admin/settings")
@capability_required("manage_settings")
def settings():
	"""
	Manages site-wide settings and configuration.
	
	Features:
	- Site configuration management
	- Theme settings
	- 2FA status display
	- Custom analytics integration
	- Page selection for special roles
	
	Settings Categories:
	- General site settings
	- Theme configuration
	- Security settings (2FA)
	- Analytics integration
	- Content organization
	
	Context:
		settings: Current site configuration
		pages: Available pages for special roles
		custom_analytics: Analytics integration code
		two_fa_enabled: Current 2FA status
	
	Returns:
		str: Rendered settings template with:
			- Current configuration
			- Available options
			- Form for modifications
	
	Security:
		- Admin access required
		- Accurate 2FA status reflection
		- Secure settings storage
	"""	
	site_settings = get_site_settings()
	site_settings["two_fa_enabled"] = bool(current_user()["two_fa_enabled"])
	return render_template(
		"settings.html",
		settings=site_settings,
		pages=get_pages(),
		themes=available_themes(),
	)


@app.route("/admin/menu")
@capability_required("manage_settings")
def menu_editor():
	"""
	Provides interface for managing site navigation menu.
	
	Features:
	- Visual menu builder
	- Drag-and-drop ordering
	- Page selection integration
	- Custom link support
	- Nested menu structure
	
	Data Structure:
		menu_items: List of menu entries with:
			- title: Display text
			- url: Link target
			- order: Position in menu
			- children: Nested items
	
	Template Context:
		- menu_items: Current menu structure
		- pages: Available pages for selection
	
	Returns:
		str: Rendered menu editor template
	
	Integration:
		- Uses site settings for storage
		- Updates via AJAX API
		- Real-time preview
	
	Security:
		- Admin access required
		- URL validation
		- XSS prevention
	"""	
	settings = get_site_settings()
	menu_items = settings.get("menu_items", [])
	quick_links = [{"label": s["name"], "url": f"/{s['section_path']}/"} for s in list_sections()]
	for kind, cfg in content_store.ENTRY_KINDS.items():
		quick_links.append({"label": cfg["label"], "url": cfg["base_path"]})
		quick_links += [{"label": label, "url": f"{cfg['base_path']}group/{g}"} for g, label in cfg["groups"] if g != cfg["groups"][0][0] and g in cfg.get("public_groups", [])]
	return render_template("menu-editor.html", menu_items=menu_items, pages=get_pages(), quick_links=quick_links)


def _clean_menu(items, depth=0):
	"""Menu items as {label, url, children[]}: keeps only safe link targets, at most two levels deep."""
	cleaned = []
	for item in items if isinstance(items, list) else []:
		if not isinstance(item, dict):
			continue
		label = str(item.get("label") or "").strip()[:100]
		url = str(item.get("url") or "").strip()[:500]
		if url and not re.match(r"^(https?://|mailto:|tel:|/|#)", url):
			url = ""
		children = _clean_menu(item.get("children"), depth + 1) if depth == 0 else []
		if label and (url or children):
			entry = {"label": label, "url": url}
			if children:
				entry["children"] = children
			cleaned.append(entry)
	return cleaned


@app.route("/api/settings", methods=["GET", "POST"])
@capability_required("manage_settings")
def api_settings():
	"""
	RESTful API endpoint for managing site settings.
	
	Features:
	- Retrieves current settings
	- Updates site configuration
	- Handles theme settings
	- Manages site metadata
	- Validates configuration
	
	Methods:
		GET: Retrieve all site settings
		POST: Update site settings
	
	Request (POST):
		JSON payload with setting updates:
		- site_title: Site name
		- site_description: Meta description
		- theme: Theme selection
		- menu_items: Navigation structure
		- custom_settings: Theme-specific options
	
	Returns:
		GET: JSON of all current settings
		POST: 
			200: Success message
			500: Error message
	
	Security:
		- Admin authentication required
		- Input validation
		- Safe settings storage
	"""	
	if request.method == "GET":
		return jsonify(get_site_settings())
	elif request.method == "POST":
		data = request.json or {}
		if "theme" in data and data["theme"] not in available_themes():
			return jsonify({"status": "error", "message": "Unknown theme"}), 400
		success = update_site_settings(data)
		if success and "theme" in data and data["theme"] != THEME_NAME:
			apply_theme(data["theme"])
		if success:
			return jsonify({"status": "success", "message": "Settings updated"})
		else:
			return (
				jsonify({"status": "error", "message": "Failed to update settings"}),
				500,
			)


@app.route('/api/check_slug')
@login_required
def api_check_slug():
	"""Check if a slug is available for posts or pages and return a suggested slug if taken."""
	slug = request.args.get('slug', '').strip()
	obj_type = request.args.get('type', 'post')
	if not slug:
		return jsonify({'unique': False, 'suggested_slug': ''})

	def exists_in(collection):
		if not isinstance(collection, dict):
			return False
		return slug in collection

	taken = False
	if obj_type == 'post':
		taken = exists_in(get_posts()) or exists_in(get_draft_posts())
	else:
		taken = exists_in(get_pages()) or exists_in(get_draft_pages())

	suggested = slug
	if taken:
		base = re.sub(r"[^a-z0-9-]", '', slug.lower())
		suffix = 2
		while True:
			candidate = f"{base}-{suffix}"
			if obj_type == 'post':
				if not (candidate in get_posts() or candidate in get_draft_posts()):
					suggested = candidate
					break
			else:
				if not (candidate in get_pages() or candidate in get_draft_pages()):
					suggested = candidate
					break
			suffix += 1

	return jsonify({'unique': not taken, 'suggested_slug': suggested})


@app.route("/api/menu", methods=["GET", "POST"])
@capability_required("manage_settings")
def api_menu():
	"""
	RESTful API endpoint for managing site navigation menu.
	
	Features:
	- Retrieves current menu structure
	- Updates menu organization
	- Handles nested menu items
	- Maintains menu state
	- Validates menu structure
	
	Methods:
		GET: Retrieve current menu configuration
		POST: Update menu structure
	
	Returns:
		GET: JSON array of menu items
		POST: 
			200: Success message
			500: Error message
	
	Security:
		- Requires admin authentication
		- Validates menu structure
		- Safe settings storage
	"""	
	if request.method == "GET":
		settings = get_site_settings()
		return jsonify(settings.get("menu_items", []))
	elif request.method == "POST":
		data = _clean_menu(request.json)
		settings = get_site_settings()
		settings["menu_items"] = data
		success = update_site_settings(settings)
		if success:
			return jsonify({"status": "success", "message": "Menu updated"})
		else:
			return jsonify({"status": "error", "message": "Failed to update menu"}), 500


@app.route("/admin/category/add", methods=["POST"])
@capability_required("manage_taxonomy")
def add_category():
	"""
	Handles addition of new post categories.
	
	Features:
	- Validates category name
	- Auto-generates category slug
	- Prevents duplicate categories
	- Provides feedback messages
	- Database transaction handling
	
	Form Parameters:
		name (str): Category name to add
	
	Processing:
		1. Name validation
		2. Slug generation
		3. Duplicate check
		4. Database insertion
		5. Feedback generation
	
	Returns:
		302: Redirect to category management page with:
			- Success/error message via flash
			- Updated category list
	
	Security:
		- Requires admin login
		- Input validation
		- SQL injection prevention
	"""	
	name = request.form.get("name", "").strip()
	if not name:
		flash("Category name required!", "error")
		return redirect(url_for("manage_tags_and_catagories"))
	success = add_category_to_db(name)
	if success:
		flash("Category added successfully!", "success")
	else:
		flash("Error adding category! (Maybe already exists)", "error")
	return redirect(url_for("manage_tags_and_catagories"))


@app.route("/admin/media")
@capability_required("upload_media")
def media_library():
	"""
	Displays and manages the media library.
	
	Features:
	- Paged display of media items
	- Search functionality
	- JSON API support for AJAX requests
	- Displays image metadata
	- Shows thumbnails and image sizes
	
	Query Parameters:
		page (int): Page number for pagination (default: 1)
		search (str): Optional search term
		format (str): Response format ('json' for API)
		
	Returns:
		str/JSON: Rendered template or JSON response with:
			- Media items with metadata
			- Pagination information
			- Search results if applicable
			
	Security:
		Requires admin login
	"""	
	page = max(1, int(request.args.get("page", 1)))
	search = request.args.get("search")
	media = db_manager.get_media_library(page=page, search=search) or {
		"items": [], "total": 0, "page": 1, "total_pages": 1, "per_page": 10,
	}

	if request.args.get("format") == "json":
		return jsonify(media)

	return render_template("media-library.html", media=media)


@app.route("/admin/media/upload", methods=["POST"])
@capability_required("upload_media")
def upload_media():
	"""
	Handles bulk media file uploads for the media library.
	
	Features:
	- Multiple file upload support
	- Automatic thumbnail generation
	- Metadata extraction
	- Database integration
	- Error handling per file
	
	Request:
		files[]: Array of file uploads
		
	Processing:
	1. Validates each file
	2. Generates unique filenames
	3. Creates thumbnails
	4. Extracts metadata
	5. Stores in database
	
	Returns:
		JSON: {
			success: bool,
			files: [{
				success: bool,
				file: {metadata} | error: str
			}]
		}
		
	Security:
		- Admin authentication required
		- File type validation
		- Secure file naming
		- Error isolation
	"""	
	if "files[]" not in request.files:
		return jsonify({"success": False, "error": "No file part"})

	files = request.files.getlist("files[]")
	results = []

	for file in files:
		if file.filename == "":
			continue

		if file:
			try:
				original_filename = secure_filename(file.filename)
				filename = str(uuid.uuid4()) + os.path.splitext(original_filename)[1]
				file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)

				file.save(file_path)

				thumbnails = create_image_thumbnails(file_path, filename)

				with Image.open(file_path) as img:
					width, height = img.size

				file_size = os.path.getsize(file_path)

				media_data = {
					"filename": filename,
					"original_filename": original_filename,
					"mime_type": file.content_type,
					"file_size": file_size,
					"width": width,
					"height": height,
					"alt_text": os.path.splitext(original_filename)[0],
					"caption": "",
					"title": os.path.splitext(original_filename)[0],
					"description": "",
					"thumbnail": thumbnails.get("thumbnail"),
				}

				result = db_manager.add_media(media_data)
				if result:
					result["thumbnails"] = thumbnails
					results.append({"success": True, "file": result})
				else:
					results.append(
						{
							"success": False,
							"error": f"Failed to save {original_filename} to database",
						}
					)

			except Exception as e:
				results.append(
					{
						"success": False,
						"error": f"Error processing {file.filename}: {str(e)}",
					}
				)

	return jsonify({"success": True, "files": results})


@app.route("/admin/export/xml", methods=["GET"])
@capability_required("manage_settings")
def export_content():
	"""
	Exports all site content as XML for backup or migration.
	
	Features:
	- Exports all posts and pages
	- Preserves metadata and relationships
	- Maintains categories and tags
	- Generates unique filenames
	- Uses standardized XML format
	
	Export Format:
		- Root element with version and timestamp
		- Pages section with all pages
		- Posts section with all posts
		- Preserved metadata for each item
		- Maintained relationships
	
	Filename Format:
		flexaflow_[version]_[site-name]_[timestamp].xml
	
	Returns:
		FileResponse: XML file containing:
			- All pages with metadata
			- All posts with metadata
			- Categories and tags
			- Export metadata
	
	Security:
		- Admin access required
		- Safe file generation
		- Proper content encoding
	"""

	FLEXAFLOW_VERSION = "1.0.0"
	export_time = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
	site_settings = get_site_settings()
	site_title = site_settings.get("site_title", "site").strip().lower().replace(" ", "_")
	timestamp = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
	filename = f"flexaflow_{FLEXAFLOW_VERSION}_export_{site_title}_{timestamp}.xml"

	root = ET.Element("flexaflow_export", version="1.0", exported_at=export_time)

	pages_elem = ET.SubElement(root, "pages")
	for slug, page in get_pages().items():
		page_elem = ET.SubElement(pages_elem, "page", slug=slug)
		for k, v in page.items():
			child = ET.SubElement(page_elem, k)
			child.text = str(v) if v is not None else ""

	posts_elem = ET.SubElement(root, "posts")
	for slug, post in get_posts().items():
		post_elem = ET.SubElement(posts_elem, "post", slug=slug)
		for k, v in post.items():
			if isinstance(v, list):
				list_elem = ET.SubElement(post_elem, k)
				for item in v:
					item_elem = ET.SubElement(list_elem, "item")
					item_elem.text = str(item)
			else:
				child = ET.SubElement(post_elem, k)
				child.text = str(v) if v is not None else ""

	xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
	return send_file(
		io.BytesIO(xml_bytes),
		mimetype="application/xml",
		as_attachment=True,
		download_name=filename,
	)


@app.route("/admin/import/xml", methods=["GET", "POST"])
@capability_required("manage_settings")
def import_content():
	"""
	Handles import of content from XML file.
	
	Features:
	- Imports both posts and pages
	- Validates XML format
	- Skips duplicate content based on slugs
	- Maintains relationships (categories, tags)
	- Updates tag counts after import
	
	Args:
		file: Uploaded XML file through request.files
		
	Returns:
		GET: Rendered import form template
		POST: Redirect to admin with status message
		
	Raises:
		Flash error messages for:
		- Invalid file type
		- XML parsing errors
		- Database operation failures
	"""
	if request.method == "POST":
		file = request.files.get("file")
		if not file or not file.filename.endswith(".xml"):
			flash("Please upload a valid XML file.", "error")
			return redirect(url_for("import_content"))
		try:
			tree = ET.parse(file)
			root = tree.getroot()
			existing_page_slugs = set(get_pages().keys())
			existing_post_slugs = set(get_posts().keys())
			skipped_pages = []
			skipped_posts = []
			imported_pages = 0
			imported_posts = 0
			pages_elem = root.find("pages")
			if pages_elem is not None:
				for page_elem in pages_elem.findall("page"):
					slug = page_elem.attrib.get("slug")
					if not slug or slug in existing_page_slugs:
						skipped_pages.append(slug)
						continue
					page_data = {child.tag: child.text for child in page_elem}
					add_page(slug, page_data)
					imported_pages += 1
			posts_elem = root.find("posts")
			if posts_elem is not None:
				for post_elem in posts_elem.findall("post"):
					slug = post_elem.attrib.get("slug")
					if not slug or slug in existing_post_slugs:
						skipped_posts.append(slug)
						continue
					post_data = {}
					for child in post_elem:
						if child.tag in ("tags",):
							post_data[child.tag] = [item.text for item in child.findall("item")]
						else:
							post_data[child.tag] = child.text
					add_post(slug, post_data)
					imported_posts += 1
			update_tag_counts()
			msg = f"Import successful! Imported {imported_pages} pages and {imported_posts} posts."
			if skipped_pages or skipped_posts:
				msg += f" Skipped {len(skipped_pages)} pages and {len(skipped_posts)} posts due to duplicate slugs."
			flash(msg, "success")
		except Exception as e:
			flash(f"Import failed: {e}", "error")
		return redirect(url_for("admin"))
	return render_template("import.html")


@app.route("/admin/media/<int:media_id>", methods=["GET", "PUT", "DELETE"])
@capability_required("manage_media")
def manage_media(media_id):
	"""
	RESTful endpoint for managing individual media items.
	
	Features:
	- Media item retrieval
	- Metadata updates
	- Media deletion
	- Thumbnail management
	- File system synchronization
	
	Methods:
		GET: Retrieve media item details
		PUT: Update media metadata
		DELETE: Remove media item
	
	URL Parameters:
		media_id (int): Database ID of media item
	
	Returns:
		GET: JSON media item data
		PUT: JSON success status
		DELETE: JSON success status
	
	Status Codes:
		200: Success
		404: Media not found
		500: Operation failed
	
	Security:
		- Admin access required
		- File system safety checks
		- Database transaction safety
	"""	
	if request.method == "GET":
		media = db_manager.get_media_by_id(media_id)
		if media:
			return jsonify(media)
		return jsonify({"error": "Media not found"}), 404

	elif request.method == "PUT":
		data = request.json
		success = db_manager.update_media(media_id, data)
		if success:
			return jsonify({"success": True})
		return jsonify({"success": False, "error": "Update failed"}), 500

	elif request.method == "DELETE":
		success = db_manager.delete_media(media_id)
		if success:
			return jsonify({"success": True})
		return jsonify({"success": False, "error": "Delete failed"}), 500


@app.route("/admin/tags-and-catagories", methods=["GET", "POST"])
@capability_required("manage_taxonomy")
def manage_tags_and_catagories():
	"""
	Manages post categories and tags.
	
	Features:
	- Lists all categories and tags
	- Shows usage count for each
	- Identifies unused categories/tags
	- Supports deletion of unused items
	- Prevents deletion of items in use
	
	Methods:
		GET: Display management interface
		POST: Handle category/tag modifications
		
	Returns:
		str: Rendered template with:
			- All categories and their counts
			- All tags and their counts
			- Lists of unused items
			
	Security:
		- Requires admin login
		- Prevents deletion of used items
	"""	
	categories = get_categories() or {}
	tags = get_tags() or {}
	unused_categories = {k: v for k, v in categories.items() if v.get("count", 0) == 0}
	unused_tags = {k: v for k, v in tags.items() if v.get("count", 0) == 0}
	return render_template(
		"tags_and_catagories.html",
		categories=categories,
		tags=tags,
		unused_categories=unused_categories,
		unused_tags=unused_tags,
	)


@app.route("/admin/category/delete/<slug>", methods=["POST"])
@capability_required("manage_taxonomy")
def delete_category(slug):
	"""
	Handles deletion of unused categories.
	
	Features:
	- Validates category exists
	- Prevents deletion of used categories
	- Database transaction handling
	- User feedback
	- Referential integrity
	
	Args:
		slug (str): URL slug of category to delete
		
	Returns:
		302: Redirect to category management with:
			- Success message if deleted
			- Error if category in use/not found
			
	Security:
		- Admin authentication required
		- Usage validation
		- Transaction safety
	"""	
	categories = get_categories() or {}
	if slug in categories and categories[slug].get("post_count", 0) == 0:
		db = db_manager.get_session()
		try:
			category = db.query(Category).filter(Category.slug == slug).first()
			if category:
				db.delete(category)
				db.commit()
				flash("Category deleted.", "success")
			else:
				flash("Category not found.", "error")
		except Exception as e:
			db.rollback()
			flash(f"Error deleting category: {e}", "error")
		finally:
			db.close()
	else:
		flash("Cannot delete category in use.", "error")
	return redirect(url_for("manage_tags_and_catagories"))


@app.route("/admin/tag/add", methods=["POST"])
@capability_required("manage_taxonomy")
def add_tag():
	"""
	Handles creation of new post tags.
	
	Features:
	- Tag name validation
	- Duplicate prevention
	- Database transaction handling
	- Error feedback
	- Automatic count initialization
	
	Form Parameters:
		name (str): Name of the new tag
	
	Processing:
	1. Name validation and sanitization
	2. Duplicate check
	3. Tag creation
	4. Transaction management
	5. User feedback
	
	Returns:
		302: Redirect to tag management with:
			- Success/error message
			- Updated tag list
			
	Security:
		- Admin access required
		- Input validation
		- Transaction safety
	"""

	name = request.form.get("name", "").strip()
	if not name:
		flash("Tag name required.", "error")
		return redirect(url_for("manage_tags_and_catagories"))
	db = db_manager.get_session()
	try:
		existing = db.query(Tag).filter(Tag.name == name).first()
		if existing:
			flash("Tag already exists.", "error")
		else:
			tag = Tag(name=name)
			db.add(tag)
			db.commit()
			flash("Tag added.", "success")
	except Exception as e:
		db.rollback()
		flash(f"Error adding tag: {e}", "error")
	finally:
		db.close()
	return redirect(url_for("manage_tags_and_catagories"))


@app.route("/admin/tag/delete/<int:tag_id>", methods=["POST"])
@capability_required("manage_taxonomy")
def delete_tag(tag_id):
	"""
	Handles deletion of unused tags.
	
	Features:
	- Safe tag removal
	- Usage validation
	- Database cleanup
	- Referential integrity
	- User feedback
	
	Processing Steps:
	1. Verify tag exists
	2. Check usage count
	3. Remove if unused
	4. Update post relationships
	5. Clean up database
	
	URL Parameters:
		tag_id (int): Database ID of tag to delete
	
	Returns:
		302: Redirect to tag management with:
			- Success message if deleted
			- Error if tag in use/not found
	
	Security:
		- Admin authentication
		- Usage validation
		- Transaction safety
		- Referential integrity
	"""

	db = db_manager.get_session()
	try:
		tag = db.query(Tag).filter(Tag.id == tag_id).first()
		if tag and tag.count == 0:
			db.delete(tag)
			db.commit()
			flash("Tag deleted.", "success")
		else:
			flash("Cannot delete tag in use or not found.", "error")
	except Exception as e:
		db.rollback()
		flash(f"Error deleting tag: {e}", "error")
	finally:
		db.close()
	return redirect(url_for("manage_tags_and_catagories"))


@app.route("/")
def home():
	"""
	Home page view that displays published posts with pagination.
	
	Features:
	- Displays most recent published posts
	- Paginates posts (10 per page)
	- Shows tag cloud and categories
	- Redirects to setup if system is not configured
	
	Returns:
		str: Rendered home page template with posts and metadata
		
	Raises:
		Redirects to setup page if system is not configured
	"""

	if not db_manager.is_setup_complete():
		return render_template("no_users.html")

	homepage_slug = str(get_site_settings().get("homepage") or "").strip()
	if homepage_slug:
		home_page = get_page_by_slug(homepage_slug)
		if home_page and home_page.get("status") == "published":
			return view_page(homepage_slug)

	try:
		page = max(1, int(request.args.get("page", 1)))
		posts_per_page = 10

		published_posts = []
		posts = get_posts()
		

		if not isinstance(posts, dict):
			posts = {}

		for slug, post in posts.items():
			if post.get("status") == "published":
				post_copy = post.copy()
				post_copy["slug"] = slug
				published_posts.append(post_copy)

		published_posts.sort(
			key=lambda x: x.get("created_at", x.get("updated_at", "")), reverse=True
		)

		total_posts = len(published_posts)
		total_pages = max(1, (total_posts + posts_per_page - 1) // posts_per_page)

		page = min(page, total_pages)

		start = (page - 1) * posts_per_page
		end = start + posts_per_page
		current_posts = published_posts[start:end]

		pagination = {
			"current_page": page,
			"total_pages": total_pages,
			"pages": range(1, total_pages + 1),
			"prev_page": page - 1 if page > 1 else None,
			"next_page": page + 1 if page < total_pages else None,
		}

		tags = get_tags()
		if not isinstance(tags, dict):
			tags = {}
		max_tag_count = (
			max([tag.get("count", 0) for tag in tags.values()]) if tags else 1
		)

		categories = get_categories()
		if not isinstance(categories, dict):
			categories = {}

		default_page = {
			"title": "Home",
			"description": get_site_settings().get(
				"site_description", "Welcome to our website"
			),
		}


		return render_template(
			"main/master.html",
			page=default_page,
			posts=current_posts,
			pagination=pagination,
			categories=categories,
			tags=tags,
			max_tag_count=max_tag_count,
			error=None,
		)

	except Exception as e:
		default_page = {
			"title": "Home",
			"description": get_site_settings().get(
				"site_description", "Welcome to our website"
			),
		}

		return render_template(
			"main/master.html",
			page=default_page,
			posts=[],
			pagination={
				"current_page": 1,
				"total_pages": 1,
				"pages": [1],
				"prev_page": None,
				"next_page": None,
			},
			categories={},
			tags={},
			max_tag_count=1,
			error=str(e),
		)


@app.route("/post/<slug>")
def view_post(slug):
	"""
	Displays a single blog post and its related content.
	
	Features:
	- Shows full post content
	- Displays related posts based on tags
	- Shows categories and tags
	- Handles both published and draft posts
	- Limits related posts to 3 items
	
	Args:
		slug (str): URL slug of the post to display
		
	Returns:
		str: Rendered post template with post data and related content
		
	Raises:
		404: If post is not found
	"""	
	post = get_post_by_slug(slug)

	if not post:
		abort(404)

	if post.get("status") == "published" and (post.get("category") or {}).get("section_path"):
		return redirect(post_url(post), code=301)

	if post.get("status") != "published":
		pass

	categories = get_categories()
	tags = get_tags()

	related_posts = []
	post_tags = post.get("tags", [])
	all_posts = get_posts()

	for other_slug, other_post in all_posts.items():
		if other_slug != slug and other_post.get("status") == "published":
			other_tags = other_post.get("tags", [])
			if set(post_tags) & set(other_tags):
				other_post_copy = other_post.copy()
				other_post_copy["slug"] = other_slug
				related_posts.append(other_post_copy)
				if len(related_posts) >= 3:
					break

	post["slug"] = slug

	return render_template(
		"post.html",
		post=post,
		related_posts=related_posts,
		categories=categories,
		tags=tags,
	)


PUBLIC_PER_PAGE = 10


def paginate_list(items, per_page=PUBLIC_PER_PAGE):
	"""Slice ``items`` for the current ?page= and return (slice, pager) for theme templates."""
	try:
		page = int(request.args.get("page", 1))
	except (TypeError, ValueError):
		page = 1
	total_pages = max(1, (len(items) + per_page - 1) // per_page)
	page = max(1, min(page, total_pages))
	pager = {
		"current_page": page,
		"total_pages": total_pages,
		"pages": list(range(1, total_pages + 1)),
		"prev_page": page - 1 if page > 1 else None,
		"next_page": page + 1 if page < total_pages else None,
		"query": urlencode({k: v for k, v in request.args.items() if k != "page" and v}),
	}
	return items[(page - 1) * per_page: page * per_page], pager


@app.route("/category/<category_slug>")
def view_category(category_slug):
	"""
	Displays posts from a specific category.
	
	Features:
	- Lists all posts in category
	- Shows category metadata
	- Filters by published status
	- Updates post counts
	- 404 handling for invalid categories
	
	URL Parameters:
		category_slug (str): URL slug of the category
	
	Template Context:
		- category: Category metadata
		- posts: List of posts in category
		- Each post includes:
			- Title, content, metadata
			- Publication date
			- Tags
			- Author info
	
	Returns:
		str: Rendered category template
		404: If category not found
	
	Notes:
		- Only shows published posts
		- Updates category post count
	"""


	categories = get_categories()
	if category_slug in categories and categories[category_slug].get("section_path"):
		return redirect(f"/{categories[category_slug]['section_path']}/", code=301)
	if category_slug in categories:
		category_posts = []
		all_posts = get_posts()

		for slug, post in all_posts.items():
			if (
				(post.get("category") or {}).get("slug") == category_slug
				and post.get("status") == "published"
			):
				post_copy = post.copy()
				post_copy["slug"] = slug
				category_posts.append(post_copy)


		category_posts.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
		category_data = categories[category_slug]
		category_data["count"] = len(category_posts)
		page_posts, pager = paginate_list(category_posts)

		return render_template(
			"category.html", category=category_data, posts=page_posts, pager=pager
		)
	else:
		abort(404)


@app.route("/tag/<tag_name>")
def view_tag(tag_name):
	"""
	Displays all posts with a specific tag.
	
	Features:
	- Lists all posts with tag
	- Shows tag statistics
	- Filters by published status
	- Orders by publication date
	
	URL Parameters:
		tag_name (str): Name of the tag to filter by
	
	Template Context:
		tag: Tag information including:
			- name: Tag name
			- count: Number of posts
		posts: List of tagged posts containing:
			- Full post data
			- Publication metadata
			- Related tags
			- Category info
	
	Returns:
		str: Rendered tag template with:
			- Tag information
			- Filtered post list
			- Related metadata
	
	Notes:
		- Only shows published posts
		- Maintains tag counts
		- Supports pagination if needed
	"""

	tagged_posts = []
	all_posts = get_posts()

	for slug, post in all_posts.items():
		if tag_name in post.get("tags", []) and post.get("status") == "published":
			post_copy = post.copy()
			post_copy["slug"] = slug
			tagged_posts.append(post_copy)

	tagged_posts.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
	page_posts, pager = paginate_list(tagged_posts)
	return render_template(
		"tag.html",
		tag={"name": tag_name, "count": len(tagged_posts)},
		posts=page_posts,
		pager=pager,
	)


@app.route("/search")
def search():
	"""
	Performs a full-text search across posts.
	
	Features:
	- Searches in title, content, excerpt, and tags
	- Case-insensitive search
	- Only returns published posts
	- Supports empty search query
	
	Query Parameters:
		q (str): Search query string
		
	Returns:
		str: Rendered search results template with:
			- Search query
			- List of matching posts
			- Each post includes title, excerpt, and metadata
	"""

	query = request.args.get("q", "")
	search_results = []

	if query:
		all_posts = get_posts()
		for slug, post in all_posts.items():
			if post.get("status") == "published":
				if (
					query.lower() in post.get("title", "").lower()
					or query.lower() in post.get("content", "").lower()
					or query.lower() in post.get("excerpt", "").lower()
					or query.lower() in ",".join(post.get("tags", [])).lower()
				):
					post_copy = post.copy()
					post_copy["slug"] = slug
					search_results.append(post_copy)

	total_results = len(search_results)
	search_results, pager = paginate_list(search_results)
	return render_template(
		"search.html", query=query, results=search_results, total_results=total_results, pager=pager
	)


def _resolve_template(key):
	"""Return a known page-template key, falling back to the plain page."""
	return key if key in PAGE_TEMPLATES else "default"


@app.route("/page/add", methods=["GET", "POST"])
@capability_required("manage_pages")
def add_page_route():
	"""
	Create a page. The page template chosen on the form decides which custom
	fields are shown and stored (see page_templates.py).
	"""
	errors = []
	if request.method == "POST":
		template_key = _resolve_template(request.form.get("template") or "default")
		template = PAGE_TEMPLATES[template_key]
		title = (request.form.get("title") or "").strip()
		slug = slugify(request.form.get("slug") or title)
		description = (request.form.get("description") or "").strip()
		content = request.form.get("content") or ""
		status = "published" if request.form.get("status") == "published" else "draft"

		if not title:
			errors.append("A page title is required.")
		if not slug:
			errors.append("A permalink (slug) is required.")
		elif slug in RESERVED_SLUGS or get_section_by_path(slug) or content_store.directory_for_path(slug)[0]:
			errors.append(f"'{slug}' is reserved. Choose another permalink.")
		elif get_page_by_slug(slug):
			errors.append(f"A page already exists at '/{slug}'.")

		fields = parse_fields(template["fields"], request.form)
		if request.form.get("action") == "switch_template":
			return render_template(
				"page_form.html", mode="add", slug=slug, errors=[],
				page={"title": title, "description": description, "content": content,
				      "status": status, "template": template_key},
				templates=PAGE_TEMPLATES, template_key=template_key,
				values=fill_defaults(fields, template["fields"]),
			)
		if not errors:
			saved = add_page(slug, {
				"title": title,
				"content": content,
				"description": description,
				"status": status,
				"template": template_key,
				"fields": fields,
				"author_id": current_user()["id"],
			})
			if saved:
				generate_and_store_sitemap()
				flash("Page created.", "success")
				return redirect(url_for("edit_page", slug=slug))
			errors.append("The page could not be saved. Please try again.")

		page = {"title": title, "description": description, "content": content,
		        "status": status, "template": template_key}
		return render_template(
			"page_form.html", mode="add", page=page, slug=slug, errors=errors,
			templates=PAGE_TEMPLATES, template_key=template_key,
			values=fill_defaults(fields, template["fields"]),
		)

	template_key = _resolve_template(request.args.get("template") or "default")
	return render_template(
		"page_form.html", mode="add", page={"title": "", "description": "", "content": "",
		                                    "status": "draft", "template": template_key},
		slug="", errors=errors, templates=PAGE_TEMPLATES, template_key=template_key,
		values=default_values(PAGE_TEMPLATES[template_key]["fields"]),
	)


@app.route("/page/edit/<slug>", methods=["GET", "POST"])
@capability_required("manage_pages")
def edit_page(slug):
	"""Edit a page's content, permalink, status, template and custom fields."""
	page = get_page_by_slug(slug)
	if not page:
		return "<h1>Page not found</h1>", 404

	errors = []
	if request.method == "POST":
		template_key = _resolve_template(request.form.get("template") or "default")
		template = PAGE_TEMPLATES[template_key]
		title = (request.form.get("title") or "").strip()
		new_slug = slugify(request.form.get("slug") or title) or slug
		description = (request.form.get("description") or "").strip()
		content = request.form.get("content") or ""
		status = "published" if request.form.get("status") == "published" else "draft"

		if not title:
			errors.append("A page title is required.")
		if new_slug != slug:
			if new_slug in RESERVED_SLUGS or get_section_by_path(new_slug) or content_store.directory_for_path(new_slug)[0]:
				errors.append(f"'{new_slug}' is reserved. Choose another permalink.")
			elif get_page_by_slug(new_slug):
				errors.append(f"A page already exists at '/{new_slug}'.")

		fields = parse_fields(template["fields"], request.form)
		if request.form.get("action") == "switch_template":
			return render_template(
				"page_form.html", mode="edit", slug=slug, errors=[],
				page=dict(page, title=title, description=description, content=content,
				          status=status, template=template_key),
				templates=PAGE_TEMPLATES, template_key=template_key,
				values=fill_defaults(fields, template["fields"]),
			)
		if not errors:
			saved = update_page_by_slug(slug, {
				"title": title,
				"content": content,
				"description": description,
				"status": status,
				"new_slug": new_slug,
				"template": template_key,
				"fields": fields,
			})
			if saved:
				generate_and_store_sitemap()
				flash("Page saved.", "success")
				return redirect(url_for("edit_page", slug=new_slug))
			errors.append("The page could not be saved. Please try again.")

		page = dict(page, title=title, description=description, content=content,
		            status=status, template=template_key)
		return render_template(
			"page_form.html", mode="edit", page=page, slug=slug, errors=errors,
			templates=PAGE_TEMPLATES, template_key=template_key,
			values=fill_defaults(fields, template["fields"]),
		)

	template_key = _resolve_template(page.get("template"))
	template = PAGE_TEMPLATES[template_key]
	return render_template(
		"page_form.html", mode="edit", page=page, slug=slug, errors=errors,
		templates=PAGE_TEMPLATES, template_key=template_key,
		values=fill_defaults(page.get("fields") or {}, template["fields"]),
	)


@app.route("/page/delete/<slug>", methods=["POST"])
@capability_required("manage_pages")
def delete_page_route(slug):
	"""Delete a page. POST only, so a link or image on another site cannot delete content."""
	if delete_page(slug):
		generate_and_store_sitemap()
		flash("Page deleted.", "success")
	else:
		flash("Error deleting page.", "error")
	return redirect(url_for("admin"))


@app.route("/page/preview/<slug>")
@capability_required("manage_pages")
def preview_page(slug):
	"""
	Provides a preview of a page before publishing.
	
	Features:
	- Live content preview
	- Theme integration
	- Metadata display
	- Draft preview support
	- Custom styling options
	
	URL Parameters:
		slug (str): Page identifier to preview
	
	Template Context:
		page: Complete page data
		theme: Theme configuration including:
			- Custom CSS settings
			- Layout classes
			- Dark mode status
	
	Returns:
		str: Rendered preview template
		404: If page not found
	
	Security:
		- Requires admin login
		- Safe content rendering
		- No permanent changes
	"""


	page = get_page_by_slug(slug)

	if page:
		theme = {
			"use_custom_css": False,
			"custom_css_url": "",
			"custom_styles": "",
			"body_class": "",
			"content_class": "",
			"dark_mode": False,
		}
		template_key = _resolve_template(page.get("template"))
		if template_key != DEFAULT_TEMPLATE_KEY:
			template = PAGE_TEMPLATES[template_key]
			values = fill_defaults(page.get("fields") or {}, template["fields"])
			return render_template(template["template_file"], page=page, f=values)
		return render_template("preview.html", page=page, theme=theme)
	else:
		return "<h1>Page not found</h1>", 404


@app.route("/page/preview_draft", methods=["POST"])
@login_required
def preview_draft():
	"""
	Previews a draft post/page before saving.
	
	Features:
	- Shows how content will look when published
	- Uses actual theme templates
	- Displays all content sections
	- No database interaction required
	
	Form Parameters:
		title (str): Draft title
		description (str): Draft description/excerpt
		content (str): Draft content
		
	Returns:
		str: Rendered preview using actual theme template
		
	Security:
		- No content is saved to database
		- Preview is temporary
	"""

	title = request.form.get("title")
	description = request.form.get("description")
	content = request.form.get("content")
	page = {"title": title, "description": description, "content": content}
	return render_template("preview.html", page=page)


def _can_edit_post(post, user):
	"""Editors may edit any post. Others may edit their own posts, subject to their role."""
	if can("edit_others_content", user):
		return True
	if post.get("author_id") != user["id"] or not can("create_posts", user):
		return False
	return post.get("status") != "published" or can("publish_posts", user)


def _post_status_for(requested: str) -> str:
	"""Contributors cannot publish; their submissions stay as drafts."""
	if requested == "published" and can("publish_posts"):
		return "published"
	return "draft"


def _post_extras(category_slug):
	"""Section fields, cover image and date from the post form: (meta, cover_image, published_at)."""
	section = get_section_by_slug(category_slug)
	meta = parse_fields(section["fields_schema"], request.form) if section else {}
	cover = parse_fields([{"name": "cover", "type": "image"}], {"field[cover]": request.form.get("cover_image", "")}).get("cover", "")
	published_at = content_store.parse_post_date(request.form.get("pdatetime") or request.form.get("pdate") or "")
	return meta, cover, published_at


@app.route("/post/add", methods=["GET", "POST"])
@capability_required("create_posts")
def add_post_route():
	"""Create a post. Contributors' posts are saved as drafts regardless of the status chosen."""
	errors = []
	form = {"slug": "", "title": "", "description": "", "content": "",
	        "category": "", "tags": "", "status": "draft", "cover_image": "",
	        "pdate": "", "pdatetime": ""}
	meta_values = {}
	if request.method == "GET":
		wanted = get_section_by_path(request.args.get("section", "")) or get_section_by_slug(request.args.get("category", ""))
		if wanted:
			form["category"] = wanted["slug"]
	if request.method == "POST":
		form = {key: (request.form.get(key) or "") for key in form}
		title = form["title"].strip()
		content = form["content"].strip()
		slug = slugify(form["slug"].strip() or title)
		if not title or not content:
			errors.append("Title and content are required.")
		if not slug:
			errors.append("A permalink (slug) is required.")
		elif get_post_by_slug(slug):
			errors.append(f"A post with slug '{slug}' already exists.")

		meta_values, cover_image, published_at = _post_extras(form["category"].strip())
		if not errors:
			tags = [t.strip() for t in form["tags"].split(",") if t.strip()]
			status = _post_status_for(form["status"])
			saved = add_post(slug, {
				"content": content,
				"title": title,
				"excerpt": form["description"].strip() or title[:200],
				"status": status,
				"category": form["category"].strip(),
				"tags": tags,
				"author_id": current_user()["id"],
				"cover_image": cover_image,
				"meta": meta_values,
				"published_at": published_at,
			})
			if saved:
				update_tag_counts()
				generate_and_store_sitemap()
				section = get_section_by_slug(form["category"].strip())
				noun = (section["singular_name"] if section else "Post")
				flash(f"{noun} created." + ("" if status == "published" else " It was saved as a draft."), "success")
				return redirect(url_for("admin"))
			errors.append("The post could not be saved. Please try again.")

	return render_template(
		"post_form.html", mode="add", post=None, slug="", form=form, errors=errors,
		categories=get_categories() or {}, can_publish=can("publish_posts"),
		meta_values=meta_values,
	)


@app.route("/post/edit/<slug>", methods=["GET", "POST"])
@login_required
def edit_post(slug):
	"""Edit a post, enforcing ownership and status rules for the current role."""
	post = get_post_by_slug(slug)
	if not post:
		return "<h1>Post not found</h1>", 404
	me = current_user()
	if not _can_edit_post(post, me):
		abort(403)

	errors = []
	if request.method == "POST":
		title = (request.form.get("title") or "").strip()
		content = request.form.get("content") or ""
		if not title or not content.strip():
			errors.append("Title and content are required.")
		if not errors:
			tags = [t.strip() for t in (request.form.get("tags") or "").split(",") if t.strip()]
			meta_values, cover_image, published_at = _post_extras((request.form.get("category") or "").strip())
			success = update_post_by_slug(slug, {
				"content": content,
				"title": title,
				"excerpt": (request.form.get("description") or "").strip(),
				"status": _post_status_for(request.form.get("status", "draft")),
				"category": request.form.get("category") or "",
				"tags": tags,
				"cover_image": cover_image,
				"meta": meta_values,
				"published_at": published_at,
			})
			if success:
				update_tag_counts()
				generate_and_store_sitemap()
				flash("Post saved.", "success")
				return redirect(url_for("edit_post", slug=slug))
			errors.append("The post could not be saved. Please try again.")
		post = dict(post, title=title, content=content,
		            excerpt=request.form.get("description") or "",
		            category=request.form.get("category") or "")

	post["tags_string"] = ", ".join(post.get("tags") or []) if isinstance(post.get("tags"), list) else ""
	return render_template(
		"post_form.html", mode="edit", post=post, slug=slug, form=None, errors=errors,
		categories=get_categories() or {}, can_publish=can("publish_posts"),
		meta_values=post.get("meta") or {},
	)


@app.route("/post/delete/<slug>", methods=["POST"])
@login_required
def delete_post_route(slug):
	"""Delete a post (POST only). Contributors and authors can delete only their own posts."""
	post = get_post_by_slug(slug)
	if not post:
		flash("Post not found.", "error")
		return redirect(url_for("admin"))
	me = current_user()
	if not can("edit_others_content") and post.get("author_id") != me["id"]:
		abort(403)
	if delete_post_by_slug(slug):
		update_tag_counts()
		generate_and_store_sitemap()
		flash("Post deleted.", "success")
	else:
		flash("Error deleting post.", "error")
	return redirect(url_for("admin"))


@app.route("/<slug>")
def view_page(slug):
	"""
	Public page. The page's template decides the layout: plain pages use the theme's
	master template; templates with custom fields receive them as ``f``.
	"""
	if get_section_by_path(slug):
		return section_index_view(slug)
	page = get_page_by_slug(slug)
	if not page or page.get("status") != "published":
		abort(404)
	template_key = _resolve_template(page.get("template"))
	template = PAGE_TEMPLATES[template_key]
	if template_key == DEFAULT_TEMPLATE_KEY:
		return render_template("main/master.html", page=page)
	values = fill_defaults(page.get("fields") or {}, template["fields"])
	return render_template(template["template_file"], page=page, f=values)


@app.route("/upload_image", methods=["POST"])
@capability_required("upload_media")
def upload_image():
	"""
	Handles image upload for the media library.
	
	Features:
	- Validates CSRF token
	- Handles file upload securely
	- Creates multiple image thumbnails
	- Stores image metadata in database
	- Supports multiple file types (png, jpg, jpeg, gif, webp)
	
	Returns:
		JSON: Response containing upload status and file location
		
	Raises:
		400: If file is missing or invalid
		403: If CSRF token is invalid
		500: If database or file operations fail
	"""

	try:
		token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
		if not token or token != session.get("csrf_token"):
			return jsonify({"error": "Invalid CSRF token"}), 403

		if "file" not in request.files:
			return jsonify({"error": "No file part"}), 400

		file = request.files["file"]
		if file.filename == "":
			return jsonify({"error": "No selected file"}), 400

		if file and allowed_file(file.filename):
			original_filename = secure_filename(file.filename)
			filename = str(uuid.uuid4()) + os.path.splitext(original_filename)[1]
			file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)

			os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

			file.save(file_path)

			try:
				thumbnails = create_image_thumbnails(file_path, filename)

				with Image.open(file_path) as img:
					width, height = img.size

				file_size = os.path.getsize(file_path)

				media_data = {
					"filename": filename,
					"original_filename": original_filename,
					"mime_type": file.content_type,
					"file_size": file_size,
					"width": width,
					"height": height,
					"alt_text": os.path.splitext(original_filename)[0],
					"caption": "",
					"title": os.path.splitext(original_filename)[0],
					"description": "",
					"thumbnail": thumbnails.get("thumbnail"),
				}

				result = db_manager.add_media(media_data)
				if result:
					return jsonify(
						{"location": f"/uploads/{filename}", "success": True}
					)

				return jsonify({"error": "Failed to save to database"}), 500

			except Exception as e:
				try:
					os.remove(file_path)
				except:
					pass
				raise e

		return jsonify({"error": "Invalid file type"}), 400

	except Exception as e:
		print(f"Upload error: {str(e)}")
		return jsonify({"error": str(e)}), 500


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
	"""
	Serves uploaded media files securely.
	
	Features:
	- Secure file serving
	- Path traversal prevention
	- Proper MIME type handling
	- 404 handling for missing files
	- Optional access control
	
	Security Measures:
	- Validates file paths
	- Prevents directory traversal
	- Confines to upload directory
	- Proper error handling
	
	Args:
		filename (str): Path to the requested file
		
	Returns:
		FileResponse: Served file with proper MIME type
		
	Raises:
		403: On path traversal attempts
		404: When file not found
	"""

	upload_folder = os.path.abspath(app.config["UPLOAD_FOLDER"])
	requested_path = os.path.abspath(os.path.join(upload_folder, filename))

	if not requested_path.startswith(upload_folder + os.sep):
		abort(403, "Forbidden: Path traversal detected.")

	if not os.path.isfile(requested_path):
		abort(404)

	return send_file(requested_path)


@app.route('/sitemap.xml')
def sitemap():
	"""
	Serves the pre-generated sitemap from the dedicated sitemaps table.
	"""
	sitemap_xml = db_manager.get_sitemap_content()
	
	if not sitemap_xml:
		print("generating sitemap")
		sitemap_task=generate_and_store_sitemap()
		if not sitemap_task:
			return '<!-- Sitemap not available -->'
		sitemap_xml = db_manager.get_sitemap_content() or '<!-- Sitemap not available -->'

	response = make_response(sitemap_xml)
	response.headers["Content-Type"] = "application/xml"
	return response


app.config["REGENERATE_SITEMAP"] = generate_and_store_sitemap
app.register_blueprint(content_blueprint)


def _read_setting(key, default=None):
	return get_site_settings().get(key, default)


def _write_setting(key, value):
	update_site_settings({key: value})


def _extensions_changed():
	content_store.set_extension_presets(extensions.section_presets())
	if THEME_NAME not in available_themes():
		update_site_settings({"theme": "default"})
		apply_theme("default")
	else:
		apply_theme(THEME_NAME)
	app.jinja_env.cache.clear()


def _switch_theme(name):
	if apply_theme(name):
		update_site_settings({"theme": name})
		return True
	return False


extensions.read_setting = _read_setting
extensions.write_setting = _write_setting
extensions.on_changed = _extensions_changed
extensions.apply_directories = content_store.set_directories
extensions.switch_theme = _switch_theme
extensions.jinja_env = app.jinja_env
_ext_state = {"seen": None}


def _ext_sync_needed():
	"""Cheap check: has the saved list of active extensions changed (e.g. in another worker)?"""
	return _ext_state["seen"] is None or time.time() - _ext_state.get("at", 0) > 2


def _ext_sync():
	_ext_state["at"] = time.time()
	try:
		changed = extensions.sync()
	except Exception as e:
		print(f"[extensions] sync failed: {e}")
		return
	_ext_state["seen"] = True


def _ext_dispatch():
	if request.path.startswith(("/static/", "/uploads/")):
		return None
	match = extensions.dispatch(request.path, request.method)
	if not match:
		return None
	view, kwargs = match
	return app.make_response(view(**kwargs))


@app.before_request
def _extensions_before():
	if request.path.startswith("/static/"):
		return None
	if _ext_sync_needed():
		_ext_sync()
	return None


@app.route("/ext/<slug>/static/<path:filename>")
def extension_static(slug, filename):
	folder = extensions.static_dir(slug)
	if not folder:
		abort(404)
	return send_from_directory(folder, filename)


import extension_views
import theme_views
import package_views
import migrate_views
extension_views.init(app, extensions)
theme_views.init(app, lambda: THEME_NAME, _switch_theme)
package_views.init(app, extensions, lambda: THEME_NAME, import_content)
app.request_class = migrate_views.MigrationRequest
migrate_views.init(app, RESERVED_SLUGS)

_ext_state["booting"] = True
_ext_sync()
_ext_state["booting"] = False

app.before_request(_ext_dispatch)


if __name__ == "__main__":
	port = int(os.environ.get("PORT", 5000))

	debug = os.environ.get("FLASK_ENV", "development") == "development"

	app.run(port=port, debug=debug)
	print(f"Server is running on http://127.0.0.1:{port}")
