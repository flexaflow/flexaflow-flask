
import os
import logging
import re
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
	func,
	inspect,
	select,
	text,
	or_,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session
from sqlalchemy.exc import SQLAlchemyError
from dotenv import load_dotenv
from crypto_utils import EncryptedString, encrypt_secret, is_encrypted

load_dotenv()

ENV = os.getenv("FLASK_ENV", "development")
if ENV == "production":
	DATABASE_URL = os.getenv("DATABASE_URL", "mysql://user:password@localhost/cms_db")
else:
	DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///cms.db")

if DATABASE_URL.startswith("sqlite"):
	engine = create_engine(
		DATABASE_URL,
		connect_args={"check_same_thread": False},
		echo=False,
	)
else:
	engine = create_engine(
		DATABASE_URL, echo=False, pool_size=5, max_overflow=10, pool_timeout=30
	)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

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


logger = logging.getLogger(__name__)


def decode_fields(raw: Optional[str]) -> Dict[str, Any]:
	"""Decode the JSON custom-field blob stored on a page (always returns a dict)."""
	if not raw:
		return {}
	try:
		value = json.loads(raw)
		return value if isinstance(value, dict) else {}
	except (TypeError, ValueError):
		return {}


def decode_json_list(raw: Optional[str]) -> List[Any]:
	"""Decode a JSON list stored in a text column (always returns a list)."""
	if not raw:
		return []
	try:
		value = json.loads(raw)
		return value if isinstance(value, list) else []
	except (TypeError, ValueError):
		return []


class Sitemap(Base):
	"""
	Model for storing the generated XML sitemap.
	
	This table is designed to be a singleton (hold only one entry).
	
	Attributes:
		id (int): Primary key.
		content (str): The full XML content of the sitemap.
		updated_at (datetime): Timestamp of the last update.
	"""
	__tablename__ = "sitemaps"

	id = Column(Integer, primary_key=True)
	content = Column(Text, nullable=False)
	updated_at = Column(
		DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
	)


class Media(Base):
	"""Media Model for storing uploaded files and their metadata.  Attributes: id (int): Primary key filename (str): Generated unique filename original_filename (str): Original uploaded filename mime_type (str): File MIME type for content handling file_size (int): Size in bytes width (int): Image width..."""
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


class MediaGroup(Base):
	"""A named group of media items (e.g. an event). An item can belong to several groups.

	Deleting a group only removes its memberships; the images themselves are kept.
	"""
	__tablename__ = "media_groups"

	id = Column(Integer, primary_key=True)
	name = Column(String(120), nullable=False, unique=True)
	description = Column(Text)
	created_at = Column(DateTime, default=datetime.datetime.utcnow)
	updated_at = Column(
		DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
	)


class MediaGroupItem(Base):
	"""Membership row linking one media item to one group."""
	__tablename__ = "media_group_items"

	group_id = Column(Integer, ForeignKey("media_groups.id", ondelete="CASCADE"), primary_key=True)
	media_id = Column(Integer, ForeignKey("media.id", ondelete="CASCADE"), primary_key=True)
	added_at = Column(DateTime, default=datetime.datetime.utcnow)


ROLE_ADMINISTRATOR = "administrator"
ROLE_EDITOR = "editor"
ROLE_AUTHOR = "author"
ROLE_CONTRIBUTOR = "contributor"
ROLES = (ROLE_ADMINISTRATOR, ROLE_EDITOR, ROLE_AUTHOR, ROLE_CONTRIBUTOR)


class User(Base):
	"""
	A CMS user account. Several users can exist, each with a role that
	controls what they may do (modelled on WordPress roles):

		administrator - everything, including users and site settings
		editor        - manage all pages, posts, taxonomy and media
		author        - write and publish their own posts
		contributor   - write their own drafts only (cannot publish)

	Accounts are created from the command line (``python manage.py
	createsuperuser`` / ``createuser``) or by an administrator in the web UI.
	"""

	__tablename__ = "users"

	id = Column(Integer, primary_key=True)
	username = Column(String(64), unique=True, nullable=False, index=True)
	email = Column(String(190), unique=True, nullable=False)
	display_name = Column(String(150), nullable=True)
	password_hash = Column(String(255), nullable=False)
	role = Column(String(20), nullable=False, default=ROLE_CONTRIBUTOR)
	is_active = Column(Boolean, nullable=False, default=True)
	two_fa_secret = Column(EncryptedString(512), nullable=True)
	two_fa_enabled = Column(Boolean, nullable=False, default=False)
	two_fa_recovery = Column(Text, nullable=True)  # JSON list of password hashes
	session_version = Column(Integer, nullable=False, default=0, server_default="0")
	last_login_at = Column(DateTime, nullable=True)
	created_at = Column(DateTime, default=datetime.datetime.utcnow)
	updated_at = Column(
		DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
	)

	def to_dict(self, include_secrets: bool = False) -> Dict[str, Any]:
		data = {
			"id": self.id,
			"username": self.username,
			"email": self.email,
			"display_name": self.display_name or self.username,
			"role": self.role,
			"is_active": bool(self.is_active),
			"two_fa_enabled": bool(self.two_fa_enabled and self.two_fa_secret),
			"session_version": self.session_version or 0,
			"last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
			"created_at": self.created_at.isoformat() if self.created_at else None,
		}
		if include_secrets:
			data["password_hash"] = self.password_hash
			data["two_fa_secret"] = self.two_fa_secret
			data["two_fa_recovery"] = self.two_fa_recovery
		return data


class LoginAttempt(Base):
	"""One failed login or 2FA attempt. Stored in the database so limits survive restarts and multiple workers."""

	__tablename__ = "login_attempts"

	id = Column(Integer, primary_key=True)
	kind = Column(String(10), nullable=False, index=True)  # "login" or "totp"
	key = Column(String(255), nullable=False, index=True)  # "ip:<addr>", "user:<name>|<addr>", "totp:<user id>"
	created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)


class Revision(Base):
	"""A saved copy of a page or post, taken before each edit (and by editor autosave)."""

	__tablename__ = "revisions"

	id = Column(Integer, primary_key=True)
	kind = Column(String(10), nullable=False, index=True)  # "page" or "post"
	object_id = Column(Integer, nullable=False, index=True)
	title = Column(String(255))
	content = Column(Text)
	summary = Column(Text)  # page description / post excerpt
	status = Column(String(50))
	is_autosave = Column(Boolean, nullable=False, default=False)
	author_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
	created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)


class Redirect(Base):
	"""Old path -> new path. Created automatically when a slug changes; also editable by hand."""

	__tablename__ = "redirects"

	id = Column(Integer, primary_key=True)
	source = Column(String(500), unique=True, nullable=False, index=True)
	target = Column(String(1000), nullable=False)
	permanent = Column(Boolean, nullable=False, default=True)
	hits = Column(Integer, nullable=False, default=0)
	created_at = Column(DateTime, default=datetime.datetime.utcnow)


class ContactMessage(Base):
	"""A message sent through the public contact form."""

	__tablename__ = "contact_messages"

	id = Column(Integer, primary_key=True)
	name = Column(String(200), nullable=False)
	email = Column(String(255), nullable=False)
	subject = Column(String(300))
	message = Column(Text, nullable=False)
	ip = Column(String(64))
	is_read = Column(Boolean, nullable=False, default=False)
	emailed = Column(Boolean, nullable=False, default=False)
	created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)


class Comment(Base):
	"""A reader comment on a post. Held as "pending" until a moderator approves it."""

	__tablename__ = "comments"

	id = Column(Integer, primary_key=True)
	post_id = Column(Integer, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
	author_name = Column(String(150), nullable=False)
	author_email = Column(String(255))
	body = Column(Text, nullable=False)
	status = Column(String(20), nullable=False, default="pending", index=True)  # pending | approved | spam
	ip = Column(String(64))
	created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)


class Page(Base):
	"""Features: - Draft system - Version tracking - URL routing - Meta descriptions - Custom templates  Attributes: id (int): Primary key and unique identifier slug (str): URL-friendly unique identifier - Used in page URLs - Must be unique - Auto-generated from title title (str): Page title - Required..."""

	__tablename__ = "pages"

	id = Column(Integer, primary_key=True, index=True)
	slug = Column(String(255), unique=True, index=True, nullable=False)
	title = Column(String(255), nullable=False)
	content = Column(Text)
	description = Column(Text)
	status = Column(String(50), default="published")
	template = Column(String(100), nullable=False, default="default")
	fields = Column(Text, nullable=True)
	author_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
	publish_at = Column(DateTime, nullable=True, index=True)  # set with status "scheduled"
	seo = Column(Text, nullable=True)  # JSON: title, description, canonical, og_image
	created_at = Column(DateTime, default=datetime.datetime.utcnow)
	updated_at = Column(
		DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
	)

	def to_dict(self):
		return {
			"publish_at": self.publish_at.isoformat() if self.publish_at else None,
			"seo": decode_fields(self.seo),
			"id": self.id,
			"slug": self.slug,
			"title": self.title,
			"content": self.content,
			"description": self.description,
			"status": self.status,
			"template": self.template or "default",
			"fields": decode_fields(self.fields),
			"author_id": self.author_id,
			"created_at": self.created_at.isoformat() if self.created_at else None,
			"updated_at": self.updated_at.isoformat() if self.updated_at else None,
		}


class Post(Base):

	"""blog post or article.  Features: - SEO-friendly slugs - Content management - Draft support - Categories and tags - Timestamps - URL routing - Rich relationships - Data validation  Attributes: id (int): Primary key and unique identifier slug (str): URL-friendly unique identifier title (str): Post..."""

	__tablename__ = "posts"

	id = Column(Integer, primary_key=True, index=True)
	slug = Column(String(255), unique=True, index=True, nullable=False)
	title = Column(String(255), nullable=False)
	content = Column(Text)
	excerpt = Column(Text)
	status = Column(String(50), default="published")
	category_id = Column(Integer, ForeignKey("categories.id"))
	author_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
	created_at = Column(DateTime, default=datetime.datetime.utcnow)
	updated_at = Column(
		DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
	)
	published_at = Column(DateTime)
	cover_image = Column(String(1000), nullable=True)
	meta = Column(Text, nullable=True)
	publish_at = Column(DateTime, nullable=True, index=True)  # set with status "scheduled"
	seo = Column(Text, nullable=True)  # JSON: title, description, canonical, og_image

	category = relationship("Category", back_populates="posts")
	tags = relationship("Tag", secondary=post_tags, back_populates="posts")

	def to_dict(self):
		return {
			"publish_at": self.publish_at.isoformat() if self.publish_at else None,
			"seo": decode_fields(self.seo),
			"author_id": self.author_id,
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
			"cover_image": self.cover_image or "",
			"meta": decode_fields(self.meta),
		}


class Category(Base):
	"""Content category for organizing posts.  Features: - Hierarchical organization - SEO-friendly URLs - Post aggregation - Navigation support - Post counting - Archive views  Attributes: id (int): Primary key and unique identifier name (str): Display name of category slug (str): URL-friendly unique..."""
	__tablename__ = "categories"

	id = Column(Integer, primary_key=True, index=True)
	name = Column(String(255), nullable=False)
	slug = Column(String(255), unique=True, index=True, nullable=False)
	description = Column(Text)
	created_at = Column(DateTime, default=datetime.datetime.utcnow)
	section_path = Column(String(100), unique=True, index=True, nullable=True)
	singular_name = Column(String(100), nullable=True)
	fields_schema = Column(Text, nullable=True)
	sort_order = Column(Integer, default=0)
	date_label = Column(String(60), nullable=True)
	show_time = Column(Boolean, default=False)

	posts = relationship("Post", back_populates="category")

	def to_dict(self):
		return {
			"id": self.id,
			"name": self.name,
			"slug": self.slug,
			"description": self.description,
			"created_at": self.created_at.isoformat() if self.created_at else None,
			"post_count": len(self.posts),
			"section_path": self.section_path or "",
			"singular_name": self.singular_name or "",
			"fields_schema": decode_json_list(self.fields_schema),
			"sort_order": self.sort_order or 0,
			"date_label": self.date_label or "Date",
			"show_time": bool(self.show_time),
		}


class Tag(Base):
	"""Content tag for flexible post organization.  Features: - Post tagging - Tag clouds - Related content - Search enhancement - Content filtering - Usage tracking  Attributes: id (int): Primary key and unique identifier name (str): Tag name, must be unique created_at (datetime): Creation timestamp ..."""
	__tablename__ = "tags"

	id = Column(Integer, primary_key=True, index=True)
	name = Column(String(255), unique=True, index=True, nullable=False)
	created_at = Column(DateTime, default=datetime.datetime.utcnow)

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
		try:
			value = json.loads(self.value) if self.value else None
		except (json.JSONDecodeError, TypeError):
			value = self.value

		return {"key": self.key, "value": value}


class MediaLibrary(Base):
	"""Model representing media files in the CMS library.  Features: - File management - Image optimization - Thumbnail generation - Metadata tracking - MIME type handling  Attributes: id (int): Primary key and unique identifier filename (str): System filename on disk - Generated unique name - Includes..."""
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


class Entry(Base):
	"""
	A record in a directory such as Faculty or Officer & Staff.

	``data`` holds the full JSON record (biography, education, publications, ...),
	in the same shape as the old faculty.json file, so it can be imported and
	exported unchanged. The columns next to it exist for listing and ordering.
	"""

	__tablename__ = "entries"

	id = Column(Integer, primary_key=True, index=True)
	kind = Column(String(50), nullable=False, index=True)
	slug = Column(String(255), nullable=False, index=True)
	name = Column(String(255), nullable=False)
	group = Column(String(50), nullable=False, default="active")
	status = Column(String(50), default="published")
	sort_order = Column(Integer, default=0)
	data = Column(Text, nullable=True)
	created_at = Column(DateTime, default=datetime.datetime.utcnow)
	updated_at = Column(
		DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
	)

	def to_dict(self):
		record = decode_fields(self.data)
		record.update({
			"id": self.id,
			"kind": self.kind,
			"slug": self.slug,
			"name": self.name,
			"group": self.group or "active",
			"status": self.status or "published",
			"sort_order": self.sort_order or 0,
		})
		return record


class DatabaseManager:
	"""Core database management class handling all database operations.  This class provides a centralized interface for: - Database initialization and setup - Session management - Table creation and updates - Media library operations - User management - Content CRUD operations  Features: - Automatic..."""

	def __init__(self):
		self.engine = engine
		self.SessionLocal = SessionLocal

		self.create_tables()

	def create_tables(self):
		"""Create all tables and bring older databases up to the current schema."""
		self._run_migrations()
		self._encrypt_legacy_secrets()

	def _run_migrations(self):
		"""Bring the database to the latest schema with Alembic (see migrations/versions)."""
		from alembic import command
		from alembic.config import Config
		base = os.path.dirname(os.path.abspath(__file__))
		cfg = Config()
		cfg.set_main_option("script_location", os.path.join(base, "migrations"))
		try:
			with self.engine.begin() as conn:
				cfg.attributes["connection"] = conn
				command.upgrade(cfg, "head")
		except Exception as e:  # keep the site up; the error is logged for the operator
			logger.error("Database migration failed: %s", e)
			raise

	def _encrypt_legacy_secrets(self):
		"""Encrypt any 2FA secrets still stored as plain text by older versions."""
		try:
			with self.engine.begin() as conn:
				rows = conn.execute(text(
					"SELECT id, two_fa_secret FROM users WHERE two_fa_secret IS NOT NULL AND two_fa_secret <> ''")).fetchall()
				for row in rows:
					if not is_encrypted(row[1]):
						conn.execute(text("UPDATE users SET two_fa_secret = :s WHERE id = :i"),
						             {"s": encrypt_secret(row[1]), "i": row[0]})
		except SQLAlchemyError as e:
			logger.info(f"2FA secret encryption warning: {e}")

	# ---- login throttling ------------------------------------------------
	def record_attempt(self, kind: str, key: str) -> None:
		db = self.get_session()
		try:
			db.add(LoginAttempt(kind=kind, key=key))
			cutoff = datetime.datetime.utcnow() - timedelta(hours=1)
			db.query(LoginAttempt).filter(LoginAttempt.created_at < cutoff).delete()
			db.commit()
		except SQLAlchemyError:
			db.rollback()
		finally:
			db.close()

	def recent_attempts(self, kind: str, key: str, window_seconds: int) -> List[datetime.datetime]:
		"""Timestamps (newest last) of attempts for this key inside the window."""
		db = self.get_session()
		try:
			cutoff = datetime.datetime.utcnow() - timedelta(seconds=window_seconds)
			rows = (db.query(LoginAttempt.created_at)
			        .filter(LoginAttempt.kind == kind, LoginAttempt.key == key, LoginAttempt.created_at >= cutoff)
			        .order_by(LoginAttempt.created_at).all())
			return [r[0] for r in rows]
		finally:
			db.close()

	def clear_attempts(self, kind: str, key: str) -> None:
		db = self.get_session()
		try:
			db.query(LoginAttempt).filter(LoginAttempt.kind == kind, LoginAttempt.key == key).delete()
			db.commit()
		except SQLAlchemyError:
			db.rollback()
		finally:
			db.close()

	def count_users(self) -> int:
		db = self.get_session()
		try:
			return db.query(User).count()
		finally:
			db.close()

	def count_active_administrators(self) -> int:
		db = self.get_session()
		try:
			return (
				db.query(User)
				.filter(User.role == ROLE_ADMINISTRATOR, User.is_active.is_(True))
				.count()
			)
		finally:
			db.close()

	def list_users(self) -> List[Dict[str, Any]]:
		db = self.get_session()
		try:
			return [u.to_dict() for u in db.query(User).order_by(User.username).all()]
		finally:
			db.close()

	def get_user(self, user_id: int, include_secrets: bool = False) -> Optional[Dict[str, Any]]:
		db = self.get_session()
		try:
			user = db.query(User).filter(User.id == user_id).first()
			return user.to_dict(include_secrets=include_secrets) if user else None
		finally:
			db.close()

	def get_user_by_username(self, username: str, include_secrets: bool = False) -> Optional[Dict[str, Any]]:
		db = self.get_session()
		try:
			user = db.query(User).filter(User.username == username).first()
			return user.to_dict(include_secrets=include_secrets) if user else None
		finally:
			db.close()

	def create_user(
		self,
		username: str,
		email: str,
		password_hash: str,
		role: str = ROLE_CONTRIBUTOR,
		display_name: Optional[str] = None,
	) -> Dict[str, Any]:
		"""Create a user. Raises ValueError with a readable message on conflict."""
		if role not in ROLES:
			raise ValueError(f"Unknown role: {role}")
		db = self.get_session()
		try:
			if db.query(User).filter(User.username == username).first():
				raise ValueError(f"Username '{username}' is already taken.")
			if db.query(User).filter(User.email == email).first():
				raise ValueError(f"Email '{email}' is already registered.")
			user = User(
				username=username,
				email=email,
				display_name=display_name or None,
				password_hash=password_hash,
				role=role,
				is_active=True,
			)
			db.add(user)
			db.commit()
			return user.to_dict()
		except SQLAlchemyError as e:
			db.rollback()
			raise ValueError(f"Database error while creating user: {str(e)}")
		finally:
			db.close()

	def update_user(self, user_id: int, **changes) -> Dict[str, Any]:
		"""Update allowed user columns. Raises ValueError on problems."""
		allowed = {"email", "display_name", "password_hash", "role", "is_active",
		           "two_fa_secret", "two_fa_enabled", "last_login_at",
		           "two_fa_recovery", "session_version"}
		unknown = set(changes) - allowed
		if unknown:
			raise ValueError(f"Cannot update fields: {', '.join(sorted(unknown))}")
		if "role" in changes and changes["role"] not in ROLES:
			raise ValueError(f"Unknown role: {changes['role']}")
		db = self.get_session()
		try:
			user = db.query(User).filter(User.id == user_id).first()
			if user is None:
				raise ValueError("User not found.")
			if "email" in changes:
				clash = db.query(User).filter(User.email == changes["email"], User.id != user_id).first()
				if clash:
					raise ValueError(f"Email '{changes['email']}' is already registered.")
			for key, value in changes.items():
				setattr(user, key, value)
			user.updated_at = datetime.datetime.utcnow()
			db.commit()
			return user.to_dict()
		except SQLAlchemyError as e:
			db.rollback()
			raise ValueError(f"Database error while updating user: {str(e)}")
		finally:
			db.close()

	def delete_user(self, user_id: int, reassign_to_id: int) -> bool:
		"""Delete a user and hand their pages and posts to another user."""
		db = self.get_session()
		try:
			user = db.query(User).filter(User.id == user_id).first()
			if user is None:
				return False
			db.query(Page).filter(Page.author_id == user_id).update(
				{Page.author_id: reassign_to_id}, synchronize_session=False
			)
			db.query(Post).filter(Post.author_id == user_id).update(
				{Post.author_id: reassign_to_id}, synchronize_session=False
			)
			db.delete(user)
			db.commit()
			return True
		except SQLAlchemyError as e:
			db.rollback()
			logger.error(f"Error deleting user: {str(e)}")
			return False
		finally:
			db.close()

	def touch_last_login(self, user_id: int) -> None:
		db = self.get_session()
		try:
			user = db.query(User).filter(User.id == user_id).first()
			if user:
				user.last_login_at = datetime.datetime.utcnow()
				db.commit()
		except SQLAlchemyError:
			db.rollback()
		finally:
			db.close()

	def get_session(self) -> Session:
		"""Get database session"""
		return self.SessionLocal()

	def is_setup_complete(self) -> bool:
		"""The CMS is usable once at least one account exists (created via CLI)."""
		return self.count_users() > 0

	def initialize_default_data(self):
		"""Initialize database with default data"""
		db = self.get_session()
		try:
			if db.query(SiteSetting).first():
				return

			general_cat = Category(
				name="General", slug="general", description="General posts"
			)
			tech_cat = Category(
				name="Technology", slug="technology", description="Tech related posts"
			)
			db.add_all([general_cat, tech_cat])
			db.flush()

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

			default_settings = {
				"site_title": "Flexaflow",
				"site_description": "A powerful CMS",
				"favicon": "/static/flexaflow.ico",
				"logo": "/static/flexaflow.png",
				"custom_analytics": "",
				"site_domain": "",
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
			logger.error(f"Error initializing default data: {str(e)}")
		finally:
			db.close()

	def add_media(self, media_data):
		"""Add a new media item to the media library."""
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

			result = media_data.copy()
			result["id"] = media.id
			result["created_at"] = media.created_at.isoformat()
			result["url"] = f"/uploads/{media.filename}"
			if media.thumbnail:
				result["thumbnail_url"] = f"/uploads/thumbnails/{media.thumbnail}"

			return result
		except Exception as e:
			db.rollback()
			logger.error(f"Error adding media: {str(e)}")
			return None
		finally:
			db.close()

	# ---- media groups -------------------------------------------------------------------------------------------
	@staticmethod
	def _clean_group_name(name):
		name = " ".join((name or "").split())
		if not name:
			raise ValueError("Give the group a name.")
		if len(name) > 120:
			raise ValueError("Group names can be at most 120 characters.")
		return name

	def list_media_groups(self):
		"""All groups, alphabetically, each with the number of images in it."""
		db = self.get_session()
		try:
			rows = (
				db.query(MediaGroup.id, MediaGroup.name, MediaGroup.description, func.count(MediaGroupItem.media_id))
				.outerjoin(MediaGroupItem, MediaGroupItem.group_id == MediaGroup.id)
				.group_by(MediaGroup.id)
				.order_by(func.lower(MediaGroup.name))
				.all()
			)
			return [{"id": r[0], "name": r[1], "description": r[2] or "", "count": r[3]} for r in rows]
		except Exception as e:
			logger.error(f"Error listing media groups: {str(e)}")
			return []
		finally:
			db.close()

	def get_groups_for_media(self, media_id):
		"""The groups one media item belongs to, as [{id, name}]."""
		db = self.get_session()
		try:
			rows = (
				db.query(MediaGroup.id, MediaGroup.name)
				.join(MediaGroupItem, MediaGroupItem.group_id == MediaGroup.id)
				.filter(MediaGroupItem.media_id == media_id)
				.order_by(func.lower(MediaGroup.name))
				.all()
			)
			return [{"id": r[0], "name": r[1]} for r in rows]
		except Exception as e:
			logger.error(f"Error getting groups for media: {str(e)}")
			return []
		finally:
			db.close()

	def get_media_group(self, group_id):
		"""One group as a dict, or None."""
		db = self.get_session()
		try:
			group = db.query(MediaGroup).filter(MediaGroup.id == group_id).first()
			if not group:
				return None
			return {"id": group.id, "name": group.name, "description": group.description or ""}
		finally:
			db.close()

	def create_media_group(self, name, description=""):
		"""Create a group. Raises ValueError for a bad or duplicate name."""
		name = self._clean_group_name(name)
		db = self.get_session()
		try:
			if db.query(MediaGroup).filter(func.lower(MediaGroup.name) == name.lower()).first():
				raise ValueError("A group with this name already exists.")
			group = MediaGroup(name=name, description=(description or "").strip() or None)
			db.add(group)
			db.commit()
			return {"id": group.id, "name": group.name, "description": group.description or "", "count": 0}
		except ValueError:
			db.rollback()
			raise
		except Exception as e:
			db.rollback()
			logger.error(f"Error creating media group: {str(e)}")
			raise ValueError("Could not create the group.")
		finally:
			db.close()

	def update_media_group(self, group_id, name, description=None):
		"""Rename a group (and change its description if one is given). Returns None if it does not exist; raises ValueError for a bad name."""
		name = self._clean_group_name(name)
		db = self.get_session()
		try:
			group = db.query(MediaGroup).filter(MediaGroup.id == group_id).first()
			if not group:
				return None
			clash = (
				db.query(MediaGroup)
				.filter(func.lower(MediaGroup.name) == name.lower(), MediaGroup.id != group_id)
				.first()
			)
			if clash:
				raise ValueError("A group with this name already exists.")
			group.name = name
			if description is not None:
				group.description = description.strip() or None
			db.commit()
			return True
		except ValueError:
			db.rollback()
			raise
		finally:
			db.close()

	def delete_media_group(self, group_id):
		"""Delete a group and its memberships. The images in it are kept."""
		db = self.get_session()
		try:
			group = db.query(MediaGroup).filter(MediaGroup.id == group_id).first()
			if not group:
				return False
			db.query(MediaGroupItem).filter(MediaGroupItem.group_id == group_id).delete(synchronize_session=False)
			db.delete(group)
			db.commit()
			return True
		except Exception as e:
			db.rollback()
			logger.error(f"Error deleting media group: {str(e)}")
			return False
		finally:
			db.close()

	def add_media_to_group(self, group_id, media_ids):
		"""Add existing media items to a group. Returns how many were added, or None if the group is missing."""
		ids = sorted({int(i) for i in media_ids})
		db = self.get_session()
		try:
			if not db.query(MediaGroup.id).filter(MediaGroup.id == group_id).first():
				return None
			if not ids:
				return 0
			existing = {r[0] for r in db.query(Media.id).filter(Media.id.in_(ids))}
			already = {r[0] for r in db.query(MediaGroupItem.media_id).filter(
				MediaGroupItem.group_id == group_id, MediaGroupItem.media_id.in_(sorted(existing)))} if existing else set()
			new = existing - already
			for media_id in new:
				db.add(MediaGroupItem(group_id=group_id, media_id=media_id))
			db.commit()
			return len(new)
		except Exception as e:
			db.rollback()
			logger.error(f"Error adding media to group: {str(e)}")
			return None
		finally:
			db.close()

	def remove_media_from_group(self, group_id, media_ids):
		"""Take items out of a group. The media items themselves are not deleted."""
		ids = sorted({int(i) for i in media_ids})
		db = self.get_session()
		try:
			if not ids:
				return 0
			removed = (
				db.query(MediaGroupItem)
				.filter(MediaGroupItem.group_id == group_id, MediaGroupItem.media_id.in_(ids))
				.delete(synchronize_session=False)
			)
			db.commit()
			return removed
		except Exception as e:
			db.rollback()
			logger.error(f"Error removing media from group: {str(e)}")
			return None
		finally:
			db.close()

	def get_media_library(self, page=1, per_page=20, search=None, group_id=None, ungrouped=False):
		"""Retrieve media library items with pagination and search.  Features: - Full-text search - Smart pagination - Sorting options - URL generation - Metadata formatting - Performance optimization  Args: page (int): Page number to retrieve (default: 1) per_page (int): Items per page (default: 20)..."""
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

			if group_id:
				query = query.filter(Media.id.in_(select(MediaGroupItem.media_id).where(MediaGroupItem.group_id == group_id)))
			elif ungrouped:
				query = query.filter(~Media.id.in_(select(MediaGroupItem.media_id)))

			total = query.count()
			total_pages = (total + per_page - 1) // per_page

			page = max(1, min(page, total_pages))

			offset = (page - 1) * per_page
			media_items = (
				query.order_by(Media.created_at.desc())
				.offset(offset)
				.limit(per_page)
				.all()
			)

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

			if items:
				memberships = {}
				rows = (
					db.query(MediaGroupItem.media_id, MediaGroup.id, MediaGroup.name)
					.join(MediaGroup, MediaGroup.id == MediaGroupItem.group_id)
					.filter(MediaGroupItem.media_id.in_([i["id"] for i in items]))
					.order_by(func.lower(MediaGroup.name))
					.all()
				)
				for media_id, gid, gname in rows:
					memberships.setdefault(media_id, []).append({"id": gid, "name": gname})
				for item in items:
					item["groups"] = memberships.get(item["id"], [])

			return {
				"items": items,
				"total": total,
				"page": page,
				"total_pages": total_pages,
				"per_page": per_page,
			}

		except Exception as e:
			logger.error(f"Error getting media library: {str(e)}")
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
			logger.error(f"Error updating media: {str(e)}")
			return False
		finally:
			db.close()

	def delete_media(self, media_id):
		"""Delete a media item and its associated files.  Features: - Database cleanup - File system cleanup - Transaction safety - Resource management - Error recovery  Args: media_id (int): ID of the media item to delete  Returns: bool: True if deletion successful, False otherwise  Processing: 1. Fetches..."""
		db = self.get_session()
		try:
			media = db.query(Media).filter(Media.id == media_id).first()
			if media:
				filename = media.filename
				thumbnail = media.thumbnail

				# Foreign-key cascades are not enforced on every database, so clear group memberships explicitly.
				db.query(MediaGroupItem).filter(MediaGroupItem.media_id == media_id).delete(synchronize_session=False)
				db.delete(media)
				db.commit()

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
					logger.error(f"Error deleting media files: {str(e)}")

				return True
			return False
		except Exception as e:
			db.rollback()
			logger.error(f"Error deleting media: {str(e)}")
			return False
		finally:
			db.close()


	def get_sitemap_content(self) -> Optional[str]:
		"""Retrieves the sitemap XML content from the database."""
		db = self.get_session()
		try:
			sitemap = db.query(Sitemap).first()
			return sitemap.content if sitemap else None
		except SQLAlchemyError as e:
			logger.error(f"Error getting sitemap: {str(e)}")
			return None
		finally:
			db.close()


	def update_sitemap_content(self, xml_content: str) -> bool:
		"""Updates or creates the sitemap entry in the database."""
		db = self.get_session()
		try:
			sitemap = db.query(Sitemap).first()
			if sitemap:
				sitemap.content = xml_content
				sitemap.updated_at = datetime.datetime.utcnow()
			else:
				sitemap = Sitemap(content=xml_content)
				db.add(sitemap)
			db.commit()
			return True
		except SQLAlchemyError as e:
			db.rollback()
			logger.error(f"Error updating sitemap: {str(e)}")
			return False
		finally:
			db.close()


db_manager = DatabaseManager()
db_manager.create_tables()
db_manager.initialize_default_data()


def get_pages() -> Dict[str, Any]:
	"""Retrieve all published pages from the database.  Returns: Dict[str, Any]: Dictionary of pages where: - key: page slug - value: page data dictionary containing: - title: page title - content: page content - description: meta description - status: publication status - timestamps: created/updated..."""
	db = db_manager.get_session()
	try:
		pages = db.query(Page).filter(Page.status == "published").all()
		return {page.slug: page.to_dict() for page in pages}
	except SQLAlchemyError as e:
		logger.error(f"Error getting pages: {str(e)}")
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
		logger.error(f"Error getting draft pages: {str(e)}")
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
		logger.error(f"Error getting posts: {str(e)}")
		return {}
	finally:
		db.close()


def search_posts(query: str, limit: int = 200) -> List[Dict[str, Any]]:
	"""Published posts matching `query`, best match first.

	Uses SQLite FTS5 or MySQL FULLTEXT when available (prefix match on each word) and
	falls back to LIKE otherwise. Tag names also match.
	"""
	words = re.findall(r"\w+", query or "", re.UNICODE)[:8]
	if not words:
		return []
	ids: List[int] = []
	dialect = db_manager.engine.dialect.name
	try:
		with db_manager.engine.connect() as conn:
			if dialect == "sqlite":
				fts = " ".join(f'"{w}"*' for w in words)
				rows = conn.exec_driver_sql(
					"SELECT ref_id FROM search_index WHERE kind = 'post' AND search_index MATCH ? ORDER BY rank LIMIT ?",
					(fts, limit)).fetchall()
				ids = [int(r[0]) for r in rows]
			elif dialect in ("mysql", "mariadb"):
				rows = conn.execute(text(
					"SELECT id FROM posts WHERE MATCH(title, content) AGAINST (:q IN BOOLEAN MODE) LIMIT :n"),
					{"q": " ".join(f"+{w}*" for w in words), "n": limit}).fetchall()
				ids = [int(r[0]) for r in rows]
			else:
				raise SQLAlchemyError("no full-text engine")
	except Exception:
		ids = []
		db = db_manager.get_session()
		try:
			q = db.query(Post.id).filter(Post.status == "published")
			for w in words:
				like = f"%{w}%"
				q = q.filter(or_(Post.title.ilike(like), Post.content.ilike(like), Post.excerpt.ilike(like)))
			ids = [r[0] for r in q.limit(limit).all()]
		finally:
			db.close()
	db = db_manager.get_session()
	try:
		tag_ids = [r[0] for r in db.query(Post.id).join(Post.tags).filter(
			Post.status == "published", or_(*[Tag.name.ilike(f"%{w}%") for w in words])).all()] if hasattr(Post, "tags") else []
		for t in tag_ids:
			if t not in ids:
				ids.append(t)
		if not ids:
			return []
		posts = {p.id: p for p in db.query(Post).filter(Post.id.in_(ids), Post.status == "published").all()}
		return [posts[i].to_dict() for i in ids if i in posts]
	finally:
		db.close()


def get_draft_posts() -> Dict[str, Any]:
	"""Get all draft posts"""
	db = db_manager.get_session()
	try:
		posts = db.query(Post).filter(Post.status == "draft").all()
		return {post.slug: post.to_dict() for post in posts}
	except SQLAlchemyError as e:
		logger.error(f"Error getting draft posts: {str(e)}")
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
		logger.error(f"Error getting categories: {str(e)}")
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
		logger.error(f"Error getting tags: {str(e)}")
		return {}
	finally:
		db.close()


def get_site_settings() -> Dict[str, Any]:
	"""Retrieve all site configuration settings.  Features: - JSON value parsing - Error handling - Type conversion - Default values - Session management  Returns: Dict[str, Any]: Configuration dictionary containing: - site_title: Site name - site_description: Meta description - theme: Active theme..."""
	db = db_manager.get_session()
	try:
		settings = db.query(SiteSetting).all()
		result = {}
		for setting in settings:
			try:
				value = json.loads(setting.value) if setting.value else ""
			except (json.JSONDecodeError, TypeError):
				value = setting.value
			result[setting.key] = value
		return result
	except SQLAlchemyError as e:
		logger.error(f"Error getting site settings: {str(e)}")
		return {}
	finally:
		db.close()


MAX_REVISIONS = 50


def _editor_id():
	"""Id of the signed-in user, when called inside a request (None for scripts and imports)."""
	try:
		from flask import has_request_context, session
		return int(session["user_id"]) if has_request_context() and session.get("user_id") else None
	except Exception:
		return None


def save_revision(db: Session, kind: str, obj, autosave: bool = False, author_id: Optional[int] = None) -> None:
	"""Store the current title/content of a page or post so it can be restored later."""
	summary = obj.description if kind == "page" else obj.excerpt
	rev = Revision(kind=kind, object_id=obj.id, title=obj.title, content=obj.content or "", summary=summary or "",
	               status=obj.status, is_autosave=autosave, author_id=author_id or _editor_id())
	db.add(rev)
	db.flush()
	old = (db.query(Revision.id).filter(Revision.kind == kind, Revision.object_id == obj.id, Revision.is_autosave.is_(False))
	       .order_by(Revision.id.desc()).offset(MAX_REVISIONS).all())
	if old:
		db.query(Revision).filter(Revision.id.in_([r[0] for r in old])).delete(synchronize_session=False)


def _content_changed(obj, title, content, summary, kind) -> bool:
	current_summary = (obj.description if kind == "page" else obj.excerpt) or ""
	return (title is not None and title != obj.title) or (content is not None and content != (obj.content or "")) \
		or (summary is not None and summary != current_summary)


def _drop_autosaves(db: Session, kind: str, object_id: int) -> None:
	db.query(Revision).filter(Revision.kind == kind, Revision.object_id == object_id,
	                          Revision.is_autosave.is_(True)).delete(synchronize_session=False)


def add_redirect(db: Session, source: str, target: str, permanent: bool = True) -> None:
	"""Create or update a redirect; removes any redirect that would now loop."""
	if not source or source == target:
		return
	db.query(Redirect).filter(Redirect.source == target).delete(synchronize_session=False)
	row = db.query(Redirect).filter(Redirect.source == source).first()
	if row:
		row.target, row.permanent = target, permanent
	else:
		db.add(Redirect(source=source, target=target, permanent=permanent))


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
			template=page_data.get("template") or "default",
			fields=json.dumps(page_data.get("fields") or {}, ensure_ascii=False),
			author_id=page_data.get("author_id"),
			publish_at=page_data.get("publish_at"),
			seo=json.dumps(page_data.get("seo") or {}, ensure_ascii=False),
		)
		db.add(page)
		db.commit()
		return True
	except SQLAlchemyError as e:
		db.rollback()
		logger.error(f"Error adding page: {str(e)}")
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
		logger.error(f"Error deleting page: {str(e)}")
		return False
	finally:
		db.close()


def add_post(slug: str, post_data: Dict[str, Any]) -> bool:
	"""Add a new post"""
	db = db_manager.get_session()
	try:
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
			author_id=post_data.get("author_id"),
			cover_image=post_data.get("cover_image") or None,
			meta=json.dumps(post_data.get("meta") or {}, ensure_ascii=False),
			publish_at=post_data.get("publish_at"),
			seo=json.dumps(post_data.get("seo") or {}, ensure_ascii=False),
			published_at=(
				post_data.get("published_at")
				or (datetime.datetime.utcnow() if post_data.get("status") == "published" else None)
			),
		)

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
		logger.error(f"Error adding post: {str(e)}")
		return False
	finally:
		db.close()


def get_latest_posts(category_slug: Optional[str] = None, limit: int = 5) -> List[Dict[str, Any]]:
	"""
	Published posts, newest first, optionally limited to one category.
	Used by page templates (e.g. a department landing page's news/notice lists).
	"""
	db = db_manager.get_session()
	try:
		query = (
			db.query(Post)
			.outerjoin(Category, Post.category_id == Category.id)
			.filter(Post.status == "published")
		)
		if category_slug:
			query = query.filter(Category.slug == category_slug)
		posts = (
			query.order_by(Post.published_at.desc(), Post.created_at.desc())
			.limit(max(1, int(limit)))
			.all()
		)
		result = []
		for post in posts:
			result.append({
				"slug": post.slug,
				"title": post.title,
				"excerpt": post.excerpt or "",
				"published_at": post.published_at,
				"category": post.category.name if post.category else "",
				"cover_image": post.cover_image or "",
				"meta": decode_fields(post.meta),
				"url": (
					f"/{post.category.section_path}/{post.slug}"
					if post.category and post.category.section_path else f"/post/{post.slug}"
				),
			})
		return result
	except SQLAlchemyError as e:
		logger.error(f"Error loading latest posts: {str(e)}")
		return []
	finally:
		db.close()


def add_draft_post(slug: str, post_data: Dict[str, Any]) -> bool:
	"""Add a new draft post"""
	post_data["status"] = "draft"
	return add_post(slug, post_data)


def update_site_settings(settings_data: Dict[str, Any]) -> bool:
	"""Update site configuration settings in the database.  Features: - JSON serialization - Atomic transactions - Upsert behavior - Type safety - Timestamp tracking - Session management  Args: settings_data (Dict[str, Any]): Configuration dictionary containing: - site_title: Site name (str) -..."""
	db = db_manager.get_session()
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
		logger.error(f"Error updating site settings: {str(e)}")
		return False
	finally:
		db.close()


def update_tag_counts() -> Dict[str, Any]:
	"""Recalculate tag counts based on published posts"""
	return get_tags()


def migrate_from_json(json_file_path: str) -> bool:
	"""Migrate data from JSON file to database"""
	try:
		with open(json_file_path, "r") as f:
			data = json.load(f)

		db = db_manager.get_session()

		db.query(Post).delete()
		db.query(Page).delete()
		db.query(Category).delete()
		db.query(Tag).delete()
		db.query(SiteSetting).delete()

		if "categories" in data:
			for slug, cat_data in data["categories"].items():
				category = Category(
					name=cat_data.get("name", slug.title()),
					slug=slug,
					description=cat_data.get("description", ""),
				)
				db.add(category)

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
		logger.info("Migration completed successfully!")
		return True

	except Exception as e:
		db.rollback()
		logger.error(f"Migration failed: {str(e)}")
		return False
	finally:
		db.close()


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

		logger.info(f"Backup completed: {json_file_path}")
		return True

	except Exception as e:
		logger.error(f"Backup failed: {str(e)}")
		return False
