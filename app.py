
from pprint import pprint
import os
import logging
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
	search_posts,
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
import secrets
from flask import send_file
import xml.etree.ElementTree as ET


from sqlalchemy.exc import SQLAlchemyError


from data_store import Page, Post, Category, Tag, SiteSetting
import features
import images
import seo
from features import publish_fields, notify_review_requested, publish_due
from data_store import save_revision, _content_changed, _drop_autosaves, add_redirect, Revision, Redirect, ContactMessage, Comment
from data_store import ROLES, ROLE_ADMINISTRATOR, ROLE_EDITOR, ROLE_AUTHOR, ROLE_CONTRIBUTOR, get_latest_posts
from auth import (
	login_required,
	capability_required,
	current_user,
	can,
	login_user,
	logout_user,
	throttle_wait,
	record_failure,
	clear_failures,
	wait_message,
	log_event,
	generate_recovery_codes,
	consume_recovery_code,
	invalidate_other_sessions,
	verify_password,
	hash_password,
	validate_password,
	validate_role,
	ROLE_LABELS,
	ROLE_DESCRIPTIONS,
)
import content_store
from migrate_utils import sanitize_html
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
logger = logging.getLogger(__name__)


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
	key = ""
	if os.path.exists(key_path):
		with open(key_path, encoding="utf-8") as fh:
			key = fh.read().strip()
	if not key:  # missing or empty file: generate a fresh key
		key = secrets.token_hex(32)
		with open(key_path, "w", encoding="utf-8") as fh:
			fh.write(key)
		try:
			os.chmod(key_path, 0o600)
		except OSError:
			pass
	return key


app.config["SECRET_KEY"] = _load_secret_key()


def _configure_logging():
	"""Log to stdout (works with Docker/systemd). Security events also go to instance/security.log."""
	level = os.getenv("LOG_LEVEL", "INFO").upper()
	root = logging.getLogger()
	if not root.handlers:
		logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
	root.setLevel(level)
	sec = logging.getLogger("flexaflow.security")
	if not any(getattr(h, "_flexaflow", False) for h in sec.handlers):
		from logging.handlers import RotatingFileHandler
		try:
			os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance"), exist_ok=True)
			fh = RotatingFileHandler(
				os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "security.log"),
				maxBytes=1_000_000, backupCount=5, encoding="utf-8")
			fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
			fh._flexaflow = True
			sec.addHandler(fh)
		except OSError:
			pass


_configure_logging()


if os.getenv("FLEXAFLOW_BEHIND_PROXY", "0") == "1":
	from werkzeug.middleware.proxy_fix import ProxyFix
	app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)


@app.after_request
def _security_headers(response):
	"""Conservative defaults. CSP omits script-src/style-src so the CDN-based admin keeps working."""
	response.headers.setdefault("X-Content-Type-Options", "nosniff")
	response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
	response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
	response.headers.setdefault(
		"Content-Security-Policy", "frame-ancestors 'self'; object-src 'none'; base-uri 'self'; form-action 'self'")
	if app.config.get("SESSION_COOKIE_SECURE"):
		response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
	return response

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
UPLOAD_DIR = os.getenv("FLEXAFLOW_UPLOAD_DIR", "uploads")

IMAGE_SIZES = {"thumbnail": (150, 150), "medium": (300, 300), "large": (1024, 1024)}

if not os.path.exists(UPLOAD_DIR):
	os.makedirs(UPLOAD_DIR)

if not os.path.exists(os.path.join(UPLOAD_DIR, "thumbnails")):
	os.makedirs(os.path.join(UPLOAD_DIR, "thumbnails"))

theme_functions = load_theme_functions(THEME_NAME)
copy_theme_static_files(THEME_NAME)

app.config["UPLOAD_FOLDER"] = UPLOAD_DIR
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = int(os.getenv("STATIC_MAX_AGE", "86400"))
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
				images.save_alternates(thumb, name, size_name, os.path.dirname(thumb_path))
				thumbnails[size_name] = thumb_filename

	except Exception as e:
		logger.error(f"Error creating thumbnails: {str(e)}")

	return thumbnails


def get_page_by_slug(slug: str):
	"""Retrieves a page by its URL slug from the database.  Args: slug (str): The URL-friendly slug of the page  Returns: dict: Page data if found, including: - title: Page title - content: Page content - description: Page description - status: Published/draft status - created_at: Creation timestamp -..."""
	db = db_manager.get_session()
	try:
		page = db.query(Page).filter(Page.slug == slug).first()
		return page.to_dict() if page else None
	except SQLAlchemyError as e:
		logger.error(f"Error getting page by slug: {str(e)}")
		return None
	finally:
		db.close()


def get_post_by_slug(slug: str):
	"""Retrieves a post by its URL slug from either published or draft posts."""
	db = db_manager.get_session()
	try:
		post = db.query(Post).filter(Post.slug == slug).first()
		return post.to_dict() if post else None
	except SQLAlchemyError as e:
		logger.error(f"Error getting post by slug: {str(e)}")
		return None
	finally:
		db.close()
def delete_post_by_slug(slug: str) -> bool:
	"""Deletes a post from the database using its URL slug."""
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
		logger.error(f"Error deleting post: {str(e)}")
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
		if _content_changed(page, page_data.get("title"), page_data.get("content"), page_data.get("description"), "page"):
			save_revision(db, "page", page)
		_drop_autosaves(db, "page", page.id)
		if new_slug != slug:
			if db.query(Page).filter(Page.slug == new_slug).first():
				return False
			page.slug = new_slug
			add_redirect(db, "/" + slug, "/" + new_slug)
		if "publish_at" in page_data:
			page.publish_at = page_data["publish_at"]
		if "seo" in page_data:
			page.seo = json.dumps(page_data["seo"] or {}, ensure_ascii=False)
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
		logger.error(f"Error updating page: {str(e)}")
		return False
	finally:
		db.close()


def update_post_by_slug(slug: str, post_data: dict) -> bool:
	"""Updates an existing post's content and metadata.  Features: - Updates basic post data (title, content, excerpt) - Manages post status (published/draft) - Handles category assignment - Manages tag relationships - Updates timestamps appropriately - Sets published_at for newly published posts ..."""
	db = db_manager.get_session()
	try:
		post = db.query(Post).filter(Post.slug == slug).first()
		if post:
			if _content_changed(post, post_data.get("title"), post_data.get("content"), post_data.get("excerpt"), "post"):
				save_revision(db, "post", post)
			_drop_autosaves(db, "post", post.id)
			if "publish_at" in post_data:
				post.publish_at = post_data["publish_at"]
			if "seo" in post_data:
				post.seo = json.dumps(post_data["seo"] or {}, ensure_ascii=False)
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
		logger.error(f"Error updating post: {str(e)}")
		return False
	finally:
		db.close()


def add_category_to_db(name: str) -> bool:
	"""Adds a new category to the database with validation.  Features: - Name uniqueness validation - Automatic slug generation - Database transaction handling - Error handling and logging  Processing Steps: 1. Sanitize input name 2. Generate URL-friendly slug 3. Check for existing category 4. Create..."""
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
		logger.error(f"Error adding category: {str(e)}")
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
		logger.info("base url: %s", base_url)
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
		logger.info("Sitemap successfully updated in the database.")
		return True
	except Exception as e:
		logger.error(f"Error generating sitemap: {str(e)}")
		return False


@app.errorhandler(404)
def page_not_found(e):
	"""Handles 404 Not Found errors.  Features: - Custom 404 error page - Maintains site theme  Args: e: Error instance  Returns: tuple: (Rendered template, HTTP status code)"""
	moved = features.redirect_for_path(request.path) if request.method == "GET" else None
	if moved:
		return moved
	return render_template("404.html", page={"title": "404 Not Found"}), 404


@app.errorhandler(500)
def server_error(e):
	"""Handles 500 Internal Server Error.  Features: - Custom error page - Generic error messaging - Maintains site theme - Prevents exposure of system details  Args: e: Error instance  Returns: tuple: (Rendered template, HTTP status code)"""
	return render_template("404.html", page={"title": "Error"}), 500



class _LazyCsrf:
	"""Creates the session token only when a template actually prints it, so ordinary public pages
	do not set a cookie (which would make them uncacheable and tag every visitor)."""

	def __str__(self):
		if "csrf_token" not in session:
			session["csrf_token"] = secrets.token_hex(16)
		return session["csrf_token"]

	__repr__ = __str__


@app.context_processor
def inject_csrf_token():
	"""Injects CSRF token into all templates automatically.  Features: - Automatic token generation - Session-based storage - Cryptographically secure tokens - Consistent token across requests  Token Generation: - Uses secrets.token_hex for secure random values - 16 bytes (32 hex characters) length -..."""
	return {"csrf_token": _LazyCsrf()}


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RESERVED_SLUGS = {
	"admin", "login", "logout", "search", "post", "page", "category", "tag",
	"api", "uploads", "static", "sitemap.xml", "setup", "upload_image",
	"ext", "healthz", "robots.txt", "feed.xml",
}


def _safe_next(target):
	"""Only allow same-site relative redirects after login (prevents open redirects)."""
	if target and target.startswith("/") and not target.startswith("//"):
		return target
	return None


def _finish_two_factor(user_id, next_url):
	"""Second login step: verify the TOTP code for the user who passed the password check."""
	user = db_manager.get_user(user_id, include_secrets=True)
	if not user or not user["is_active"] or not user["two_fa_secret"]:
		session.pop("pending_2fa_user_id", None)
		flash("Login session expired. Please log in again.", "error")
		return redirect(url_for("auth.login"))

	who = str(user["id"])
	wait = throttle_wait("totp", who)
	if wait:
		flash(wait_message(wait), "error")
		return render_template("login.html", show_2fa=True, username=user["username"], next_url=next_url or ""), 429

	code = (request.form.get("2fa_code") or "").strip()
	used_recovery = False
	ok = bool(code) and code.isdigit() and pyotp.TOTP(user["two_fa_secret"]).verify(code, valid_window=1)
	if not ok and code and not code.isdigit():
		ok = used_recovery = consume_recovery_code(user, code)
	if ok:
		clear_failures("totp", who)
		login_user(user["id"])
		log_event("login_success", user=user["username"], two_factor=True, recovery_code=used_recovery)
		flash("Login successful!" + (" You used a recovery code; generate new ones in your profile." if used_recovery else ""), "success")
		return redirect(next_url or url_for("admin.admin"))

	record_failure("totp", who)
	flash("Invalid 2FA code!", "error")
	return render_template("login.html", show_2fa=True, username=user["username"], next_url=next_url or "")


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


def _resolve_template(key):
	"""Return a known page-template key, falling back to the plain page."""
	return key if key in PAGE_TEMPLATES else "default"


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


app.config["REGENERATE_SITEMAP"] = generate_and_store_sitemap
app.register_blueprint(content_blueprint)
app.register_blueprint(features.bp)
app.jinja_env.filters["responsive"] = images.responsive_filter(app.config["UPLOAD_FOLDER"])
app.jinja_env.globals["seo_head"] = seo.seo_head

# ---- page cache (anonymous visitors only) -----------------------------------------------------
from flask_caching import Cache

app.config.setdefault("CACHE_TYPE", os.getenv("CACHE_TYPE", "FileSystemCache"))
app.config.setdefault("CACHE_DEFAULT_TIMEOUT", int(os.getenv("CACHE_TIMEOUT", "300")))
app.config.setdefault("CACHE_DIR", os.getenv("CACHE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "cache")))
if os.getenv("CACHE_REDIS_URL"):
	app.config.setdefault("CACHE_REDIS_URL", os.getenv("CACHE_REDIS_URL"))
cache = Cache(app)

_NO_CACHE_PREFIXES = ("/admin", "/login", "/logout", "/contact", "/healthz", "/api", "/uploads", "/static", "/ext/",
                      "/setup", "/upload_image", "/sitemap.xml", "/robots.txt", "/search", "/page/", "/post/add", "/post/edit")
_CLEARING_PREFIXES = ("/admin", "/page/", "/post/", "/api", "/upload_image", "/category", "/tag")


def clear_page_cache():
	try:
		cache.clear()
	except Exception:
		logger.exception("Could not clear the page cache")


def _cacheable_request():
	return (request.method == "GET" and app.config["CACHE_TYPE"] != "NullCache"
	        and app.config["SESSION_COOKIE_NAME"] not in request.cookies
	        and not request.path.startswith(_NO_CACHE_PREFIXES) and len(request.args) <= 2)


def _cache_key():
	return f"page:{THEME_NAME}:{request.host}:{request.full_path}"


@app.before_request
def _publish_scheduled():
	if not request.path.startswith(("/static/", "/uploads/")):
		publish_due()


def _page_cache_lookup():
	if not _cacheable_request():
		return None
	hit = cache.get(_cache_key())
	if not hit:
		return None
	resp = make_response(hit["body"], hit["status"])
	resp.headers["Content-Type"] = hit["type"]
	resp.headers["X-Cache"] = "HIT"
	resp.add_etag()
	return resp.make_conditional(request)


@app.after_request
def _page_cache_store(response):
	try:
		if request.method in ("POST", "PUT", "PATCH", "DELETE"):
			if response.status_code < 400 and request.path.startswith(_CLEARING_PREFIXES):
				clear_page_cache()
		elif (_cacheable_request() and response.status_code == 200 and not response.direct_passthrough
		      and response.mimetype == "text/html" and "Set-Cookie" not in response.headers
		      and not session.modified and not session  # a page that touched the session (e.g. printed a CSRF token) is per-visitor
		      and request.endpoint is not None and response.headers.get("X-Cache") != "HIT"):
			cache.set(_cache_key(), {"body": response.get_data(), "status": 200, "type": response.headers["Content-Type"]})
			response.headers["X-Cache"] = "MISS"
			response.add_etag()
			response.make_conditional(request)
	except Exception:
		logger.exception("page cache error")
	return response


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
		logger.error(f"[extensions] sync failed: {e}")
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


import blueprints
blueprints.register(app)
import extension_views
import theme_views
import package_views
import migrate_views
import package_index
extension_views.init(app, extensions)
theme_views.init(app, lambda: THEME_NAME, _switch_theme)
package_views.init(app, extensions, lambda: THEME_NAME, blueprints.admin.import_content)
app.request_class = migrate_views.MigrationRequest
migrate_views.init(app, RESERVED_SLUGS)
package_index.init(app)

_ext_state["booting"] = True
_ext_sync()
_ext_state["booting"] = False

app.before_request(_ext_dispatch)
app.before_request(_page_cache_lookup)


if __name__ == "__main__":
	port = int(os.environ.get("PORT", 5000))

	debug = os.environ.get("FLASK_ENV", "development") == "development"

	app.run(port=port, debug=debug)
	logger.info(f"Server is running on http://127.0.0.1:{port}")
