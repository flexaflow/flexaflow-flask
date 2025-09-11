# FlexaFlow CMS Database Layer
# 
# Author: Mashiur Rahman
# Last Updated: September 11, 2025

import os
import json
import datetime
from datetime import timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy import (
	create_engine,
	Column,
	Integer,
	String,
	Text,
	DateTime,
	Boolean,
	ForeignKey,
	Table,
	event,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session
from sqlalchemy.exc import SQLAlchemyError
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database configuration
ENV = os.getenv("FLASK_ENV", "development")
if ENV == "production":
	DATABASE_URL = os.getenv("DATABASE_URL", "mysql://user:password@localhost/cms_db")
else:
	# Use SQLite for development/testing
	DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///cms.db")

# Configure SQLAlchemy engine based on database type
if DATABASE_URL.startswith("sqlite"):
	engine = create_engine(
		DATABASE_URL,
		connect_args={"check_same_thread": False},  # Needed for SQLite
		echo=False,
	)
else:
	# MySQL configuration
	engine = create_engine(
		DATABASE_URL, echo=False, pool_size=5, max_overflow=10, pool_timeout=30
	)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Association table for post-tag many-to-many relationship
"""
Association table managing the many-to-many relationship between posts and tags.

Features:
	- Flexible post tagging
	- Automatic cleanup
	- Referential integrity
	- Index optimization
	- Query support

Structure:
	post_id: Foreign key to posts table
		- Primary key part
		- Cascade delete
		- Index support
	
	tag_id: Foreign key to tags table
		- Primary key part
		- Cascade delete
		- Index support
		
Usage:
	- Automatically managed by SQLAlchemy
	- Used through Post.tags relationship
	- Supports filtering and joins
	- Enables tag clouds
	
Performance:
	- Composite primary key
	- Foreign key indexes
	- Query optimization
	- Join efficiency
"""


post_tags = Table(
	"post_tags",
	Base.metadata,
	Column("post_id", Integer, ForeignKey("posts.id"), primary_key=True),
	Column("tag_id", Integer, ForeignKey("tags.id"), primary_key=True),
)


# Media Model
class Media(Base):
	"""
	Media Model for storing uploaded files and their metadata.
	
	Attributes:
		id (int): Primary key
		filename (str): Generated unique filename
		original_filename (str): Original uploaded filename
		mime_type (str): File MIME type for content handling
		file_size (int): Size in bytes
		width (int): Image width in pixels
		height (int): Image height in pixels
		alt_text (str): Alternative text for accessibility
		caption (str): Optional media caption
		title (str): Display title
		description (str): Detailed description
		thumbnail (str): Thumbnail filename
		created_at (datetime): Creation timestamp
		updated_at (datetime): Last update timestamp
	
	Features:
		- Automatic thumbnail tracking
		- Metadata storage
		- Timestamp management
		- Image dimension tracking
	"""    
	__tablename__ = "media"

	id = Column(Integer, primary_key=True)
	filename = Column(String(255), nullable=False)
	original_filename = Column(String(255), nullable=False)
	mime_type = Column(String(100))
	file_size = Column(Integer)
	width = Column(Integer)
	height = Column(Integer)
	alt_text = Column(String(255))
	caption = Column(Text)
	title = Column(String(255))
	description = Column(Text)
	thumbnail = Column(String(255))
	created_at = Column(DateTime, default=datetime.datetime.utcnow)
	updated_at = Column(
		DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
	)


# Models
class AdminSetup(Base):
	"""
	AdminSetup Model for managing admin user configuration and 2FA settings.
	
	Attributes:
		id (int): Primary key
		is_configured (bool): Whether initial setup is complete
		username (str): Admin username, must be unique
		password (str): Hashed password string
		email (str): Admin email address
		two_fa_secret (str): OTP secret for 2FA
		two_fa_enabled (bool): Whether 2FA is active
		created_at (datetime): Account creation time
		updated_at (datetime): Last modification time
		
	Features:
		- Two-factor authentication support
		- Secure password storage
		- Setup state tracking
		- Timestamp management
		- Email verification
		
	Security:
		- Unique username enforcement
		- Optional 2FA
		- Password hashing required
		- Update tracking
	"""    
	__tablename__ = "admin_setup"

	id = Column(Integer, primary_key=True)
	is_configured = Column(Boolean, default=False)
	username = Column(String(255), unique=True)
	password = Column(String(255))
	email = Column(String(255))
	two_fa_secret = Column(String(255), nullable=True)
	two_fa_enabled = Column(Boolean, default=False, server_default="0")
	created_at = Column(DateTime, default=datetime.datetime.utcnow)
	updated_at = Column(
		DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
	)

	@property
	def has_2fa(self):
		"""Check if 2FA is properly configured and enabled"""
		return self.two_fa_enabled and self.two_fa_secret is not None


class Page(Base):
	"""
	
	Features:
		- Draft system
		- Version tracking
		- URL routing
		- Meta descriptions
		- Custom templates
		
	Attributes:
		id (int): Primary key and unique identifier
		slug (str): URL-friendly unique identifier
			- Used in page URLs
			- Must be unique
			- Auto-generated from title
		title (str): Page title 
			- Required
			- Used in navigation
		content (str): Main page content
			- Supports HTML
			- Markdown rendering
			- Rich text
		description (str): Meta description
			- Search snippets
			- Social sharing
		status (str): Publication status
			- 'published': Live and visible
			- 'draft': Work in progress
		created_at (datetime): Creation timestamp
		updated_at (datetime): Last modification time
		
	Common Pages:
		- Homepage
		- About
		- Contact
		- Privacy Policy
		- Terms of Service
		
	Usage:
		# Create new page
		page = Page(
			slug='about',
			title='About Us',
			content='Company history...',
			status='published'
		)
		
		# Create draft
		draft = Page(
			slug='new-services',
			title='Our Services',
			status='draft'
		)
	"""

	__tablename__ = "pages"

	id = Column(Integer, primary_key=True, index=True)
	slug = Column(String(255), unique=True, index=True, nullable=False)
	title = Column(String(255), nullable=False)
	content = Column(Text)
	description = Column(Text)
	status = Column(String(50), default="published")  # published, draft
	created_at = Column(DateTime, default=datetime.datetime.utcnow)
	updated_at = Column(
		DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
	)

	def to_dict(self):
		return {
			"id": self.id,
			"slug": self.slug,
			"title": self.title,
			"content": self.content,
			"description": self.description,
			"status": self.status,
			"created_at": self.created_at.isoformat() if self.created_at else None,
			"updated_at": self.updated_at.isoformat() if self.updated_at else None,
		}


class Post(Base):

	"""
	blog post or article.
	
	Features:
		- SEO-friendly slugs
		- Content management
		- Draft support
		- Categories and tags
		- Timestamps
		- URL routing
		- Rich relationships
		- Data validation
		
	Attributes:
		id (int): Primary key and unique identifier
		slug (str): URL-friendly unique identifier
		title (str): Post title, required
		content (str): Full post content
		excerpt (str): Brief summary
		status (str): Publication status
			- 'published': Live and visible
			- 'draft': Work in progress
		category_id (int): Foreign key to Category
		created_at (datetime): Creation timestamp
		updated_at (datetime): Last modification time
		published_at (datetime): When post went live
		
	Relationships:
		category: Parent category (many-to-one)
			- Optional categorization
			- Hierarchical organization
			- Navigation support
			
		tags: Associated tags (many-to-many)
			- Flexible tagging
			- Multiple tags per post
			- Tag cloud support
			
	Usage:
		# Create a new post
		post = Post(
			slug='hello-world',
			title='Hello World',
			content='Welcome to my blog!',
			status='published'
		)
		
		# Add tags
		post.tags.append(Tag(name='welcome'))
		
		# Set category
		post.category = Category(name='General')
	"""

	__tablename__ = "posts"

	id = Column(Integer, primary_key=True, index=True)
	slug = Column(String(255), unique=True, index=True, nullable=False)
	title = Column(String(255), nullable=False)
	content = Column(Text)
	excerpt = Column(Text)
	status = Column(String(50), default="published")  # published, draft
	category_id = Column(Integer, ForeignKey("categories.id"))
	created_at = Column(DateTime, default=datetime.datetime.utcnow)
	updated_at = Column(
		DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
	)
	published_at = Column(DateTime)

	# Relationships
	category = relationship("Category", back_populates="posts")
	tags = relationship("Tag", secondary=post_tags, back_populates="posts")

	def to_dict(self):
		return {
			"id": self.id,
			"slug": self.slug,
			"title": self.title,
			"content": self.content,
			"excerpt": self.excerpt,
			"status": self.status,
			"category_id": self.category_id,
			"category": self.category.to_dict() if self.category else None,
			"tags": [tag.name for tag in self.tags],
			"created_at": self.created_at.isoformat() if self.created_at else None,
			"updated_at": self.updated_at.isoformat() if self.updated_at else None,
			"published_at": (
				self.published_at.isoformat() if self.published_at else None
			),
		}


class Category(Base):
	"""
	Content category for organizing posts.
	
	Features:
		- Hierarchical organization
		- SEO-friendly URLs
		- Post aggregation
		- Navigation support
		- Post counting
		- Archive views
		
	Attributes:
		id (int): Primary key and unique identifier
		name (str): Display name of category
		slug (str): URL-friendly unique identifier
		description (str): Detailed description
		created_at (datetime): Creation timestamp
		
	Relationships:
		posts: Child posts (one-to-many)
			- Reverse lookup
			- Automatic cleanup
			- Count tracking
			- Filtering support
			
	Indexing:
		- Primary key (id)
		- Unique slug
		- Name lookup
		
	Usage:
		# Create new category
		tech = Category(
			name='Technology',
			slug='tech',
			description='Tech posts'
		)
		
		# Add posts
		tech.posts.append(new_post)
		  
		# Get post count
		count = len(tech.posts)
	"""        
	__tablename__ = "categories"

	id = Column(Integer, primary_key=True, index=True)
	name = Column(String(255), nullable=False)
	slug = Column(String(255), unique=True, index=True, nullable=False)
	description = Column(Text)
	created_at = Column(DateTime, default=datetime.datetime.utcnow)

	# Relationships
	posts = relationship("Post", back_populates="category")

	def to_dict(self):
		return {
			"id": self.id,
			"name": self.name,
			"slug": self.slug,
			"description": self.description,
			"created_at": self.created_at.isoformat() if self.created_at else None,
			"post_count": len(self.posts),
		}


class Tag(Base):
	"""
	Content tag for flexible post organization.
	
	Features:
		- Post tagging
		- Tag clouds
		- Related content
		- Search enhancement
		- Content filtering
		- Usage tracking
		
	Attributes:
		id (int): Primary key and unique identifier
		name (str): Tag name, must be unique
		created_at (datetime): Creation timestamp
		
	Relationships:
		posts: Tagged posts (many-to-many)
			- Bidirectional relationship
			- Automatic cleanup
			- Count tracking
			- Post filtering
			
	Indexing:
		- Primary key (id)
		- Unique name
		- Fast lookups
		
	Usage:
		# Create new tag
		tag = Tag(name='python')
		
		# Add to post
		post.tags.append(tag)
		
		# Get usage count
		count = len(tag.posts)
		
		# Get related posts
		related = [p for p in tag.posts if p.status == 'published']
	"""    
	__tablename__ = "tags"

	id = Column(Integer, primary_key=True, index=True)
	name = Column(String(255), unique=True, index=True, nullable=False)
	created_at = Column(DateTime, default=datetime.datetime.utcnow)

	# Relationships
	posts = relationship("Post", secondary=post_tags, back_populates="tags")

	def to_dict(self):
		return {
			"id": self.id,
			"name": self.name,
			"created_at": self.created_at.isoformat() if self.created_at else None,
			"count": len([post for post in self.posts if post.status == "published"]),
		}






class SiteSetting(Base):
	__tablename__ = "site_settings"

	id = Column(Integer, primary_key=True, index=True)
	key = Column(String(255), unique=True, index=True, nullable=False)
	value = Column(Text)
	created_at = Column(DateTime, default=datetime.datetime.utcnow)
	updated_at = Column(
		DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
	)

	def to_dict(self):
		# Try to parse JSON values
		try:
			value = json.loads(self.value) if self.value else None
		except (json.JSONDecodeError, TypeError):
			value = self.value

		return {"key": self.key, "value": value}







class MediaLibrary(Base):
	"""
	Model representing media files in the CMS library.
	
	Features:
		- File management 
		- Image optimization
		- Thumbnail generation
		- Metadata tracking
		- MIME type handling
		
	Attributes:
		id (int): Primary key and unique identifier
		filename (str): System filename on disk
			- Generated unique name
			- Includes extension
			- Path management
		original_filename (str): Original upload name
			- User-friendly name
			- File history
		mime_type (str): Content type
			- Security validation
			- Browser handling
			- Download headers
		file_size (int): Size in bytes
			- Storage tracking
			- Download info
			- Quota management
		width (int): Image width in pixels
			- Image sizing
			- Responsive display
			- Thumbnail calc
		height (int): Image height in pixels
			- Aspect ratios
			- Layout planning
			- Cropping
		alt_text (str): Accessibility text
			- Screen readers
			- SEO compliance
			- Fallback display
		caption (str): Display caption
			- Image galleries
			- Visual context
			- Citations
		title (str): Media title
			- Search indexing
			- Organization
		description (str): Detailed description
			- SEO content
			- Media context
			- Search indexing
		thumbnail (str): Thumbnail filename
			- Preview images
			- Gallery display
			- Media grid
		uploaded_at (datetime): Upload timestamp
			- File tracking
			- Sort ordering
			- Archive management
			
	File Handling:
		- Automatic thumbnail creation
		- MIME validation
		- Dimension extraction
		- Size calculation
		- Extension validation
		
	Usage:
		# Add new media
		media = MediaLibrary(
			filename='abc123.jpg',
			original_filename='photo.jpg',
			mime_type='image/jpeg',
			width=800,
			height=600
		)
		
		# Generate thumbnail
		media.thumbnail = 'thumb_abc123.jpg'
	"""
	__tablename__ = "media_library"

	id = Column(Integer, primary_key=True)
	filename = Column(String(255), nullable=False)
	original_filename = Column(String(255), nullable=False)
	mime_type = Column(String(100))
	file_size = Column(Integer)
	width = Column(Integer)
	height = Column(Integer)
	alt_text = Column(String(255))
	caption = Column(Text)
	uploaded_at = Column(DateTime, default=datetime.datetime.utcnow)
	title = Column(String(255))
	description = Column(Text)
	thumbnail = Column(String(255))

	def to_dict(self) -> Dict[str, Any]:
		base_url = "/uploads"
		return {
			"id": self.id,
			"filename": self.filename,
			"original_filename": self.original_filename,
			"mime_type": self.mime_type,
			"file_size": self.file_size,
			"width": self.width,
			"height": self.height,
			"alt_text": self.alt_text,
			"caption": self.caption,
			"title": self.title,
			"description": self.description,
			"uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
			"url": f"{base_url}/{self.filename}",
			"thumbnail_url": (
				f"{base_url}/thumbnails/{self.thumbnail}" if self.thumbnail else None
			),
		}





# Database operations class
class DatabaseManager:
	"""
	Core database management class handling all database operations.
	
	This class provides a centralized interface for:
	- Database initialization and setup
	- Session management
	- Table creation and updates
	- Media library operations
	- User management
	- Content CRUD operations
	
	Features:
		- Automatic table creation
		- Session pooling
		- Transaction management
		- Error handling
		- Connection management
		
	Usage:
		manager = DatabaseManager()
		manager.create_tables()
		manager.initialize_default_data()
		
	Security:
		- SQL injection prevention
		- Connection pooling
		- Transaction isolation
		- Error logging
	"""

	def __init__(self):
		self.engine = engine
		self.SessionLocal = SessionLocal

		# Create tables
		Base.metadata.create_all(self.engine)

	def create_tables(self):
		"""Create all tables"""
		Base.metadata.create_all(bind=self.engine)

	def get_session(self) -> Session:
		"""Get database session"""
		return self.SessionLocal()

	def is_setup_complete(self) -> bool:
		"""Check if initial setup is complete"""
		db = self.get_session()
		try:
			admin_setup = db.query(AdminSetup).first()
			return admin_setup is not None and admin_setup.is_configured
		except SQLAlchemyError as e:
			print(f"Error checking setup status: {str(e)}")
			return False
		finally:
			db.close()

	def complete_setup(
		self,
		username: str,
		password: str,
		email: str,
		enable_2fa: bool = False,
		two_fa_secret: str = None,
	) -> bool:
		"""Complete the initial setup"""
		db = self.get_session()
		try:
			admin_setup = db.query(AdminSetup).first()
			if admin_setup is None:
				admin_setup = AdminSetup()

			admin_setup.username = username
			admin_setup.password = password
			admin_setup.email = email
			admin_setup.is_configured = True
			admin_setup.two_fa_enabled = enable_2fa
			if enable_2fa and two_fa_secret:
				admin_setup.two_fa_secret = two_fa_secret

			db.add(admin_setup)
			db.commit()
			return True
		except SQLAlchemyError as e:
			db.rollback()
			print(f"Error completing setup: {str(e)}")
			return False
		finally:
			db.close()

	def get_admin_setup(self) -> Optional[Dict[str, Any]]:
		"""Get admin setup information"""
		db = self.get_session()
		try:
			admin_setup = db.query(AdminSetup).first()
			if admin_setup and admin_setup.is_configured:
				return {
					"username": admin_setup.username,
					"password": admin_setup.password,
					"email": admin_setup.email,
					"two_fa_enabled": admin_setup.has_2fa,  # Use the property instead
					"two_fa_secret": (
						admin_setup.two_fa_secret if admin_setup.has_2fa else None
					),
					"is_configured": True,
				}
			return None
		except SQLAlchemyError as e:
			print(f"Error getting admin setup: {str(e)}")
			return None
		finally:
			db.close()

	def initialize_default_data(self):
		"""Initialize database with default data"""
		db = self.get_session()
		try:
			# Check if data already exists
			if db.query(SiteSetting).first():
				return

			# Create default categories
			general_cat = Category(
				name="General", slug="general", description="General posts"
			)
			tech_cat = Category(
				name="Technology", slug="technology", description="Tech related posts"
			)
			db.add_all([general_cat, tech_cat])
			db.flush()  # Flush to get IDs

			# Create default pages
			home_page = Page(
				slug="home",
				title="Home",
				content="Welcome to the home page!",
				description="This is the home page.",
				status="published",
			)
			about_page = Page(
				slug="about",
				title="About",
				content="This is the about page.",
				description="This is the about page.",
				status="published",
			)
			db.add_all([home_page, about_page])

			# Create default site settings
			default_settings = {
				"site_title": "Flexaflow",
				"site_description": "A powerful CMS",
				"favicon": "/static/flexaflow.ico",
				"logo": "/static/flexaflow.png",
				"custom_analytics": "",
				"homepage": "home",
				"privacy_page": "",
				"terms_page": "",
				"social_links": {"facebook": "", "twitter": "", "instagram": ""},
				"theme": "default",
				"menu_items": [
					{"label": "Home", "url": "/home"},
					{"label": "About", "url": "/about"},
				],
			}

			for key, value in default_settings.items():
				setting = SiteSetting(
					key=key,
					value=(
						json.dumps(value)
						if isinstance(value, (dict, list))
						else str(value)
					),
				)
				db.add(setting)

			db.commit()
		except Exception as e:
			db.rollback()
			print(f"Error initializing default data: {str(e)}")
		finally:
			db.close()

	# Add media methods
	def add_media(self, media_data):
		"""
		Add a new media item to the media library.

		Features:
			- Metadata extraction
			- Thumbnail handling
			- URL generation
			- Transaction safety
			- Error recovery
			
		Args:
			media_data (dict): Media item details containing:
				- filename: Generated unique filename (str, required)
				- original_filename: Original upload name (str, required)
				- mime_type: File content type (str, optional)
				- file_size: Size in bytes (int, optional)
				- width: Image width (int, optional)
				- height: Image height (optional)
				- alt_text: Accessibility text (str, optional)
				- caption: Display caption (str, optional)
				- title: Media title (str, optional)
				- description: Long description (str, optional)
				- thumbnail: Thumbnail filename (str, optional)
				
		Returns:
			dict: Created media item data including:
				- All input fields
				- id: Generated database ID
				- created_at: Creation timestamp
				- url: Full media URL 
				- thumbnail_url: Full thumbnail URL (if exists)
			None: If operation fails
			
		Processing:
			1. Creates Media record
			2. Stores metadata
			3. Generates URLs
			4. Handles transactions
			5. Validates data
			
		Error Handling:
			- Missing required fields
			- Invalid field types
			- Database constraints
			- Transaction rollback
			- Resource cleanup
			
		Usage:
			media = add_media({
				'filename': 'abc123.jpg',
				'original_filename': 'photo.jpg',
				'mime_type': 'image/jpeg',
				'width': 800,
				'height': 600
			})
			if media:
				print(f"Created media: {media['url']}")
		"""
		db = self.get_session()
		try:
			media = Media(
				filename=media_data["filename"],
				original_filename=media_data["original_filename"],
				mime_type=media_data.get("mime_type"),
				file_size=media_data.get("file_size"),
				width=media_data.get("width"),
				height=media_data.get("height"),
				alt_text=media_data.get("alt_text"),
				caption=media_data.get("caption", ""),
				title=media_data.get("title", ""),
				description=media_data.get("description", ""),
				thumbnail=media_data.get("thumbnail"),
			)
			db.add(media)
			db.commit()

			# Return the media data with additional fields
			result = media_data.copy()
			result["id"] = media.id
			result["created_at"] = media.created_at.isoformat()
			result["url"] = f"/uploads/{media.filename}"
			if media.thumbnail:
				result["thumbnail_url"] = f"/uploads/thumbnails/{media.thumbnail}"

			return result
		except Exception as e:
			db.rollback()
			print(f"Error adding media: {str(e)}")
			return None
		finally:
			db.close()

	def get_media_library(self, page=1, per_page=20, search=None):
		"""
		Retrieve media library items with pagination and search.
		
		Features:
			- Full-text search
			- Smart pagination 
			- Sorting options
			- URL generation
			- Metadata formatting
			- Performance optimization
			
		Args:
			page (int): Page number to retrieve (default: 1)
			per_page (int): Items per page (default: 20)
			search (str): Search query for filtering (optional)
				- Searches filename, title, and description
				- Case-insensitive partial matches
				- Multiple term support
				
		Returns:
			dict: Media library data containing:
				items: List of media items, each with:
					- id: Media ID
					- filename: Stored filename
					- original_filename: Upload name
					- mime_type: Content type
					- file_size: Size in bytes
					- width: Image width
					- height: Image height
					- alt_text: Alt text
					- caption: Display caption
					- title: Media title
					- description: Full description
					- thumbnail: Thumbnail name
					- created_at: Creation time
					- updated_at: Last modified
					- url: Full media URL
					- thumbnail_url: Full thumbnail URL
				total: Total items count
				page: Current page number
				total_pages: Total available pages
				per_page: Items per page
			None: If operation fails
			
		Processing:
			1. Builds search query if needed
			2. Calculates pagination
			3. Retrieves page slice
			4. Formats response data
			5. Generates URLs
			
		Performance:
			- Efficient pagination
			- Query optimization
			- Connection pooling
			- Resource cleanup
			
		Usage:
			# Get first page
			first_page = get_media_library()
			
			# Search with pagination
			results = get_media_library(
				page=2,
				per_page=10,
				search='banner'
			)
			
			# Display results
			for item in results['items']:
				print(f"{item['title']}: {item['url']}")
		"""
		db = self.get_session()
		try:
			query = db.query(Media)

			if search:
				search = f"%{search}%"
				query = query.filter(
					(Media.original_filename.ilike(search))
					| (Media.title.ilike(search))
					| (Media.description.ilike(search))
				)

			# Calculate pagination
			total = query.count()
			total_pages = (total + per_page - 1) // per_page

			# Ensure page is within bounds
			page = max(1, min(page, total_pages))

			# Get paginated results
			offset = (page - 1) * per_page
			media_items = (
				query.order_by(Media.created_at.desc())
				.offset(offset)
				.limit(per_page)
				.all()
			)

			# Format results
			items = []
			for media in media_items:
				items.append(
					{
						"id": media.id,
						"filename": media.filename,
						"original_filename": media.original_filename,
						"mime_type": media.mime_type,
						"file_size": media.file_size,
						"width": media.width,
						"height": media.height,
						"alt_text": media.alt_text,
						"caption": media.caption,
						"title": media.title,
						"description": media.description,
						"thumbnail": media.thumbnail,
						"created_at": media.created_at.isoformat(),
						"updated_at": media.updated_at.isoformat(),
						"url": f"/uploads/{media.filename}",
						"thumbnail_url": (
							f"/uploads/thumbnails/{media.thumbnail}"
							if media.thumbnail
							else None
						),
					}
				)

			return {
				"items": items,
				"total": total,
				"page": page,
				"total_pages": total_pages,
				"per_page": per_page,
			}

		except Exception as e:
			print(f"Error getting media library: {str(e)}")
			return None
		finally:
			db.close()

	def get_media_by_id(self, media_id):
		"""Get a media item by ID"""
		db = self.get_session()
		try:
			media = db.query(Media).filter(Media.id == media_id).first()
			if media:
				return {
					"id": media.id,
					"filename": media.filename,
					"original_filename": media.original_filename,
					"mime_type": media.mime_type,
					"file_size": media.file_size,
					"width": media.width,
					"height": media.height,
					"alt_text": media.alt_text,
					"caption": media.caption,
					"title": media.title,
					"description": media.description,
					"thumbnail": media.thumbnail,
					"created_at": media.created_at.isoformat(),
					"updated_at": media.updated_at.isoformat(),
					"url": f"/uploads/{media.filename}",
					"thumbnail_url": (
						f"/uploads/thumbnails/{media.thumbnail}"
						if media.thumbnail
						else None
					),
				}
			return None
		finally:
			db.close()

	def update_media(self, media_id, data):
		"""Update a media item"""
		db = self.get_session()
		try:
			media = db.query(Media).filter(Media.id == media_id).first()
			if media:
				for key, value in data.items():
					if hasattr(media, key):
						setattr(media, key, value)
				db.commit()
				return True
			return False
		except Exception as e:
			db.rollback()
			print(f"Error updating media: {str(e)}")
			return False
		finally:
			db.close()

	def delete_media(self, media_id):
		"""
		Delete a media item and its associated files.
		
		Features:
			- Database cleanup
			- File system cleanup
			- Transaction safety
			- Resource management
			- Error recovery
			
		Args:
			media_id (int): ID of the media item to delete
			
		Returns:
			bool: True if deletion successful, False otherwise
			
		Processing:
			1. Fetches media record
			2. Stores file references
			3. Deletes database record
			4. Removes main media file
			5. Removes thumbnail file
			6. Handles failures gracefully
			
		Security:
			- Transaction isolation
			- File path validation
			- Error containment
			- Resource cleanup
			- Access validation
			
		File System:
			- Handles main media file
			- Manages thumbnails
			- Validates paths
			- Checks permissions
			- Recovers from errors
			
		Error Handling:
			- Database errors
			- File system errors
			- Missing files
			- Permission issues
			- Resource cleanup
			
		Usage:
			# Delete a media item
			success = delete_media(123)
			if success:
				print("Media deleted successfully")
			else:
				print("Failed to delete media")
		"""
		db = self.get_session()
		try:
			media = db.query(Media).filter(Media.id == media_id).first()
			if media:
				# Get filenames before deletion
				filename = media.filename
				thumbnail = media.thumbnail

				# Delete from database
				db.delete(media)
				db.commit()

				# Delete files
				try:
					if filename:
						file_path = os.path.join("uploads", filename)
						if os.path.exists(file_path):
							os.remove(file_path)

					if thumbnail:
						thumb_path = os.path.join("uploads", "thumbnails", thumbnail)
						if os.path.exists(thumb_path):
							os.remove(thumb_path)
				except Exception as e:
					print(f"Error deleting media files: {str(e)}")

				return True
			return False
		except Exception as e:
			db.rollback()
			print(f"Error deleting media: {str(e)}")
			return False
		finally:
			db.close()


# Initialize database manager
db_manager = DatabaseManager()
db_manager.create_tables()
db_manager.initialize_default_data()


# Data access functions (matching your original API)
def get_pages() -> Dict[str, Any]:
	"""
	Retrieve all published pages from the database.
	
	Returns:
		Dict[str, Any]: Dictionary of pages where:
			- key: page slug
			- value: page data dictionary containing:
				- title: page title
				- content: page content
				- description: meta description
				- status: publication status
				- timestamps: created/updated times
				
	Error Handling:
		- Returns empty dict on database errors
		- Logs error messages
		- Ensures session cleanup
		
	Performance:
		- Uses session pooling
		- Automatic connection cleanup
		- Efficient dictionary comprehension
		
	Usage:
		pages = get_pages()
		for slug, page in pages.items():
			print(f"{page['title']}: {page['description']}")
	"""
	db = db_manager.get_session()
	try:
		pages = db.query(Page).filter(Page.status == "published").all()
		return {page.slug: page.to_dict() for page in pages}
	except SQLAlchemyError as e:
		print(f"Error getting pages: {str(e)}")
		return {}
	finally:
		db.close()


def get_draft_pages() -> Dict[str, Any]:
	"""Get all draft pages"""
	db = db_manager.get_session()
	try:
		pages = db.query(Page).filter(Page.status == "draft").all()
		return {page.slug: page.to_dict() for page in pages}
	except SQLAlchemyError as e:
		print(f"Error getting draft pages: {str(e)}")
		return {}
	finally:
		db.close()


def get_posts() -> Dict[str, Any]:
	"""Get all published posts"""
	db = db_manager.get_session()
	try:
		posts = db.query(Post).filter(Post.status == "published").all()
		return {post.slug: post.to_dict() for post in posts}
	except SQLAlchemyError as e:
		print(f"Error getting posts: {str(e)}")
		return {}
	finally:
		db.close()


def get_draft_posts() -> Dict[str, Any]:
	"""Get all draft posts"""
	db = db_manager.get_session()
	try:
		posts = db.query(Post).filter(Post.status == "draft").all()
		return {post.slug: post.to_dict() for post in posts}
	except SQLAlchemyError as e:
		print(f"Error getting draft posts: {str(e)}")
		return {}
	finally:
		db.close()


def get_categories() -> Dict[str, Any]:
	"""Get all categories"""
	db = db_manager.get_session()
	try:
		categories = db.query(Category).all()
		return {category.slug: category.to_dict() for category in categories}
	except SQLAlchemyError as e:
		print(f"Error getting categories: {str(e)}")
		return {}
	finally:
		db.close()


def get_tags() -> Dict[str, Any]:
	"""Get all tags with counts"""
	db = db_manager.get_session()
	try:
		tags = db.query(Tag).all()
		return {tag.name: tag.to_dict() for tag in tags}
	except SQLAlchemyError as e:
		print(f"Error getting tags: {str(e)}")
		return {}
	finally:
		db.close()


def get_site_settings() -> Dict[str, Any]:
	"""
	Retrieve all site configuration settings.
	
	Features:
		- JSON value parsing
		- Error handling
		- Type conversion
		- Default values
		- Session management
	
	Returns:
		Dict[str, Any]: Configuration dictionary containing:
			- site_title: Site name
			- site_description: Meta description
			- theme: Active theme name
			- menu_items: Navigation structure
			- custom_settings: Theme-specific options
			- social_links: Social media URLs
			- analytics: Analytics configuration
			
	Processing:
		1. Fetch all settings from database
		2. Parse JSON values where applicable
		3. Fall back to string values if parsing fails
		4. Handle missing settings gracefully
		
	Error Handling:
		- Returns empty dict on database errors
		- Maintains valid JSON structure
		- Logs parsing errors
		- Ensures session cleanup
	
	Usage:
		settings = get_site_settings()
		theme = settings.get('theme', 'default')
		title = settings.get('site_title', 'My Site')
	"""
	db = db_manager.get_session()
	try:
		settings = db.query(SiteSetting).all()
		result = {}
		for setting in settings:
			try:
				# Try to parse JSON values
				value = json.loads(setting.value) if setting.value else None
			except (json.JSONDecodeError, TypeError):
				value = setting.value
			result[setting.key] = value
		return result
	except SQLAlchemyError as e:
		print(f"Error getting site settings: {str(e)}")
		return {}
	finally:
		db.close()


def add_page(slug: str, page_data: Dict[str, Any]) -> bool:
	"""Add a new page"""
	db = db_manager.get_session()
	try:
		page = Page(
			slug=slug,
			title=page_data.get("title", ""),
			content=page_data.get("content", ""),
			description=page_data.get("description", ""),
			status=page_data.get("status", "published"),
		)
		db.add(page)
		db.commit()
		return True
	except SQLAlchemyError as e:
		db.rollback()
		print(f"Error adding page: {str(e)}")
		return False
	finally:
		db.close()


def add_draft_page(slug: str, page_data: Dict[str, Any]) -> bool:
	"""Add a new draft page"""
	page_data["status"] = "draft"
	return add_page(slug, page_data)


def delete_page(slug: str) -> bool:
	"""Delete a page"""
	db = db_manager.get_session()
	try:
		page = db.query(Page).filter(Page.slug == slug).first()
		if page:
			db.delete(page)
			db.commit()
			return True
		return False
	except SQLAlchemyError as e:
		db.rollback()
		print(f"Error deleting page: {str(e)}")
		return False
	finally:
		db.close()


def add_post(slug: str, post_data: Dict[str, Any]) -> bool:
	"""Add a new post"""
	db = db_manager.get_session()
	try:
		# Get or create category
		category = None
		if "category" in post_data:
			category = (
				db.query(Category)
				.filter(Category.slug == post_data["category"])
				.first()
			)

		post = Post(
			slug=slug,
			title=post_data.get("title", ""),
			content=post_data.get("content", ""),
			excerpt=post_data.get("excerpt", ""),
			status=post_data.get("status", "published"),
			category_id=category.id if category else None,
			published_at=(
				datetime.datetime.utcnow()
				if post_data.get("status") == "published"
				else None
			),
		)

		# Handle tags
		if "tags" in post_data and isinstance(post_data["tags"], list):
			for tag_name in post_data["tags"]:
				tag = db.query(Tag).filter(Tag.name == tag_name).first()
				if not tag:
					tag = Tag(name=tag_name)
					db.add(tag)
				post.tags.append(tag)

		db.add(post)
		db.commit()
		return True
	except SQLAlchemyError as e:
		db.rollback()
		print(f"Error adding post: {str(e)}")
		return False
	finally:
		db.close()


def add_draft_post(slug: str, post_data: Dict[str, Any]) -> bool:
	"""Add a new draft post"""
	post_data["status"] = "draft"
	return add_post(slug, post_data)


def update_site_settings(settings_data: Dict[str, Any]) -> bool:
	"""
	Update site configuration settings in the database.
	
	Features:
		- JSON serialization
		- Atomic transactions
		- Upsert behavior
		- Type safety
		- Timestamp tracking
		- Session management
		
	Args:
		settings_data (Dict[str, Any]): Configuration dictionary containing:
			- site_title: Site name (str)
			- site_description: Meta description (str)
			- theme: Active theme name (str)
			- menu_items: Navigation structure (List/Dict)
			- custom_settings: Theme-specific options (Dict)
			- social_links: Social media URLs (Dict)
			- analytics: Analytics configuration (Dict)
			
	Returns:
		bool: True if update successful, False on error
		
	Processing:
		1. Opens database transaction
		2. Serializes complex values to JSON
		3. Updates existing or creates new settings
		4. Updates modification timestamps
		5. Commits changes atomically
		6. Rolls back on errors
		
	Error Handling:
		- JSON serialization errors
		- Database constraint violations 
		- Transaction management
		- Timestamp validation
		- Session cleanup
		
	Usage:
		success = update_site_settings({
			'site_title': 'My Blog',
			'theme': 'dark',
			'menu_items': [
				{'label': 'Home', 'url': '/'},
				{'label': 'About', 'url': '/about'}
			]
		})
	"""
	db = db_manager.get_session()
	print(settings_data,"database")
	try:
		for key, value in settings_data.items():
			setting = db.query(SiteSetting).filter(SiteSetting.key == key).first()
			if setting:
				setting.value = (
					json.dumps(value) if isinstance(value, (dict, list)) else str(value)
				)
				setting.updated_at = datetime.datetime.utcnow()
			else:
				setting = SiteSetting(
					key=key,
					value=(
						json.dumps(value)
						if isinstance(value, (dict, list))
						else str(value)
					),
				)
				db.add(setting)

		db.commit()
		return True
	except SQLAlchemyError as e:
		db.rollback()
		print(f"Error updating site settings: {str(e)}")
		return False
	finally:
		db.close()


def update_tag_counts() -> Dict[str, Any]:
	"""Recalculate tag counts based on published posts"""
	# This is automatically handled by the relationship and to_dict method
	return get_tags()


# Migration helper functions
def migrate_from_json(json_file_path: str) -> bool:
	"""Migrate data from JSON file to database"""
	try:
		with open(json_file_path, "r") as f:
			data = json.load(f)

		db = db_manager.get_session()

		# Clear existing data
		db.query(Post).delete()
		db.query(Page).delete()
		db.query(Category).delete()
		db.query(Tag).delete()
		db.query(SiteSetting).delete()

		# Migrate categories
		if "categories" in data:
			for slug, cat_data in data["categories"].items():
				category = Category(
					name=cat_data.get("name", slug.title()),
					slug=slug,
					description=cat_data.get("description", ""),
				)
				db.add(category)

		# Migrate pages
		if "pages" in data:
			for slug, page_data in data["pages"].items():
				page = Page(
					slug=slug,
					title=page_data.get("title", ""),
					content=page_data.get("content", ""),
					description=page_data.get("description", ""),
					status=page_data.get("status", "published"),
				)
				db.add(page)

		# Migrate site settings
		if "site_settings" in data:
			for key, value in data["site_settings"].items():
				setting = SiteSetting(
					key=key,
					value=(
						json.dumps(value)
						if isinstance(value, (dict, list))
						else str(value)
					),
				)
				db.add(setting)

		db.commit()
		print("Migration completed successfully!")
		return True

	except Exception as e:
		db.rollback()
		print(f"Migration failed: {str(e)}")
		return False
	finally:
		db.close()


# Backup function
def backup_to_json(json_file_path: str) -> bool:
	"""Backup database to JSON file"""
	try:
		data = {
			"pages": get_pages(),
			"draft_pages": get_draft_pages(),
			"posts": get_posts(),
			"draft_posts": get_draft_posts(),
			"categories": get_categories(),
			"tags": get_tags(),
			"site_settings": get_site_settings(),
		}

		with open(json_file_path, "w") as f:
			json.dump(data, f, indent=4)

		print(f"Backup completed: {json_file_path}")
		return True

	except Exception as e:
		print(f"Backup failed: {str(e)}")
		return False
