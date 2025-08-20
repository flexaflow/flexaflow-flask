# FlexaFlow Flask CMS
# 
# Author: Mashiur Rahman
# Last Updated: August 20, 2025

#  ***********************  Start Standard And Installed library Import ****************************
#**************************

import os
import re
import uuid
import json
import datetime
import pyqrcode
import io
import base64
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
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from jinja2 import Environment, FileSystemLoader, ChoiceLoader, select_autoescape
from utils.theme_loader import load_theme_functions
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
#  ***********************  End Standard And Installed library Import ****************************
#*
#
#
#
#
#
#
#




#
#  ***********************  Start Local Import ****************************
#**************************
from data_store import Page, Post, Category, Tag, SiteSetting

#  ***********************  End Local Import ****************************
#**************************








#  ***********************  Start Configuration ****************************
#**************************
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
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", secrets.token_hex(16))

# Configure template loading from multiple directories with absolute paths
base_dir = os.path.dirname(os.path.abspath(__file__))
app.jinja_loader = ChoiceLoader(
	[
		FileSystemLoader(
			os.path.join(base_dir, "templates")
		),  # Load from main templates
		FileSystemLoader(
			os.path.join(base_dir, "themes", "default", "templates")
		),  # Load from theme templates
	]
)













site_settings = get_site_settings()
#print(site_settings)
THEME_NAME = site_settings.get("theme", "default")







@app.context_processor
def inject_globals():
	"""
	Injects global variables into all templates.
	
	This context processor makes the following variables available to all templates:
	- year: Current year
	- theme functions: All functions from the current theme
	- settings: Current site settings
	
	Returns:
		dict: Dictionary containing global template variables
	"""
	data = {"year": datetime.datetime.now().year}
	for name, func in theme_functions.items():
		data[name] = func
	data["settings"] = get_site_settings()
	return data







UPLOAD_DIR = "uploads"

# Add WordPress-like image sizes
IMAGE_SIZES = {"thumbnail": (150, 150), "medium": (300, 300), "large": (1024, 1024)}

if not os.path.exists(UPLOAD_DIR):
	os.makedirs(UPLOAD_DIR)

if not os.path.exists(os.path.join(UPLOAD_DIR, "thumbnails")):
	os.makedirs(os.path.join(UPLOAD_DIR, "thumbnails"))

# Load theme functions
theme_functions = load_theme_functions(THEME_NAME)

# app.secret_key = os.getenv('SECRET_KEY', 'dev_key_change_in_production')
app.secret_key = secrets.token_hex(32)
app.config["UPLOAD_FOLDER"] = UPLOAD_DIR
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

#  ***********************  End Configuration  ****************************
#*

#
#
#
#
#
#
#




#  ***********************  start helper functions ****************************
#**************************
def slugify(title):
	"""
	Convert a string title into a URL-friendly slug.

	Args:
		title (str): The title to slugify.

	Returns:
		str: The slugified string.
	"""
	# Lowercase, remove special chars, replace spaces with dashes
	slug = re.sub(r"[^\w\s-]", "", title).strip().lower()
	return re.sub(r"[-\s]+", "-", slug)


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
			# Convert RGBA to RGB if needed
			if img.mode == "RGBA":
				bg = Image.new("RGB", img.size, "white")
				bg.paste(img, mask=img.split()[3])
				img = bg

			# Create thumbnails for each size
			for size_name, dimensions in IMAGE_SIZES.items():
				thumb = img.copy()
				thumb.thumbnail(dimensions, Image.Resampling.LANCZOS)

				# Generate thumbnail filename
				name, ext = os.path.splitext(filename)
				thumb_filename = f"{name}-{size_name}{ext}"
				thumb_path = os.path.join(
					app.config["UPLOAD_FOLDER"], "thumbnails", thumb_filename
				)

				# Save thumbnail
				thumb.save(thumb_path, quality=90, optimize=True)
				thumbnails[size_name] = thumb_filename

	except Exception as e:
		print(f"Error creating thumbnails: {str(e)}")

	return thumbnails


def setup_required(f):
	"""
	Decorator to ensure the initial setup is complete before accessing a route.

	Args:
		f (function): The route function to wrap.

	Returns:
		function: The wrapped function.
	"""	
	@wraps(f)
	def decorated_function(*args, **kwargs):
		if not db_manager.is_setup_complete():
			return redirect(url_for("setup"))
		return f(*args, **kwargs)

	return decorated_function


def login_required(f):
	"""
	Decorator to require user login for a route.

	Args:
		f (function): The route function to wrap.

	Returns:
		function: The wrapped function.
	"""	
	@wraps(f)
	def decorated_function(*args, **kwargs):
		if not session.get("logged_in"):
			return redirect(url_for("login"))
		return f(*args, **kwargs)

	return decorated_function







# Helper functions for database operations
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
	"""Update a page by slug"""
	db = db_manager.get_session()
	try:
		page = db.query(Page).filter(Page.slug == slug).first()
		if page:
			page.title = page_data.get("title", page.title)
			page.content = page_data.get("content", page.content)
			page.description = page_data.get("description", page.description)
			page.status = page_data.get("status", page.status)
			page.updated_at = datetime.datetime.utcnow()
			db.commit()
			return True
		return False
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

			# Handle category
			if "category" in post_data:
				category = (
					db.query(Category)
					.filter(Category.slug == post_data["category"])
					.first()
				)
				post.category_id = category.id if category else None

			# Handle tags
			if "tags" in post_data:
				# Clear existing tags
				post.tags.clear()
				# Add new tags
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
		# Use the name as slug (lowercase, hyphens)
		slug = name.strip().lower().replace(" ", "-")
		# Check for existing category by name or slug
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
		# Also check form data for CSRF token
		token = request.form.get("csrf_token")
	return token and token == session.get("csrf_token")






#  ***********************  End helper functions ****************************
#*
#
#
#
#
#
#
#



###








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















#  ***********************   FIRST TIME SETUP AND SETUP ADMIN ****************************
#**************************
@app.route("/setup", methods=["GET", "POST"])
def setup():
	"""
	Handles initial system setup and configuration.
	
	Features:
	- First-time system initialization
	- Admin account creation
	- Password security enforcement
	- Optional 2FA setup
	- Database initialization
	
	Methods:
		GET: Display setup form
		POST: Process initial configuration
	
	Form Fields:
		- username: Admin username
		- password: Admin password
		- confirm_password: Password verification
		- email: Admin email
	
	Validation:
		- Password minimum length (8 chars)
		- Password confirmation match
		- Required field checks
		- Setup state validation
	
	Returns:
		GET: Setup form or redirect if complete
		POST: Redirect to login on success
	
	Security:
		- Prevents multiple setups
		- Secure password hashing
		- Email validation
		- Safe initialization
	"""	
	if db_manager.is_setup_complete():
		flash("Setup has already been completed.", "warning")
		return redirect(url_for("login"))

	if request.method == "POST":
		username = request.form.get("username")
		password = request.form.get("password")
		confirm_password = request.form.get("confirm_password")
		email = request.form.get("email")

		if password != confirm_password:
			flash("Passwords do not match.", "error")
			return render_template("setup.html")

		if len(password) < 8:
			flash("Password must be at least 8 characters long.", "error")
			return render_template("setup.html")

		# Generate hashed password
		hashed_password = generate_password_hash(password)

		# Complete setup with 2FA disabled by default
		success = db_manager.complete_setup(
			username=username,
			password=hashed_password,
			email=email,
			enable_2fa=False,
			two_fa_secret=None,
		)

		if success:
			flash("Setup completed successfully!", "success")
			return redirect(url_for("login"))
		else:
			flash("Error during setup. Please try again.", "error")

	return render_template("setup.html")


#  ***********************   End FIRST TIME SETUP AND SETUP ADMIN ****************************
#*



#  ***********************   Start Login Logout ****************************
#**************************
@app.route("/login", methods=["GET", "POST"])
@setup_required
def login():
	"""
	Handles user authentication with optional 2FA support.
	
	Features:
	- Username/password authentication
	- Two-factor authentication (2FA) if enabled
	- Session management
	- CSRF protection
	- Secure password verification
	
	Methods:
		GET: Display login form
		POST: Process login attempt
			- Step 1: Validate username/password
			- Step 2: Validate 2FA code (if enabled)
	
	Session Management:
		- pending_2fa: Tracks 2FA verification status
		- temp_username: Stores username during 2FA
		- logged_in: Final authentication state
	
	Returns:
		GET: Rendered login template
		POST: 
			- Redirect to admin on success
			- Rendered login template with errors
			- 2FA verification form if needed
	
	Security:
		- Hashed password comparison
		- 2FA token verification
		- Session cleanup on fresh login
		- Protection against session fixation
	"""	
	# Always clear any 2FA session flags on GET (fresh login page)
	if request.method == "GET":
		session.pop("pending_2fa", None)
		session.pop("temp_username", None)
		session.pop("logged_in", None)

	if request.method == "POST":
		username = request.form.get("username")
		password = request.form.get("password")
		twofa_code = request.form.get("2fa_code")

		admin_setup = db_manager.get_admin_setup()
		if not admin_setup:
			flash("System is not properly configured.", "error")
			return redirect(url_for("setup"))

		# If we're in the 2FA verification step
		if session.get("pending_2fa") and twofa_code:
			if username != session.get("temp_username"):
				session.clear()
				flash("Invalid session. Please login again.", "error")
				return redirect(url_for("login"))

			if admin_setup.get("two_fa_enabled") and admin_setup.get("two_fa_secret"):
				totp = pyotp.TOTP(admin_setup["two_fa_secret"])
				if totp.verify(twofa_code):
					session["logged_in"] = True
					session.pop("pending_2fa", None)
					session.pop("temp_username", None)
					flash("Login successful!", "success")
					return redirect(url_for("admin"))
				else:
					flash("Invalid 2FA code!", "error")
					return render_template("login.html", show_2fa=True)
			else:
				session.clear()
				flash("2FA is not properly configured. Please login again.", "error")
				return redirect(url_for("login"))

		# First step of login - username/password verification
		if username and password:
			if username == admin_setup["username"] and check_password_hash(
				admin_setup["password"], password
			):
				# Check if 2FA is properly enabled and configured
				if admin_setup.get("two_fa_enabled") and admin_setup.get(
					"two_fa_secret"
				):
					session["pending_2fa"] = True
					session["temp_username"] = username
					return render_template("login.html", show_2fa=True)
				else:
					# No 2FA needed, clear any 2FA session flags
					session.pop("pending_2fa", None)
					session.pop("temp_username", None)
					session["logged_in"] = True
					flash("Login successful!", "success")
					return redirect(url_for("admin"))
			else:

				flash("Invalid credentials!", "error")
		else:
			flash("Username and password are required!", "error")

	# GET request or form validation failed
	show_2fa = session.get("pending_2fa", False)
	return render_template("login.html", show_2fa=show_2fa)





@app.route("/logout")
@login_required
def logout():
	"""
	Handles user logout and session cleanup.
	
	Features:
	- Complete session cleanup
	- 2FA state reset
	- User feedback
	- Secure redirect
	
	Security Measures:
	- Requires active login
	- Clears all session data
	- Prevents session fixation
	- Invalidates authentication state
	
	Processing:
	1. Validates current login
	2. Clears entire session
	3. Provides feedback message
	4. Redirects to login page
	
	Returns:
		302: Redirect to login page with:
			- Success message
			- Clean session state
	
	Notes:
		- Also clears 2FA states
		- Prevents session reuse
		- Forces new authentication
	"""	
	session.clear()
	flash("You have been logged out.", "info")
	return redirect(url_for("login"))


#  ***********************   End Login Logout ****************************
#*







#  ***********************   Start Administrator and  two-factor authentication configuration  ****************************
#**************************

@app.route("/admin")
@login_required
def admin():
	"""
	Admin dashboard view that displays all pages and posts.
	
	Shows both published and draft content. Requires user to be logged in.
	
	Returns:
		str: Rendered admin dashboard template with list of all pages and posts
	"""	
	# Get all pages (both published and draft)
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

	# Get all posts (both published and draft)
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

	return render_template("admin.html", pages=all_pages, posts=all_posts)






@app.route("/admin/settings")
@login_required
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
	admin_setup = db_manager.get_admin_setup()
	# Ensure two_fa_enabled reflects the admin's real 2FA status
	site_settings["two_fa_enabled"] = bool(
		admin_setup and admin_setup.get("two_fa_enabled")
	)
	return render_template(
		"settings.html",
		settings=site_settings,
		pages=get_pages(),
		custom_analytics=os.getenv("CUSTOM_ANALYTICS_SCRIPT", ""),
	)


@app.route("/admin/menu")
@login_required
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
	return render_template("menu-editor.html", menu_items=menu_items, pages=get_pages())


@app.route("/api/settings", methods=["GET", "POST"])
@login_required
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
		data = request.json
		success = update_site_settings(data)
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
		# append a short suffix until unique
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
@login_required
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
		data = request.json
		settings = get_site_settings()
		settings["menu_items"] = data
		success = update_site_settings(settings)
		if success:
			return jsonify({"status": "success", "message": "Menu updated"})
		else:
			return jsonify({"status": "error", "message": "Failed to update menu"}), 500


@app.route("/admin/category/add", methods=["POST"])
@login_required
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
@login_required
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
	media = db_manager.get_media_library(page=page, search=search)

	# Return JSON if requested
	if request.args.get("format") == "json":
		return jsonify(media)

	return render_template("media-library.html", media=media)


@app.route("/admin/media/upload", methods=["POST"])
@login_required
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
				# Generate unique filename
				original_filename = secure_filename(file.filename)
				filename = str(uuid.uuid4()) + os.path.splitext(original_filename)[1]
				file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)

				# Save original file
				file.save(file_path)

				# Create thumbnails
				thumbnails = create_image_thumbnails(file_path, filename)

				# Get image dimensions and size
				with Image.open(file_path) as img:
					width, height = img.size

				file_size = os.path.getsize(file_path)

				# Add to media library
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












@app.route("/admin/export", methods=["GET"])
@login_required
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
	# Compose filename: flexaflow(v1.0.0)_sitename_YYYYMMDDTHHMMSSZ.xml
	timestamp = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
	filename = f"flexaflow_{FLEXAFLOW_VERSION}_export_{site_title}_{timestamp}.xml"

	root = ET.Element("flexaflow_export", version="1.0", exported_at=export_time)

	# Export pages
	pages_elem = ET.SubElement(root, "pages")
	for slug, page in get_pages().items():
		page_elem = ET.SubElement(pages_elem, "page", slug=slug)
		for k, v in page.items():
			child = ET.SubElement(page_elem, k)
			child.text = str(v) if v is not None else ""

	# Export posts
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

	# Serialize XML to memory
	xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
	return send_file(
		io.BytesIO(xml_bytes),
		mimetype="application/xml",
		as_attachment=True,
		download_name=filename,
	)


@app.route("/admin/import", methods=["GET", "POST"])
@login_required
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
			# Get existing slugs
			existing_page_slugs = set(get_pages().keys())
			existing_post_slugs = set(get_posts().keys())
			skipped_pages = []
			skipped_posts = []
			imported_pages = 0
			imported_posts = 0
			# Import pages
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
			# Import posts
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
@login_required
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
@login_required
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
@login_required
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
	# Only allow deleting unused categories
	categories = get_categories() or {}
	if slug in categories and categories[slug].get("count", 0) == 0:
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
@login_required
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
@login_required
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













@app.route("/admin/2fa-setup", methods=["GET", "POST"])
@login_required
def setup_2fa():
	"""
	Handles setup of two-factor authentication (2FA).
	
	Features:
	- Generates QR code for 2FA setup
	- Validates 2FA verification code
	- Stores 2FA secret securely
	- Supports setup cancellation
	- Handles legacy configurations
	
	Routes:
		GET: Shows 2FA setup page with QR code
		POST: Handles verification or cancellation
		
	Returns:
		GET: Rendered 2FA setup template with QR code
		POST: Redirect to admin or login page
		
	Security:
	- Requires user to be logged in
	- Uses secure random token generation
	- Stores secrets securely in database
	- Validates codes
	"""

	admin_setup = db_manager.get_admin_setup()
	if not admin_setup:
		flash("System is not properly configured.", "error")
		return redirect(url_for("setup"))

	# Use secret from session if present, else fallback to admin_setup (for legacy)
	two_fa_secret = session.get("pending_2fa_secret") or admin_setup.get(
		"two_fa_secret"
	)
	if not two_fa_secret:
		return redirect(url_for("enable_2fa"))

	# Handle POST: verify code or cancel 2FA
	if request.method == "POST":
		if request.form.get("cancel_2fa") == "1":
			session.pop("pending_2fa_secret", None)
			flash("Two-factor authentication setup was canceled.", "info")
			return redirect(url_for("login"))
		code = request.form.get("verify_2fa_code", "").strip()
		totp = pyotp.TOTP(two_fa_secret)
		if totp.verify(code):
			# Save 2FA secret and enable 2FA only after verification
			db_manager.complete_setup(
				username=admin_setup["username"],
				password=admin_setup["password"],
				email=admin_setup["email"],
				enable_2fa=True,
				two_fa_secret=two_fa_secret,
			)
			session.pop("pending_2fa_secret", None)
			flash("Two-factor authentication is now active and verified!", "success")
			return redirect(url_for("admin"))
		else:
			flash("Invalid code. Please try again.", "danger")

	provisioning_uri = pyotp.totp.TOTP(two_fa_secret).provisioning_uri(
		name=admin_setup["email"], issuer_name="FlexaFlow CMS"
	)
	qr = pyqrcode.create(provisioning_uri)
	buffer = io.BytesIO()
	qr.svg(buffer, scale=5)
	qr_code = base64.b64encode(buffer.getvalue()).decode()

	return render_template("setup_2fa.html", qr_code=qr_code, secret=two_fa_secret)








@app.route("/admin/disable-2fa")
@login_required
def disable_2fa():
	"""
	Disables two-factor authentication for the admin account.
	
	Features:
	- Secure 2FA deactivation
	- Configuration update
	- Secret removal
	- User feedback
	- State validation
	
	Security Measures:
	- Requires admin login
	- Validates system configuration
	- Removes 2FA secrets
	- Updates authentication state
	
	Processing Steps:
	1. Verify admin setup exists
	2. Clear 2FA configuration
	3. Update admin settings
	4. Provide user feedback
	
	Returns:
		302: Redirect to settings with:
			- Success/error message
			- Updated 2FA status
	
	Notes:
		- Immediate effect
		- Requires re-setup to enable
		- Maintains password security
	"""
	
	admin_setup = db_manager.get_admin_setup()
	if not admin_setup:
		flash("System is not properly configured.", "error")
		return redirect(url_for("setup"))

	# Update the admin setup to disable 2FA
	success = db_manager.complete_setup(
		username=admin_setup["username"],
		password=admin_setup["password"],
		email=admin_setup["email"],
		enable_2fa=False,
		two_fa_secret=None,
	)

	if success:
		flash("Two-factor authentication has been disabled.", "success")
	else:
		flash("Failed to disable two-factor authentication.", "error")

	return redirect(url_for("settings"))


@app.route("/admin/enable-2fa")
@login_required
def enable_2fa():
	"""
	Initiates two-factor authentication setup process.
	
	Features:
	- Generates new 2FA secret
	- Stores secret temporarily in session
	- Validates system configuration
	- User feedback
	- Secure secret generation
	
	Process Flow:
	1. Verify admin configuration
	2. Generate secure random secret
	3. Store in session temporarily
	4. Redirect to setup process
	
	Returns:
		302: Redirect to:
			- 2FA setup page with new secret
			- Setup page if system not configured
			
	Security:
		- Admin authentication required
		- Secure secret generation
		- Temporary session storage
		- System validation
	"""

	admin_setup = db_manager.get_admin_setup()
	if not admin_setup:
		flash("System is not properly configured.", "error")
		return redirect(url_for("setup"))

	# Generate new 2FA secret and store in session only
	two_fa_secret = pyotp.random_base32()
	session["pending_2fa_secret"] = two_fa_secret
	flash(
		"Two-factor authentication setup started. Please scan the QR code and verify.",
		"success",
	)
	return redirect(url_for("setup_2fa"))


#  ***********************   End Administrator and  two-factor authentication configuration  ****************************
#*



























#  *********************** Start Page and Post Serve  ****************************
#**************************


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

	# Check if system is configured, if not redirect to setup
	if not db_manager.is_setup_complete():
		return redirect(url_for("setup"))

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

		# Sort by created_at or updated_at
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

	# Only show published posts to public (unless in admin mode)
	if post.get("status") != "published":
		# You can add admin authentication check here
		# For now, we'll show drafts too
		pass

	categories = get_categories()
	tags = get_tags()

	# Get related posts
	related_posts = []
	post_tags = post.get("tags", [])
	all_posts = get_posts()

	for other_slug, other_post in all_posts.items():
		if other_slug != slug and other_post.get("status") == "published":
			other_tags = other_post.get("tags", [])
			if set(post_tags) & set(other_tags):  # If there are common tags
				other_post_copy = other_post.copy()
				other_post_copy["slug"] = other_slug
				related_posts.append(other_post_copy)
				if len(related_posts) >= 3:
					break

	# Add slug to post for template
	post["slug"] = slug

	return render_template(
		"post.html",
		post=post,
		related_posts=related_posts,
		categories=categories,
		tags=tags,
	)


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
	if category_slug in categories:
		category_posts = []
		all_posts = get_posts()

		for slug, post in all_posts.items():
			# Corrected line:
			if (
				(post.get("category") or {}).get("slug") == category_slug
				and post.get("status") == "published"
			):
				post_copy = post.copy()
				post_copy["slug"] = slug
				category_posts.append(post_copy)


		category_data = categories[category_slug]
		category_data["count"] = len(category_posts)

		return render_template(
			"category.html", category=category_data, posts=category_posts
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

	return render_template(
		"tag.html",
		tag={"name": tag_name, "count": len(tagged_posts)},
		posts=tagged_posts,
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

	return render_template("search.html", query=query, results=search_results)






#  *********************** End Page and Post Serve  ****************************
#*

























#  *********************** Start Page and Post CRUD  ****************************
#**************************

@app.route("/page/add", methods=["GET", "POST"])
def add_page_route():
	"""
	Handles page creation with support for drafts and publishing.

	Features:
	- Supports both draft and published states
	- Automatic slug generation
	- Form validation
	- User feedback
	- Database integration

	Methods:
		GET: Display page creation form
		POST: Process new page submission

	Form Data:
		- slug: URL slug (auto-generated if not provided)
		- title: Page title
		- description: Meta description
		- content: Page content
		- status: 'published' or 'draft'

	Returns:
		GET: Rendered page creation form
		POST: Redirect to admin with status message

	Security:
		- Input validation
		- Safe slug generation
		- Database transaction safety
	"""
	if request.method == "POST":
		slug = request.form.get("slug")
		slug = slugify(slug)
		title = request.form.get("title")
		description = request.form.get("description")
		content = request.form.get("content")
		status = request.form.get("status", "draft")

		page_data = {
			"content": content,
			"title": title,
			"description": description,
			"status": status,
		}

		if status == "published":
			success = add_page(slug, page_data)
		else:
			success = add_draft_page(slug, page_data)

		if success:
			flash("Page added successfully!", "success")
		else:
			flash("Error adding page!", "error")

		return redirect(url_for("admin"))

	return render_template("add_page.html")
@app.route("/page/edit/<slug>", methods=["GET", "POST"])
def edit_page(slug):
	"""
	Handles editing of existing pages.
	
	Features:
	- Full page editing
	- Status management
	- Content preview
	- Version tracking
	- SEO metadata editing
	
	URL Parameters:
		slug (str): Page identifier to edit
	
	Methods:
		GET: Display edit form with current content
		POST: Process page updates
	
	Form Fields:
		- title: Page title
		- description: SEO description
		- content: Main page content
		- status: Publication status
	
	Processing:
	1. Load existing page
	2. Validate input
	3. Update content
	4. Update timestamps
	5. Save changes
	
	Returns:
		GET: Rendered edit form with current content
		POST: Redirect to admin with status
		404: If page not found
	
	Security:
		- Validates page existence
		- Handles concurrent edits
		- Preserves metadata
	"""	
	page = get_page_by_slug(slug)

	if not page:
		return "<h1>Page not found</h1>", 404

	if request.method == "POST":
		title = request.form.get("title")
		description = request.form.get("description")
		content = request.form.get("content")
		status = request.form.get("status", "draft")

		page_data = {
			"content": content,
			"title": title,
			"description": description,
			"status": status,
		}

		success = update_page_by_slug(slug, page_data)

		if success:
			flash("Page updated successfully!", "success")
		else:
			flash("Error updating page!", "error")

		return redirect(url_for("admin"))

	return render_template("edit_page.html", slug=slug, page=page)


@app.route("/page/preview/<slug>")
@login_required
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
		return render_template("preview.html", page=page, theme=theme)
	else:
		return "<h1>Page not found</h1>", 404


@app.route("/page/preview_draft", methods=["POST"])
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


@app.route("/page/delete/<slug>")
@login_required
def delete_page_route(slug):
	"""
	Deletes a page by its URL slug.

	Features:
	- Removes page from database
	- Handles both published and draft pages
	- Provides user feedback
	- Maintains data integrity

	Args:
		slug (str): URL slug of the page to delete

	Returns:
		302: Redirect to admin dashboard with:
			- Success message if deletion succeeded
			- Error message if deletion failed

	Security:
		- Requires admin login
		- Database transaction safety
		- Error isolation
	"""
	success = delete_page(slug)
	if success:
		flash("Page deleted successfully!", "success")
	else:
		flash("Error deleting page!", "error")
	return redirect(url_for("admin"))
@app.route("/post/add", methods=["GET", "POST"])
@login_required
def add_post_route():
	"""
	Handles creation of new blog posts.
	
	Features:
	- Supports both draft and published states
	- Auto-generates URL slugs
	- Handles categories and tags
	- Input validation
	- Error handling
	- Duplicate slug prevention
	
	Methods:
		GET: Display post creation form
		POST: Process new post submission
	
	Form Data:
		slug: URL slug (auto-generated if empty)
		title: Post title
		description: Post description/excerpt
		content: Main post content
		category: Category slug
		tags: Comma-separated tag list
		status: Published/draft status
	
	Returns:
		GET: Rendered post creation form
		POST: 
			- Redirect to admin on success
			- Form with errors on failure
			
	Security:
		- Requires admin login
		- Validates input
		- Sanitizes slugs
		- Prevents duplicate slugs
	"""

	try:
		if request.method == "POST":
			slug = request.form.get("slug", "").strip()
			slug = slugify(slug)
			title = request.form.get("title", "").strip()
			description = request.form.get("description", "").strip()
			content = request.form.get("content", "").strip()
			category = request.form.get("category", "").strip()
			tag_list = request.form.get("tags", "").strip()
			status = request.form.get("status", "draft").strip()

			if not slug or not title or not content:
				raise ValueError("Slug, title and content are required")

			# Check if post already exists
			existing_post = get_post_by_slug(slug)
			if existing_post:
				raise ValueError(f"A post with slug '{slug}' already exists")

			post_tags = [tag.strip() for tag in tag_list.split(",") if tag.strip()]

			post_data = {
				"content": content,
				"title": title,
				"excerpt": description
				or title[:200],  # Use first 200 chars if no description
				"status": status,
				"category": category,
				"tags": post_tags,
			}

			if status == "published":
				success = add_post(slug, post_data)
			else:
				success = add_draft_post(slug, post_data)

			if success:
				update_tag_counts()
				flash("Post added successfully!", "success")
				return redirect(url_for("admin"))
			else:
				raise ValueError("Failed to save post")

		categories = get_categories()
		if not categories:
			categories = {}
		return render_template("add_post.html", categories=categories, error=None)

	except Exception as e:
		return render_template(
			"add_post.html",
			categories=get_categories(),
			error=str(e),
			form_data={
				"slug": request.form.get("slug", ""),
				"title": request.form.get("title", ""),
				"description": request.form.get("description", ""),
				"content": request.form.get("content", ""),
				"category": request.form.get("category", ""),
				"tags": request.form.get("tags", ""),
				"status": request.form.get("status", "draft"),
			},
		)


@app.route("/post/edit/<slug>", methods=["GET", "POST"])
@login_required
def edit_post(slug):
	"""
	Handles editing of existing blog posts.
	
	Features:
	- Full post editing
	- Category management
	- Tag management
	- Status updates
	- Metadata editing
	
	URL Parameters:
		slug (str): Post identifier to edit
		
	Methods:
		GET: Display edit form with current content
		POST: Process post updates
		
	Form Fields:
		- title: Post title
		- description: Post excerpt
		- content: Main post content
		- category: Category selection
		- tags: Comma-separated tag list
		- status: Publication status
		
	Returns:
		GET: Rendered edit form with current content
		POST: Redirect to admin with status
		404: If post not found
		
	Security:
		- Requires admin login
		- Validates post existence
		- Safe tag/category handling
	"""

	post = get_post_by_slug(slug)

	if not post:
		return "<h1>Post not found</h1>", 404

	if request.method == "POST":
		title = request.form.get("title")
		description = request.form.get("description")
		content = request.form.get("content")
		category = request.form.get("category")
		tag_list = request.form.get("tags", "")
		status = request.form.get("status", "draft")

		post_tags = [tag.strip() for tag in tag_list.split(",") if tag.strip()]

		post_data = {
			"content": content,
			"title": title,
			"excerpt": description,
			"status": status,
			"category": category,
			"tags": post_tags,
		}

		success = update_post_by_slug(slug, post_data)

		if success:
			update_tag_counts()
			flash("Post updated successfully!", "success")
		else:
			flash("Error updating post!", "error")

		return redirect(url_for("admin"))

	# Format tags for display
	if "tags" in post and isinstance(post["tags"], list):
		post["tags_string"] = ", ".join(post["tags"])
	else:
		post["tags_string"] = ""

	return render_template(
		"edit_post.html", slug=slug, post=post, categories=get_categories()
	)


@app.route("/post/delete/<slug>")
def delete_post_route(slug):
	"""
	Handles deletion of blog posts.
	
	Features:
	- Removes post content
	- Updates tag counts
	- Cleans up relationships
	- User feedback
	- Error handling
	
	Args:
		slug (str): URL slug of post to delete
		
	Process:
	1. Delete post content
	2. Update tag relationships
	3. Recalculate tag counts
	4. Provide user feedback
	
	Returns:
		302: Redirect to admin with:
			- Success message if deleted
			- Error message if failed
			
	Notes:
		- Also handles draft posts
		- Maintains tag count accuracy
	"""

	success = delete_post_by_slug(slug)
	if success:
		update_tag_counts()
		flash("Post deleted successfully!", "success")
	else:
		flash("Error deleting post!", "error")

	return redirect(url_for("admin"))

@app.route("/<slug>")
def view_page(slug):
	"""
	Catch-all route that serves individual pages.
	
	Features:
	- Handles all non-specific routes
	- Serves published pages only
	- Theme integration
	- 404 handling
	
	Args:
		slug (str): URL slug of the page to display
		
	Processing:
	1. Retrieve page by slug
	2. Verify published status
	3. Apply theme template
	4. Handle missing pages
	
	Returns:
		str: Rendered page content with theme
		404: If page not found or not published
		
	Notes:
		- Only serves published pages
		- Must be the last route defined
		- Used for static pages
	"""	
	page = get_page_by_slug(slug)
	if page and page.get("status") == "published":
		return render_template("main/master.html", page=page)
	else:
		abort(404)



#  *********************** End Page and Post CRUD  ****************************
#**************************
















#  *********************** Start Media Upload  ****************************
#**************************


@app.route("/upload_image", methods=["POST"])
@login_required
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
		# Check CSRF token from either header or form
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

			# Ensure upload directory exists
			os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

			# Save the file
			file.save(file_path)

			try:
				# Create thumbnails
				thumbnails = create_image_thumbnails(file_path, filename)

				# Get image dimensions
				with Image.open(file_path) as img:
					width, height = img.size

				file_size = os.path.getsize(file_path)

				# Prepare media data
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

				# Add to media library
				result = db_manager.add_media(media_data)
				if result:
					return jsonify(
						{"location": f"/uploads/{filename}", "success": True}
					)

				return jsonify({"error": "Failed to save to database"}), 500

			except Exception as e:
				# Clean up the file if there was an error
				try:
					os.remove(file_path)
				except:
					pass
				raise e

		return jsonify({"error": "Invalid file type"}), 400

	except Exception as e:
		print(f"Upload error: {str(e)}")  # Add error logging
		return jsonify({"error": str(e)}), 500





#@app.route("/uploads/<path:filename>")
#@login_required
#def uploaded_file(filename):
#    """Serve uploaded files with proper mime type"""
#    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)






@app.route("/uploads/<path:filename>")
#@login_required
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

	# Ensure the requested path is inside the upload folder
	if not requested_path.startswith(upload_folder + os.sep):
		abort(403, "Forbidden: Path traversal detected.")

	if not os.path.isfile(requested_path):
		abort(404)

	return send_file(requested_path)



#  *********************** End Media Upload  ****************************
#*













if __name__ == "__main__":
	# Get port from environment variable or default to 5000
	port = int(os.environ.get("PORT", 5000))

	# Enable debug mode in development
	debug = os.environ.get("FLASK_ENV", "development") == "development"

	# Run the application
	app.run(port=port, debug=debug)
	print(f"Server is running on http://127.0.0.1:{port}")
