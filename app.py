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
	Flask, render_template, request, redirect, 
	url_for, jsonify, send_from_directory, flash,
	abort, session
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from jinja2 import Environment, FileSystemLoader, ChoiceLoader
from utils.theme_loader import load_theme_functions
from functools import wraps
import pyotp
from dotenv import load_dotenv
from data_store import (
	get_pages, get_draft_pages, get_posts, get_draft_posts,
	get_categories, get_tags, get_site_settings,
	add_page, add_draft_page, delete_page, add_post, add_draft_post,
	update_site_settings, update_tag_counts, db_manager
)
from PIL import Image
import io
import secrets
# Load environment variables
load_dotenv()

# Additional imports for SQLAlchemy operations
from data_store import Page, Post, Category, Tag, SiteSetting
from sqlalchemy.exc import SQLAlchemyError

app = Flask(__name__, static_folder="static")
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', secrets.token_hex(16))

# Configure template loading from multiple directories with absolute paths
base_dir = os.path.dirname(os.path.abspath(__file__))
app.jinja_loader = ChoiceLoader([
	FileSystemLoader(os.path.join(base_dir, 'templates')),  # Load from main templates
	FileSystemLoader(os.path.join(base_dir, 'themes', 'default', 'templates'))  # Load from theme templates
])

site_settings = get_site_settings()
THEME_NAME = site_settings.get("theme", "default")
UPLOAD_DIR = "uploads"

# Add WordPress-like image sizes
IMAGE_SIZES = {
	'thumbnail': (150, 150),
	'medium': (300, 300),
	'large': (1024, 1024)
}

if not os.path.exists(UPLOAD_DIR):
	os.makedirs(UPLOAD_DIR)
	
if not os.path.exists(os.path.join(UPLOAD_DIR, 'thumbnails')):
	os.makedirs(os.path.join(UPLOAD_DIR, 'thumbnails'))

# Load theme functions
theme_functions = load_theme_functions(THEME_NAME)

#app.secret_key = os.getenv('SECRET_KEY', 'dev_key_change_in_production')
app.secret_key = secrets.token_hex(32)
app.config['UPLOAD_FOLDER'] = UPLOAD_DIR
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024



def slugify(title):
	# Lowercase, remove special chars, replace spaces with dashes
	slug = re.sub(r'[^\w\s-]', '', title).strip().lower()
	return re.sub(r'[-\s]+', '-', slug)



def create_image_thumbnails(image_path: str, filename: str) -> Dict[str, str]:
	"""Create WordPress-like image thumbnails"""
	thumbnails = {}
	try:
		with Image.open(image_path) as img:
			# Convert RGBA to RGB if needed
			if img.mode == 'RGBA':
				bg = Image.new('RGB', img.size, 'white')
				bg.paste(img, mask=img.split()[3])
				img = bg
			
			# Create thumbnails for each size
			for size_name, dimensions in IMAGE_SIZES.items():
				thumb = img.copy()
				thumb.thumbnail(dimensions, Image.Resampling.LANCZOS)
				
				# Generate thumbnail filename
				name, ext = os.path.splitext(filename)
				thumb_filename = f"{name}-{size_name}{ext}"
				thumb_path = os.path.join(app.config['UPLOAD_FOLDER'], 'thumbnails', thumb_filename)
				
				# Save thumbnail
				thumb.save(thumb_path, quality=90, optimize=True)
				thumbnails[size_name] = thumb_filename
				
	except Exception as e:
		print(f"Error creating thumbnails: {str(e)}")
	
	return thumbnails

def setup_required(f):
	@wraps(f)
	def decorated_function(*args, **kwargs):
		if not db_manager.is_setup_complete():
			return redirect(url_for('setup'))
		return f(*args, **kwargs)
	return decorated_function

def login_required(f):
	@wraps(f)
	def decorated_function(*args, **kwargs):
		if not session.get('logged_in'):
			return redirect(url_for('login'))
		return f(*args, **kwargs)
	return decorated_function



@app.route('/setup', methods=['GET', 'POST'])
def setup():
	if db_manager.is_setup_complete():
		flash('Setup has already been completed.', 'warning')
		return redirect(url_for('login'))

	if request.method == 'POST':
		username = request.form.get('username')
		password = request.form.get('password')
		confirm_password = request.form.get('confirm_password')
		email = request.form.get('email')

		if password != confirm_password:
			flash('Passwords do not match.', 'error')
			return render_template('setup.html')

		if len(password) < 8:
			flash('Password must be at least 8 characters long.', 'error')
			return render_template('setup.html')

		# Generate hashed password
		hashed_password = generate_password_hash(password)

		# Complete setup with 2FA disabled by default
		success = db_manager.complete_setup(
			username=username,
			password=hashed_password,
			email=email,
			enable_2fa=False,
			two_fa_secret=None
		)

		if success:
			flash('Setup completed successfully!', 'success')
			return redirect(url_for('login'))
		else:
			flash('Error during setup. Please try again.', 'error')

	return render_template('setup.html')

@app.route('/login', methods=['GET', 'POST'])
@setup_required
def login():
	# Always clear any 2FA session flags on GET (fresh login page)
	if request.method == 'GET':
		session.pop('pending_2fa', None)
		session.pop('temp_username', None)
		session.pop('logged_in', None)

	if request.method == 'POST':
		username = request.form.get('username')
		password = request.form.get('password')
		twofa_code = request.form.get('2fa_code')
		
		admin_setup = db_manager.get_admin_setup()
		if not admin_setup:
			flash('System is not properly configured.', 'error')
			return redirect(url_for('setup'))

		# If we're in the 2FA verification step
		if session.get('pending_2fa') and twofa_code:
			if username != session.get('temp_username'):
				session.clear()
				flash('Invalid session. Please login again.', 'error')
				return redirect(url_for('login'))
				
			if admin_setup.get('two_fa_enabled') and admin_setup.get('two_fa_secret'):
				totp = pyotp.TOTP(admin_setup['two_fa_secret'])
				if totp.verify(twofa_code):
					session['logged_in'] = True
					session.pop('pending_2fa', None)
					session.pop('temp_username', None)
					flash('Login successful!', 'success')
					return redirect(url_for('admin'))
				else:
					flash('Invalid 2FA code!', 'error')
					return render_template('login.html', show_2fa=True)
			else:
				session.clear()
				flash('2FA is not properly configured. Please login again.', 'error')
				return redirect(url_for('login'))

		# First step of login - username/password verification
		if username and password:
			if username == admin_setup['username'] and check_password_hash(admin_setup['password'], password):
				# Check if 2FA is properly enabled and configured
				if admin_setup.get('two_fa_enabled') and admin_setup.get('two_fa_secret'):
					session['pending_2fa'] = True
					session['temp_username'] = username
					return render_template('login.html', show_2fa=True)
				else:
					# No 2FA needed, clear any 2FA session flags
					session.pop('pending_2fa', None)
					session.pop('temp_username', None)
					session['logged_in'] = True
					flash('Login successful!', 'success')
					return redirect(url_for('admin'))
			else:
				flash('Invalid credentials!', 'error')
		else:
			flash('Username and password are required!', 'error')

	# GET request or form validation failed
	show_2fa = session.get('pending_2fa', False)
	return render_template('login.html', show_2fa=show_2fa)

@app.route('/admin/2fa-setup', methods=['GET', 'POST'])
@login_required
def setup_2fa():
	admin_setup = db_manager.get_admin_setup()
	if not admin_setup:
		flash('System is not properly configured.', 'error')
		return redirect(url_for('setup'))

	# Use secret from session if present, else fallback to admin_setup (for legacy)
	two_fa_secret = session.get('pending_2fa_secret') or admin_setup.get('two_fa_secret')
	if not two_fa_secret:
		return redirect(url_for('enable_2fa'))

	# Handle POST: verify code or cancel 2FA
	if request.method == 'POST':
		if request.form.get('cancel_2fa') == '1':
			session.pop('pending_2fa_secret', None)
			flash('Two-factor authentication setup was canceled.', 'info')
			return redirect(url_for('login'))
		code = request.form.get('verify_2fa_code', '').strip()
		totp = pyotp.TOTP(two_fa_secret)
		if totp.verify(code):
			# Save 2FA secret and enable 2FA only after verification
			db_manager.complete_setup(
				username=admin_setup['username'],
				password=admin_setup['password'],
				email=admin_setup['email'],
				enable_2fa=True,
				two_fa_secret=two_fa_secret
			)
			session.pop('pending_2fa_secret', None)
			flash('Two-factor authentication is now active and verified!', 'success')
			return redirect(url_for('admin'))
		else:
			flash('Invalid code. Please try again.', 'danger')

	provisioning_uri = pyotp.totp.TOTP(two_fa_secret).provisioning_uri(
		name=admin_setup['email'],
		issuer_name='FlexaFlow CMS'
	)
	qr = pyqrcode.create(provisioning_uri)
	buffer = io.BytesIO()
	qr.svg(buffer, scale=5)
	qr_code = base64.b64encode(buffer.getvalue()).decode()

	return render_template(
		'setup_2fa.html',
		qr_code=qr_code,
		secret=two_fa_secret
	)
	

@app.route('/admin/disable-2fa')
@login_required
def disable_2fa():
	admin_setup = db_manager.get_admin_setup()
	if not admin_setup:
		flash('System is not properly configured.', 'error')
		return redirect(url_for('setup'))

	# Update the admin setup to disable 2FA
	success = db_manager.complete_setup(
		username=admin_setup['username'],
		password=admin_setup['password'],
		email=admin_setup['email'],
		enable_2fa=False,
		two_fa_secret=None
	)

	if success:
		flash('Two-factor authentication has been disabled.', 'success')
	else:
		flash('Failed to disable two-factor authentication.', 'error')

	return redirect(url_for('settings'))

@app.route('/admin/enable-2fa')
@login_required
def enable_2fa():
	admin_setup = db_manager.get_admin_setup()
	if not admin_setup:
		flash('System is not properly configured.', 'error')
		return redirect(url_for('setup'))

	# Generate new 2FA secret and store in session only
	two_fa_secret = pyotp.random_base32()
	session['pending_2fa_secret'] = two_fa_secret
	flash('Two-factor authentication setup started. Please scan the QR code and verify.', 'success')
	return redirect(url_for('setup_2fa'))

# Helper functions for database operations
def get_page_by_slug(slug: str):
	"""Get a page by slug from either published or draft pages"""
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
	"""Get a post by slug from either published or draft posts"""
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
	"""Delete a post by slug"""
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
			page.title = page_data.get('title', page.title)
			page.content = page_data.get('content', page.content)
			page.description = page_data.get('description', page.description)
			page.status = page_data.get('status', page.status)
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
	"""Update a post by slug"""
	db = db_manager.get_session()
	try:
		post = db.query(Post).filter(Post.slug == slug).first()
		if post:
			post.title = post_data.get('title', post.title)
			post.content = post_data.get('content', post.content)
			post.excerpt = post_data.get('excerpt', post.excerpt)
			post.status = post_data.get('status', post.status)
			post.updated_at = datetime.datetime.utcnow()
			
			# Handle category
			if 'category' in post_data:
				category = db.query(Category).filter(Category.slug == post_data['category']).first()
				post.category_id = category.id if category else None
			
			# Handle tags
			if 'tags' in post_data:
				# Clear existing tags
				post.tags.clear()
				# Add new tags
				for tag_name in post_data['tags']:
					tag = db.query(Tag).filter(Tag.name == tag_name).first()
					if not tag:
						tag = Tag(name=tag_name)
						db.add(tag)
					post.tags.append(tag)
			
			if post_data.get('status') == 'published' and not post.published_at:
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
	"""Add a new category to database with only a unique name"""
	db = db_manager.get_session()
	try:
		# Use the name as slug (lowercase, hyphens)
		slug = name.strip().lower().replace(' ', '-')
		# Check for existing category by name or slug
		existing = db.query(Category).filter((Category.name == name) | (Category.slug == slug)).first()
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

@app.context_processor
def inject_globals():
	data = {'year': datetime.datetime.now().year}
	for name, func in theme_functions.items():
		data[name] = func
	data['settings'] = get_site_settings()
	return data

@app.errorhandler(404)
def page_not_found(e):
	return render_template('404.html', page={'title': '404 Not Found'}), 404

@app.errorhandler(500)
def server_error(e):
	return render_template('404.html', page={'title': 'Error'}), 500

@app.route('/')
def home():
	# Check if system is configured, if not redirect to setup
	if not db_manager.is_setup_complete():
		return redirect(url_for('setup'))
		
	try:
		page = max(1, int(request.args.get("page", 1)))
		posts_per_page = 10
		
		published_posts = []
		posts = get_posts()
		
		if not isinstance(posts, dict):
			posts = {}
			
		for slug, post in posts.items():
			if post.get('status') == 'published':
				post_copy = post.copy()
				post_copy['slug'] = slug
				published_posts.append(post_copy)
		
		# Sort by created_at or updated_at
		published_posts.sort(key=lambda x: x.get('created_at', x.get('updated_at', '')), reverse=True)
		
		total_posts = len(published_posts)
		total_pages = max(1, (total_posts + posts_per_page - 1) // posts_per_page)
		
		page = min(page, total_pages)
		
		start = (page - 1) * posts_per_page
		end = start + posts_per_page
		current_posts = published_posts[start:end]
		
		pagination = {
			'current_page': page,
			'total_pages': total_pages,
			'pages': range(1, total_pages + 1),
			'prev_page': page - 1 if page > 1 else None,
			'next_page': page + 1 if page < total_pages else None
		}
		
		tags = get_tags()
		if not isinstance(tags, dict):
			tags = {}
		max_tag_count = max([tag.get('count', 0) for tag in tags.values()]) if tags else 1
		
		categories = get_categories()
		if not isinstance(categories, dict):
			categories = {}
		
		default_page = {
			'title': 'Home',
			'description': get_site_settings().get('site_description', 'Welcome to our website')
		}
		
		return render_template("main/master.html", 
						   page=default_page,
						   posts=current_posts,
						   pagination=pagination,
						   categories=categories,
						   tags=tags,
						   max_tag_count=max_tag_count,
						   error=None)
						   
	except Exception as e:
		default_page = {
			'title': 'Home',
			'description': get_site_settings().get('site_description', 'Welcome to our website')
		}
		
		return render_template("main/master.html",
						   page=default_page,
						   posts=[],
						   pagination={'current_page': 1, 'total_pages': 1, 'pages': [1], 'prev_page': None, 'next_page': None},
						   categories={},
						   tags={},
						   max_tag_count=1,
						   error=str(e))

@app.route('/admin')
@login_required
def admin():
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

@app.route('/page/add', methods=['GET', 'POST'])
def add_page_route():
	if request.method == 'POST':
		slug = request.form.get("slug")
		slug=slugify(slug)
		title = request.form.get("title")
		description = request.form.get("description") 
		content = request.form.get("content")
		status = request.form.get("status", "draft")
		
		page_data = {
			"content": content,
			"title": title, 
			"description": description,
			"status": status
		}
		
		if status == "published":
			success = add_page(slug, page_data)
		else:
			success = add_draft_page(slug, page_data)
			
		if success:
			flash('Page added successfully!', 'success')
		else:
			flash('Error adding page!', 'error')
			
		return redirect(url_for('admin'))
	
	return render_template("add_page.html")

@app.route('/page/edit/<slug>', methods=['GET', 'POST'])
def edit_page(slug):
	page = get_page_by_slug(slug)
		
	if not page:
		return "<h1>Page not found</h1>", 404
		
	if request.method == 'POST':
		title = request.form.get("title")
		description = request.form.get("description")
		content = request.form.get("content")
		status = request.form.get("status", "draft")
		
		page_data = {
			"content": content,
			"title": title,
			"description": description,
			"status": status
		}

		success = update_page_by_slug(slug, page_data)
		
		if success:
			flash('Page updated successfully!', 'success')
		else:
			flash('Error updating page!', 'error')
				
		return redirect(url_for('admin'))
		
	return render_template("edit_page.html", slug=slug, page=page)

@app.route('/page/preview/<slug>')
@login_required
def preview_page(slug):
	page = get_page_by_slug(slug)
		
	if page:
		theme = {
			'use_custom_css': False,
			'custom_css_url': '',
			'custom_styles': '',
			'body_class': '',
			'content_class': '',
			'dark_mode': False
		}
		return render_template("preview.html", page=page, theme=theme)
	else:
		return "<h1>Page not found</h1>", 404

@app.route('/page/preview_draft', methods=['POST'])
def preview_draft():
	title = request.form.get("title")
	description = request.form.get("description")
	content = request.form.get("content")
	page = {"title": title, "description": description, "content": content}
	return render_template("preview.html", page=page)

@app.route('/page/delete/<slug>')
@login_required
def delete_page_route(slug):
	success = delete_page(slug)
	if success:
		flash('Page deleted successfully!', 'success')
	else:
		flash('Error deleting page!', 'error')
	return redirect(url_for('admin'))





@app.route('/post/add', methods=['GET', 'POST'])
@login_required
def add_post_route():
	try:
		if request.method == 'POST':
			slug = request.form.get("slug", "").strip()
			slug=slugify(slug)
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
			
			post_tags = [tag.strip() for tag in tag_list.split(',') if tag.strip()]
			
			post_data = {
				"content": content,
				"title": title,
				"excerpt": description or title[:200],  # Use first 200 chars if no description
				"status": status,
				"category": category,
				"tags": post_tags
			}
			
			if status == "published":
				success = add_post(slug, post_data)
			else:
				success = add_draft_post(slug, post_data)
			
			if success:
				update_tag_counts()
				flash('Post added successfully!', 'success')
				return redirect(url_for('admin'))
			else:
				raise ValueError("Failed to save post")
			
		categories = get_categories()
		if not categories:
			categories = {}
		return render_template("add_post.html", categories=categories, error=None)
		
	except Exception as e:
		return render_template("add_post.html", 
							categories=get_categories(),
							error=str(e),
							form_data={
								'slug': request.form.get("slug", ""),
								'title': request.form.get("title", ""),
								'description': request.form.get("description", ""),
								'content': request.form.get("content", ""),
								'category': request.form.get("category", ""),
								'tags': request.form.get("tags", ""),
								'status': request.form.get("status", "draft")
							})

@app.route('/post/edit/<slug>', methods=['GET', 'POST'])
@login_required
def edit_post(slug):
	post = get_post_by_slug(slug)
		
	if not post:
		return "<h1>Post not found</h1>", 404
		
	if request.method == 'POST':
		title = request.form.get("title")
		description = request.form.get("description")
		content = request.form.get("content")
		category = request.form.get("category")
		tag_list = request.form.get("tags", "")
		status = request.form.get("status", "draft")
		
		post_tags = [tag.strip() for tag in tag_list.split(',') if tag.strip()]
		
		post_data = {
			"content": content,
			"title": title,
			"excerpt": description,
			"status": status,
			"category": category,
			"tags": post_tags
		}
		
		success = update_post_by_slug(slug, post_data)
		
		if success:
			update_tag_counts()
			flash('Post updated successfully!', 'success')
		else:
			flash('Error updating post!', 'error')
		
		return redirect(url_for('admin'))
	
	# Format tags for display
	if 'tags' in post and isinstance(post['tags'], list):
		post['tags_string'] = ', '.join(post['tags'])
	else:
		post['tags_string'] = ''
		
	return render_template("edit_post.html", slug=slug, post=post, categories=get_categories())

@app.route('/post/delete/<slug>')
def delete_post_route(slug):
	success = delete_post_by_slug(slug)
	if success:
		update_tag_counts()
		flash('Post deleted successfully!', 'success')
	else:
		flash('Error deleting post!', 'error')
	
	return redirect(url_for('admin'))

@app.route('/post/<slug>')
def view_post(slug):
	post = get_post_by_slug(slug)
	
	if not post:
		abort(404)
	
	# Only show published posts to public (unless in admin mode)
	if post.get('status') != 'published':
		# You can add admin authentication check here
		# For now, we'll show drafts too
		pass
	
	categories = get_categories()
	tags = get_tags()
	
	# Get related posts
	related_posts = []
	post_tags = post.get('tags', [])
	all_posts = get_posts()
	
	for other_slug, other_post in all_posts.items():
		if other_slug != slug and other_post.get('status') == 'published':
			other_tags = other_post.get('tags', [])
			if set(post_tags) & set(other_tags):  # If there are common tags
				other_post_copy = other_post.copy()
				other_post_copy['slug'] = other_slug
				related_posts.append(other_post_copy)
				if len(related_posts) >= 3:
					break
	
	# Add slug to post for template
	post['slug'] = slug
	
	return render_template("post.html", post=post, related_posts=related_posts, categories=categories, tags=tags)

@app.route('/category/<category_slug>')
def view_category(category_slug):
	categories = get_categories()
	if category_slug in categories:
		category_posts = []
		all_posts = get_posts()
		
		for slug, post in all_posts.items():
			if post.get('category', {}).get('slug') == category_slug and post.get('status') == 'published':
				post_copy = post.copy()
				post_copy['slug'] = slug
				category_posts.append(post_copy)
		
		category_data = categories[category_slug]
		category_data['count'] = len(category_posts)
				
		return render_template("category.html", 
							category=category_data,
							posts=category_posts)
	else:
		abort(404)

@app.route('/tag/<tag_name>')
def view_tag(tag_name):
	tagged_posts = []
	all_posts = get_posts()
	
	for slug, post in all_posts.items():
		if tag_name in post.get('tags', []) and post.get('status') == 'published':
			post_copy = post.copy()
			post_copy['slug'] = slug
			tagged_posts.append(post_copy)
			
	return render_template("tag.html", 
						tag={'name': tag_name, 'count': len(tagged_posts)},
						posts=tagged_posts)

@app.route('/search')
def search():
	query = request.args.get("q", "")
	search_results = []
	
	if query:
		all_posts = get_posts()
		for slug, post in all_posts.items():
			if post.get('status') == 'published':
				if (query.lower() in post.get('title', '').lower() or 
					query.lower() in post.get('content', '').lower() or
					query.lower() in post.get('excerpt', '').lower() or
					query.lower() in ','.join(post.get('tags', [])).lower()):
					post_copy = post.copy()
					post_copy['slug'] = slug
					search_results.append(post_copy)
					
	return render_template("search.html", 
						query=query,
						results=search_results)

@app.route('/admin/settings')
@login_required
def settings():
	site_settings = get_site_settings()
	admin_setup = db_manager.get_admin_setup()
	# Ensure two_fa_enabled reflects the admin's real 2FA status
	site_settings['two_fa_enabled'] = bool(admin_setup and admin_setup.get('two_fa_enabled'))
	return render_template("settings.html", 
						 settings=site_settings, 
						 pages=get_pages(),
						 custom_analytics=os.getenv('CUSTOM_ANALYTICS_SCRIPT', ''))

@app.route('/admin/menu')
@login_required
def menu_editor():
	settings = get_site_settings()
	menu_items = settings.get("menu_items", [])
	return render_template("menu-editor.html", menu_items=menu_items, pages=get_pages())

@app.route('/api/settings', methods=['GET', 'POST'])
@login_required
def api_settings():
	if request.method == 'GET':
		return jsonify(get_site_settings())
	elif request.method == 'POST':
		data = request.json
		success = update_site_settings(data)
		if success:
			return jsonify({"status": "success", "message": "Settings updated"})
		else:
			return jsonify({"status": "error", "message": "Failed to update settings"}), 500

@app.route('/api/menu', methods=['GET', 'POST'])
@login_required
def api_menu():
	if request.method == 'GET':
		settings = get_site_settings()
		return jsonify(settings.get("menu_items", []))
	elif request.method == 'POST':
		data = request.json
		settings = get_site_settings()
		settings["menu_items"] = data
		success = update_site_settings(settings)
		if success:
			return jsonify({"status": "success", "message": "Menu updated"})
		else:
			return jsonify({"status": "error", "message": "Failed to update menu"}), 500

@app.route('/admin/category/add', methods=['POST'])
@login_required
def add_category():
	name = request.form.get("name", "").strip()
	if not name:
		flash('Category name required!', 'error')
		return redirect(url_for('manage_tags_and_catagories'))
	success = add_category_to_db(name)
	if success:
		flash('Category added successfully!', 'success')
	else:
		flash('Error adding category! (Maybe already exists)', 'error')
	return redirect(url_for('manage_tags_and_catagories'))

@app.route('/admin/media')
@login_required
def media_library():
	page = max(1, int(request.args.get("page", 1)))
	search = request.args.get("search")
	media = db_manager.get_media_library(page=page, search=search)
	
	# Return JSON if requested
	if request.args.get("format") == "json":
		return jsonify(media)
		
	return render_template("media-library.html", media=media)

@app.route('/admin/media/upload', methods=['POST'])
@login_required
def upload_media():
	if 'files[]' not in request.files:
		return jsonify({'success': False, 'error': 'No file part'})
	
	files = request.files.getlist('files[]')
	results = []
	
	for file in files:
		if file.filename == '':
			continue
			
		if file:
			try:
				# Generate unique filename
				original_filename = secure_filename(file.filename)
				filename = str(uuid.uuid4()) + os.path.splitext(original_filename)[1]
				file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
				
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
					'filename': filename,
					'original_filename': original_filename,
					'mime_type': file.content_type,
					'file_size': file_size,
					'width': width,
					'height': height,
					'alt_text': os.path.splitext(original_filename)[0],
					'caption': '',
					'title': os.path.splitext(original_filename)[0],
					'description': '',
					'thumbnail': thumbnails.get('thumbnail')
				}
				
				result = db_manager.add_media(media_data)
				if result:
					result['thumbnails'] = thumbnails
					results.append({
						'success': True,
						'file': result
					})
				else:
					results.append({
						'success': False,
						'error': f'Failed to save {original_filename} to database'
					})
					
			except Exception as e:
				results.append({
					'success': False,
					'error': f'Error processing {file.filename}: {str(e)}'
				})
				
	return jsonify({
		'success': True,
		'files': results
	})

@app.context_processor
def inject_csrf_token():
	if 'csrf_token' not in session:
		session['csrf_token'] = secrets.token_hex(16)
	return {'csrf_token': session['csrf_token']}

def validate_csrf_token():
	token = request.headers.get('X-CSRF-Token')
	if not token:
		# Also check form data for CSRF token
		token = request.form.get('csrf_token')
	return token and token == session.get('csrf_token')

@app.route('/upload_image', methods=['POST'])
@login_required
def upload_image():
	try:
		# Check CSRF token from either header or form
		token = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token')
		if not token or token != session.get('csrf_token'):
			return jsonify({'error': 'Invalid CSRF token'}), 403

		if 'file' not in request.files:
			return jsonify({'error': 'No file part'}), 400
		
		file = request.files['file']
		if file.filename == '':
			return jsonify({'error': 'No selected file'}), 400
		
		if file and allowed_file(file.filename):
			original_filename = secure_filename(file.filename)
			filename = str(uuid.uuid4()) + os.path.splitext(original_filename)[1]
			file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
			
			# Ensure upload directory exists
			os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
			
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
					'filename': filename,
					'original_filename': original_filename,
					'mime_type': file.content_type,
					'file_size': file_size,
					'width': width,
					'height': height,
					'alt_text': os.path.splitext(original_filename)[0],
					'caption': '',
					'title': os.path.splitext(original_filename)[0],
					'description': '',
					'thumbnail': thumbnails.get('thumbnail')
				}
				
				# Add to media library
				result = db_manager.add_media(media_data)
				if result:
					return jsonify({
						'location': f"/uploads/{filename}",
						'success': True
					})
					
				return jsonify({'error': 'Failed to save to database'}), 500
				
			except Exception as e:
				# Clean up the file if there was an error
				try:
					os.remove(file_path)
				except:
					pass
				raise e
					
		return jsonify({'error': 'Invalid file type'}), 400
				
	except Exception as e:
		print(f"Upload error: {str(e)}")  # Add error logging
		return jsonify({'error': str(e)}), 500

def allowed_file(filename):
	"""Check if the file type is allowed"""
	ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
	return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/uploads/<path:filename>')
@login_required
def uploaded_file(filename):
	"""Serve uploaded files with proper mime type"""
	return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/<slug>')
def view_page(slug):
	page = get_page_by_slug(slug)
	if page and page.get('status') == 'published':
		return render_template("main/master.html", page=page)
	else:
		abort(404)

@app.route('/admin/media/<int:media_id>', methods=['GET', 'PUT', 'DELETE'])
@login_required
def manage_media(media_id):
	if request.method == 'GET':
		media = db_manager.get_media_by_id(media_id)
		if media:
			return jsonify(media)
		return jsonify({'error': 'Media not found'}), 404
		
	elif request.method == 'PUT':
		data = request.json
		success = db_manager.update_media(media_id, data)
		if success:
			return jsonify({'success': True})
		return jsonify({'success': False, 'error': 'Update failed'}), 500
		
	elif request.method == 'DELETE':
		success = db_manager.delete_media(media_id)
		if success:
			return jsonify({'success': True})
		return jsonify({'success': False, 'error': 'Delete failed'}), 500

@app.route('/admin/tags-and-catagories', methods=['GET', 'POST'])
@login_required
def manage_tags_and_catagories():
	categories = get_categories() or {}
	tags = get_tags() or {}
	unused_categories = {k: v for k, v in categories.items() if v.get('count', 0) == 0}
	unused_tags = {k: v for k, v in tags.items() if v.get('count', 0) == 0}
	return render_template('tags_and_catagories.html', 
						   categories=categories, tags=tags, 
						   unused_categories=unused_categories, unused_tags=unused_tags)

@app.route('/admin/category/delete/<slug>', methods=['POST'])
@login_required
def delete_category(slug):
	# Only allow deleting unused categories
	categories = get_categories() or {}
	if slug in categories and categories[slug].get('count', 0) == 0:
		db = db_manager.get_session()
		try:
			category = db.query(Category).filter(Category.slug == slug).first()
			if category:
				db.delete(category)
				db.commit()
				flash('Category deleted.', 'success')
			else:
				flash('Category not found.', 'error')
		except Exception as e:
			db.rollback()
			flash(f'Error deleting category: {e}', 'error')
		finally:
			db.close()
	else:
		flash('Cannot delete category in use.', 'error')
	return redirect(url_for('manage_tags_and_catagories'))

@app.route('/admin/tag/add', methods=['POST'])
@login_required
def add_tag():
	name = request.form.get('name', '').strip()
	if not name:
		flash('Tag name required.', 'error')
		return redirect(url_for('manage_tags_and_catagories'))
	db = db_manager.get_session()
	try:
		existing = db.query(Tag).filter(Tag.name == name).first()
		if existing:
			flash('Tag already exists.', 'error')
		else:
			tag = Tag(name=name)
			db.add(tag)
			db.commit()
			flash('Tag added.', 'success')
	except Exception as e:
		db.rollback()
		flash(f'Error adding tag: {e}', 'error')
	finally:
		db.close()
	return redirect(url_for('manage_tags_and_catagories'))

@app.route('/admin/tag/delete/<int:tag_id>', methods=['POST'])
@login_required
def delete_tag(tag_id):
	db = db_manager.get_session()
	try:
		tag = db.query(Tag).filter(Tag.id == tag_id).first()
		if tag and tag.count == 0:
			db.delete(tag)
			db.commit()
			flash('Tag deleted.', 'success')
		else:
			flash('Cannot delete tag in use or not found.', 'error')
	except Exception as e:
		db.rollback()
		flash(f'Error deleting tag: {e}', 'error')
	finally:
		db.close()
	return redirect(url_for('manage_tags_and_catagories'))

@app.route('/logout')
@login_required
def logout():
	session.clear()
	flash('You have been logged out.', 'info')
	return redirect(url_for('login'))

if __name__ == "__main__":
	# Get port from environment variable or default to 5000
	port = int(os.environ.get('PORT', 5000))
	
	# Enable debug mode in development
	debug = os.environ.get('FLASK_ENV', 'development') == 'development'
	
	# Run the application
	app.run(port=port, debug=debug)
	print(f"Server is running on http://127.0.0.1:{port}")
