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
post_tags = Table(
    "post_tags",
    Base.metadata,
    Column("post_id", Integer, ForeignKey("posts.id"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id"), primary_key=True),
)


# Media Model
class Media(Base):
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
    title = Column(String(255))  # Added for WordPress-like title
    description = Column(Text)  # Added for WordPress-like description
    thumbnail = Column(String(255))  # Added for thumbnail filename

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
                "google_analytics": "",
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
        """Add a new media item to the database"""
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
        """Get media library items with pagination and search"""
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
        """Delete a media item"""
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
    """Get all published pages"""
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
    """Get all site settings"""
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
    """Update site settings"""
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
