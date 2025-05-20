import os
import json
import datetime
from typing import Dict, Any

DATA_FILE = "data.json"

# Default data structure
DEFAULT_DATA = {
    "pages": {
        "home": {"content": "Welcome to the home page!", "title": "Home", "description": "This is the home page.", "status": "published"},
        "about": {"content": "This is the about page.", "title": "About", "description": "This is the about page.", "status": "published"}
    },
    "draft_pages": {},
    "posts": {},
    "draft_posts": {},
    "categories": {
        "general": {"name": "General", "description": "General posts", "slug": "general"},
        "technology": {"name": "Technology", "description": "Tech related posts", "slug": "technology"}
    },
    "tags": {},
    "site_settings": {
        "site_title": "My Website",
        "site_description": "A powerful CMS",
        "favicon": "/static/favicon.ico",
        "logo": "/static/logo.png",
        "google_analytics": "",
        "homepage": "home",
        "privacy_page": "",
        "terms_page": "",
        "social_links": {
            "facebook": "",
            "twitter": "",
            "instagram": ""
        },
        "theme": "default",
        "menu_items": [
            {"label": "Home", "url": "/home"},
            {"label": "About", "url": "/about"}
        ]
    }
}

def validate_data_structure(data: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure all required keys exist in the data structure"""
    validated = DEFAULT_DATA.copy()
    if isinstance(data, dict):
        for key in DEFAULT_DATA.keys():
            if key in data and isinstance(data[key], type(DEFAULT_DATA[key])):
                validated[key] = data[key]
    return validated

def load_data() -> Dict[str, Any]:
    """Load data from file with error handling and validation"""
    try:
        if not os.path.exists(DATA_FILE):
            # Create new file with default data
            save_data(DEFAULT_DATA)
            return DEFAULT_DATA.copy()
        
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            # Validate and repair data structure if needed
            return validate_data_structure(data)
            
    except (json.JSONDecodeError, IOError) as e:
        print(f"Error loading data: {str(e)}")
        # Backup corrupted file if it exists
        if os.path.exists(DATA_FILE):
            backup_file = f"{DATA_FILE}.backup.{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
            try:
                os.rename(DATA_FILE, backup_file)
                print(f"Corrupted data file backed up to: {backup_file}")
            except OSError as e:
                print(f"Failed to backup corrupted file: {str(e)}")
        
        # Create new file with default data
        save_data(DEFAULT_DATA)
        return DEFAULT_DATA.copy()

def save_data(data_store: Dict[str, Any]) -> bool:
    """Save data to file with error handling"""
    try:
        # Validate data before saving
        validated_data = validate_data_structure(data_store)
        
        # Create backup of existing file
        if os.path.exists(DATA_FILE):
            backup_file = f"{DATA_FILE}.backup"
            try:
                os.replace(DATA_FILE, backup_file)
            except OSError:
                pass  # Continue even if backup fails
        
        # Write new data
        with open(DATA_FILE, "w") as f:
            json.dump(validated_data, f, indent=4)
        return True
        
    except Exception as e:
        print(f"Error saving data: {str(e)}")
        # Restore from backup if save failed
        if os.path.exists(f"{DATA_FILE}.backup"):
            try:
                os.replace(f"{DATA_FILE}.backup", DATA_FILE)
            except OSError:
                pass
        return False

# Global data store
DATA_STORE = load_data()

def get_pages() -> Dict[str, Any]:
    return DATA_STORE.get("pages", {})

def get_draft_pages() -> Dict[str, Any]:
    return DATA_STORE.get("draft_pages", {})

def get_posts() -> Dict[str, Any]:
    return DATA_STORE.get("posts", {})

def get_draft_posts() -> Dict[str, Any]:
    return DATA_STORE.get("draft_posts", {})

def get_categories() -> Dict[str, Any]:
    return DATA_STORE.get("categories", {})

def get_tags() -> Dict[str, Any]:
    return DATA_STORE.get("tags", {})

def get_site_settings() -> Dict[str, Any]:
    return DATA_STORE.get("site_settings", DEFAULT_DATA["site_settings"])

def update_pages(pages_data: Dict[str, Any]) -> bool:
    if not isinstance(pages_data, dict):
        return False
    DATA_STORE["pages"] = pages_data
    return save_data(DATA_STORE)

def update_draft_pages(draft_pages_data: Dict[str, Any]) -> bool:
    if not isinstance(draft_pages_data, dict):
        return False
    DATA_STORE["draft_pages"] = draft_pages_data
    return save_data(DATA_STORE)

def update_posts(posts_data: Dict[str, Any]) -> bool:
    if not isinstance(posts_data, dict):
        return False
    DATA_STORE["posts"] = posts_data
    return save_data(DATA_STORE)

def update_draft_posts(draft_posts_data: Dict[str, Any]) -> bool:
    if not isinstance(draft_posts_data, dict):
        return False
    DATA_STORE["draft_posts"] = draft_posts_data
    return save_data(DATA_STORE)

def update_categories(categories_data: Dict[str, Any]) -> bool:
    if not isinstance(categories_data, dict):
        return False
    DATA_STORE["categories"] = categories_data
    return save_data(DATA_STORE)

def update_tags(tags_data: Dict[str, Any]) -> bool:
    if not isinstance(tags_data, dict):
        return False
    DATA_STORE["tags"] = tags_data
    return save_data(DATA_STORE)

def update_site_settings(settings_data: Dict[str, Any]) -> bool:
    if not isinstance(settings_data, dict):
        return False
    DATA_STORE["site_settings"] = settings_data
    return save_data(DATA_STORE)

def add_page(slug: str, page_data: Dict[str, Any]) -> bool:
    if not isinstance(page_data, dict):
        return False
    pages = get_pages()
    pages[slug] = page_data
    return update_pages(pages)

def add_draft_page(slug: str, page_data: Dict[str, Any]) -> bool:
    if not isinstance(page_data, dict):
        return False
    draft_pages = get_draft_pages()
    draft_pages[slug] = page_data
    return update_draft_pages(draft_pages)

def delete_page(slug: str) -> bool:
    pages = get_pages()
    if slug in pages:
        del pages[slug]
        return update_pages(pages)
    return False

def add_post(slug: str, post_data: Dict[str, Any]) -> bool:
    if not isinstance(post_data, dict):
        return False
    posts = get_posts()
    posts[slug] = post_data
    return update_posts(posts)

def add_draft_post(slug: str, post_data: Dict[str, Any]) -> bool:
    if not isinstance(post_data, dict):
        return False
    draft_posts = get_draft_posts()
    draft_posts[slug] = post_data
    return update_draft_posts(draft_posts)

def update_tag_counts() -> Dict[str, Any]:
    """Recalculate tag counts based on published posts"""
    all_tags = {}
    try:
        posts = get_posts()
        if isinstance(posts, dict):
            for post in posts.values():
                if isinstance(post, dict) and post.get('status') == 'published':
                    tags = post.get('tags', [])
                    if isinstance(tags, list):
                        for tag in tags:
                            if isinstance(tag, str):
                                if tag not in all_tags:
                                    all_tags[tag] = {'name': tag, 'count': 0}
                                all_tags[tag]['count'] = all_tags[tag].get('count', 0) + 1
    except Exception as e:
        print(f"Error updating tag counts: {str(e)}")
        return {}
    
    update_tags(all_tags)
    return all_tags
