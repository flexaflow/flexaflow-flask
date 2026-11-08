"""
Sections and directories for the department site.

SECTIONS
    A section is a category that has a public address (``section_path``).
    Posts in the "Notices" section (path ``notices``) are published at
    ``/notices/<post-slug>`` and listed at ``/notices/``. The admin chooses the
    address, the singular name used on the "New notice" button, the label of the
    date, and any extra fields (notice number, attachment, venue, ...).

DIRECTORIES
    Faculty and Officer & Staff are records with a long structured profile
    (biography, education, publications). They are stored as ``Entry`` rows whose
    JSON has the same shape as the old faculty.json, so files can be imported and
    exported unchanged.
"""

import datetime
import json
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.exc import SQLAlchemyError

from data_store import Category, Entry, Post, db_manager, decode_fields, decode_json_list
from page_templates import NAME_RE, FIELD_TYPES, _validate_schema


RESERVED_PATHS = {
	"admin", "login", "logout", "search", "post", "page", "category", "tag", "api",
	"uploads", "static", "sitemap.xml", "setup", "upload_image", "ext",
	"robots.txt", "favicon.ico",
}


def reserved_paths():
	"""Core addresses plus the base paths of directories registered by extensions."""
	return RESERVED_PATHS | {c["base_path"].strip("/") for c in ENTRY_KINDS.values()}

PATH_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
POSTS_PER_PAGE = 8

CORE_SECTION_PRESETS = {
	"notice": {
		"name": "Notices", "singular_name": "Notice", "section_path": "notices",
		"description": "Official notices for students and faculty.",
		"date_label": "Notice date", "show_time": False,
		"fields_schema": [
			{"name": "notice_no", "label": "Notice number", "type": "text", "help": "For example CSE/2026/N-041."},
			{"name": "attachment", "label": "Attachment (PDF)", "type": "file"},
		],
	},
	"news": {
		"name": "News", "singular_name": "News story", "section_path": "news",
		"description": "Department news and announcements.",
		"date_label": "Date", "show_time": False,
		"fields_schema": [],
	},
	"event": {
		"name": "Events", "singular_name": "Event", "section_path": "events",
		"description": "Seminars, workshops, competitions and other events.",
		"date_label": "Event starts", "show_time": True,
		"fields_schema": [
			{"name": "end_datetime", "label": "Event ends", "type": "datetime"},
			{"name": "location", "label": "Location", "type": "text"},
			{"name": "registration_url", "label": "Registration link", "type": "url"},
		],
	},
}

EXTENSION_PRESETS = {}


def section_presets():
	merged = dict(CORE_SECTION_PRESETS)
	merged.update(EXTENSION_PRESETS)
	return merged


def section_url(section_path: str, slug: Optional[str] = None) -> str:
	return f"/{section_path}/{slug}" if slug else f"/{section_path}/"


def post_url(post: Dict[str, Any]) -> str:
	"""Public address of a post: under its section if it has one, else /post/<slug>."""
	category = post.get("category") or {}
	path = category.get("section_path") if isinstance(category, dict) else None
	return section_url(path, post["slug"]) if path else f"/post/{post['slug']}"


def validate_section_path(path: str, current_category_id: Optional[int] = None) -> Optional[str]:
	"""Error message if the address cannot be used, else None."""
	if not PATH_RE.match(path or ""):
		return "The address may contain lowercase letters, digits and hyphens only (for example: notices)."
	if path in reserved_paths():
		return f"'/{path}/' is used by the system. Please choose another address."
	db = db_manager.get_session()
	try:
		clash = db.query(Category).filter(Category.section_path == path).first()
		if clash and clash.id != current_category_id:
			return f"'/{path}/' is already used by the section {clash.name}."
		from data_store import Page
		if db.query(Page).filter(Page.slug == path).first():
			return f"A page already uses the address '/{path}'. Rename that page first."
		return None
	finally:
		db.close()


def clean_fields_schema(raw: Any) -> List[Dict[str, Any]]:
	"""Validate the extra-field definitions an admin typed in; raises ValueError."""
	if isinstance(raw, str):
		raw = json.loads(raw or "[]")
	if not isinstance(raw, list):
		raise ValueError("Extra fields must be a list.")
	fields = _validate_schema({"fields": raw}, "section")
	for f in fields:
		if f["type"] == "repeater":
			raise ValueError("Repeating groups are not available for section fields.")
	return fields


def list_sections() -> List[Dict[str, Any]]:
	"""Every category that has a public address, in menu order."""
	db = db_manager.get_session()
	try:
		rows = (
			db.query(Category)
			.filter(Category.section_path.isnot(None), Category.section_path != "")
			.order_by(Category.sort_order, Category.id)
			.all()
		)
		return [c.to_dict() for c in rows]
	except SQLAlchemyError as e:
		print(f"Error listing sections: {e}")
		return []
	finally:
		db.close()


def get_section_by_path(path: str) -> Optional[Dict[str, Any]]:
	db = db_manager.get_session()
	try:
		row = db.query(Category).filter(Category.section_path == path).first()
		return row.to_dict() if row else None
	finally:
		db.close()


def get_section_by_slug(slug: str) -> Optional[Dict[str, Any]]:
	db = db_manager.get_session()
	try:
		row = db.query(Category).filter(Category.slug == slug, Category.section_path.isnot(None)).first()
		return row.to_dict() if row and row.section_path else None
	finally:
		db.close()


def save_section(data: Dict[str, Any], category_id: Optional[int] = None) -> Tuple[Optional[Dict[str, Any]], List[str]]:
	"""
	Create a section (a new category, or an existing one given by ``category_id``) or update it.
	Returns (section, errors).
	"""
	errors: List[str] = []
	name = (data.get("name") or "").strip()
	path = (data.get("section_path") or "").strip().lower().strip("/")
	if not name:
		errors.append("A name is required.")
	err = validate_section_path(path, category_id)
	if err:
		errors.append(err)
	try:
		schema = clean_fields_schema(data.get("fields_schema") or [])
	except (ValueError, json.JSONDecodeError) as e:
		errors.append(str(e))
		schema = []
	if errors:
		return None, errors

	db = db_manager.get_session()
	try:
		cat = db.get(Category, category_id) if category_id else None
		if cat is None:
			slug = path
			if db.query(Category).filter(Category.slug == slug).first():
				slug = f"{path}-section"
			cat = Category(name=name, slug=slug)
			db.add(cat)
		cat.name = name
		cat.description = (data.get("description") or "").strip()
		cat.section_path = path
		cat.singular_name = (data.get("singular_name") or "").strip() or name.rstrip("s")
		cat.date_label = (data.get("date_label") or "").strip() or "Date"
		cat.show_time = bool(data.get("show_time"))
		cat.fields_schema = json.dumps(schema, ensure_ascii=False)
		if data.get("sort_order") not in (None, ""):
			try:
				cat.sort_order = int(data["sort_order"])
			except (TypeError, ValueError):
				pass
		elif cat.sort_order is None:
			cat.sort_order = (db.query(Category).count() + 1) * 10
		db.commit()
		return cat.to_dict(), []
	except SQLAlchemyError as e:
		db.rollback()
		return None, [f"Could not save the section: {e}"]
	finally:
		db.close()


def detach_section(category_id: int) -> bool:
	"""Stop publishing a category under its own address. Its posts and the category stay."""
	db = db_manager.get_session()
	try:
		cat = db.get(Category, category_id)
		if not cat:
			return False
		cat.section_path = None
		db.commit()
		return True
	except SQLAlchemyError:
		db.rollback()
		return False
	finally:
		db.close()


def reorder_sections(category_ids: List[int]) -> None:
	db = db_manager.get_session()
	try:
		for i, cid in enumerate(category_ids):
			cat = db.get(Category, cid)
			if cat:
				cat.sort_order = (i + 1) * 10
		db.commit()
	except SQLAlchemyError:
		db.rollback()
	finally:
		db.close()


def _post_card(post: Post) -> Dict[str, Any]:
	d = post.to_dict()
	d["published_at"] = post.published_at
	d["created_at_dt"] = post.created_at
	d["url"] = post_url(d)
	return d


def list_section_posts(category_slug: str, page: Any = 1, per_page: int = POSTS_PER_PAGE) -> Dict[str, Any]:
	"""One page of published posts in a category, newest first, with pagination info."""
	db = db_manager.get_session()
	try:
		base = (
			db.query(Post).join(Category, Post.category_id == Category.id)
			.filter(Post.status == "published", Category.slug == category_slug)
		)
		total = base.count()
		pages = max(1, math.ceil(total / per_page))
		try:
			page = int(page)
		except (TypeError, ValueError):
			page = 1
		page = max(1, min(page, pages))
		rows = (
			base.order_by(Post.published_at.desc(), Post.created_at.desc())
			.offset((page - 1) * per_page).limit(per_page).all()
		)
		return {
			"records": [_post_card(p) for p in rows],
			"page": page, "total_pages": pages, "total_items": total,
			"has_prev": page > 1, "has_next": page < pages,
			"prev_page": page - 1 if page > 1 else None,
			"next_page": page + 1 if page < pages else None,
		}
	finally:
		db.close()


def get_section_post(section_path: str, slug: str) -> Optional[Dict[str, Any]]:
	db = db_manager.get_session()
	try:
		row = (
			db.query(Post).join(Category, Post.category_id == Category.id)
			.filter(Post.slug == slug, Category.section_path == section_path, Post.status == "published")
			.first()
		)
		return _post_card(row) if row else None
	finally:
		db.close()


def parse_post_date(value: str) -> Optional[datetime.datetime]:
	"""Read the value of an <input type=date|datetime-local>; None if empty or invalid."""
	value = (value or "").strip()
	for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
		try:
			return datetime.datetime.strptime(value, fmt)
		except ValueError:
			continue
	return None


def format_input_date(dt: Optional[datetime.datetime], with_time: bool = False) -> str:
	if not dt:
		return ""
	return dt.strftime("%Y-%m-%dT%H:%M") if with_time else dt.strftime("%Y-%m-%d")


ENTRY_KINDS: Dict[str, Dict[str, Any]] = {}


def set_directories(directories: Dict[str, Dict[str, Any]]) -> None:
	ENTRY_KINDS.clear()
	ENTRY_KINDS.update(directories)


def set_extension_presets(presets: Dict[str, Dict[str, Any]]) -> None:
	EXTENSION_PRESETS.clear()
	EXTENSION_PRESETS.update(presets)


def directory_for_path(path: str):
	"""(kind, config) of the directory whose base path is /<path>/, else (None, None)."""
	for kind, cfg in ENTRY_KINDS.items():
		if cfg["base_path"].strip("/") == path:
			return kind, cfg
	return None, None


SAFE_URL_RE = re.compile(r"^(https?://|mailto:|tel:|/|#)", re.I)
SAFE_FILE_RE = re.compile(r"^(https?://|/uploads/|/static/|/|#)", re.I)


def blank_entry(kind: str) -> Dict[str, Any]:
	record = {
		"_id": "", "slug": "", "name": "", "name_bengali": "", "title": "",
		"department": "", "institution": "", "campus_address": "",
		"photo": "", "cv_url": "",
		"emails": [""], "personal_site": {"label": "", "url": ""},
		"profiles": [], "biography": [""], "education": [],
		"research_interests": [], "publications": [],
		"group": "active", "status": "published",
	}
	record.update((ENTRY_KINDS.get(kind) or {}).get("defaults", {}))
	return record


def _s(value: Any, limit: int = 500) -> str:
	return str(value if value is not None else "").strip()[:limit]


def _url(value: Any) -> str:
	v = _s(value, 1000)
	return v if (not v or SAFE_URL_RE.match(v)) else ""


def _file(value: Any) -> str:
	v = _s(value, 1000)
	return v if (not v or SAFE_FILE_RE.match(v)) else ""


def slugify_name(text: str) -> str:
	text = re.sub(r"[^\w\s-]", "", (text or "").lower(), flags=re.UNICODE)
	return re.sub(r"[\s_-]+", "-", text).strip("-")


def normalize_entry(kind: str, raw: Dict[str, Any]) -> Dict[str, Any]:
	"""Clean a record from the editor or an imported file into the stored shape."""
	cfg = ENTRY_KINDS[kind]
	raw = raw if isinstance(raw, dict) else {}
	groups = {g for g, _ in cfg["groups"]}
	name = _s(raw.get("name"), 255)
	slug = slugify_name(_s(raw.get("slug"), 255)) or slugify_name(name)
	group = raw.get("group") if raw.get("group") in groups else "active"
	status = "draft" if raw.get("status") == "draft" else "published"

	def lst(key, limit=50):
		v = raw.get(key)
		return v if isinstance(v, list) else []

	record = {
		"_id": _s(raw.get("_id"), 255) or slug,
		"slug": slug,
		"name": name,
		"name_bengali": _s(raw.get("name_bengali"), 255),
		"title": _s(raw.get("title"), 500),
		"department": _s(raw.get("department"), 255),
		"institution": _s(raw.get("institution"), 255),
		"campus_address": _s(raw.get("campus_address"), 500),
		"photo": _file(raw.get("photo")),
		"cv_url": _file(raw.get("cv_url")),
		"emails": [e for e in (_s(x, 254) for x in lst("emails")) if e][:10],
		"personal_site": {
			"label": _s((raw.get("personal_site") or {}).get("label") if isinstance(raw.get("personal_site"), dict) else "", 200),
			"url": _url((raw.get("personal_site") or {}).get("url") if isinstance(raw.get("personal_site"), dict) else ""),
		},
		"profiles": [
			{"name": _s(p.get("name"), 100), "url": _url(p.get("url"))}
			for p in lst("profiles") if isinstance(p, dict) and (p.get("name") or p.get("url"))
		][:30],
		"biography": [b for b in (_s(x, 5000) for x in lst("biography")) if b][:30],
		"education": [
			{"degree": _s(e.get("degree"), 300), "year": _s(e.get("year"), 30), "institute": _s(e.get("institute"), 300)}
			for e in lst("education") if isinstance(e, dict) and any(e.get(k) for k in ("degree", "year", "institute"))
		][:30],
		"research_interests": [r for r in (_s(x, 200) for x in lst("research_interests")) if r][:50],
		"publications": [
			{
				"authors": _s(p.get("authors"), 1000), "title": _s(p.get("title"), 1000),
				"journal": _s(p.get("journal"), 500), "year": _s(p.get("year"), 30),
				"type": _s(p.get("type"), 100), "link": _url(p.get("link")),
			}
			for p in lst("publications") if isinstance(p, dict) and (p.get("title") or p.get("authors"))
		][:500],
	}
	if kind == "staff":
		for k in ("profiles", "research_interests", "publications"):
			record[k] = []
		record["cv_url"] = ""
	record["_group"] = group
	record["_status"] = status
	return record


def _row_to_record(row: Entry) -> Dict[str, Any]:
	record = row.to_dict()
	record["photo"] = record.get("photo", "")
	return record


def list_entries(kind: str, groups: Optional[List[str]] = None, include_drafts: bool = False) -> List[Dict[str, Any]]:
	db = db_manager.get_session()
	try:
		q = db.query(Entry).filter(Entry.kind == kind)
		if groups:
			q = q.filter(Entry.group.in_(groups))
		if not include_drafts:
			q = q.filter(Entry.status == "published")
		return [_row_to_record(r) for r in q.order_by(Entry.sort_order, Entry.id).all()]
	except SQLAlchemyError as e:
		print(f"Error listing entries: {e}")
		return []
	finally:
		db.close()


def get_entry(kind: str, slug: str, include_drafts: bool = False) -> Optional[Dict[str, Any]]:
	db = db_manager.get_session()
	try:
		q = db.query(Entry).filter(Entry.kind == kind, Entry.slug == slug)
		if not include_drafts:
			q = q.filter(Entry.status == "published")
		row = q.first()
		return _row_to_record(row) if row else None
	finally:
		db.close()


def save_entry(kind: str, raw: Dict[str, Any], original_slug: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], List[str]]:
	"""Create or update one record. ``original_slug`` is the slug it had when it was opened."""
	rec = normalize_entry(kind, raw)
	errors = []
	if not rec["name"]:
		errors.append("Name is required.")
	if not rec["slug"]:
		errors.append("A slug could not be made from the name. Please type one.")
	if errors:
		return None, errors
	group, status = rec.pop("_group"), rec.pop("_status")

	db = db_manager.get_session()
	try:
		row = None
		if original_slug:
			row = db.query(Entry).filter(Entry.kind == kind, Entry.slug == original_slug).first()
		clash = db.query(Entry).filter(Entry.kind == kind, Entry.slug == rec["slug"]).first()
		if clash and (row is None or clash.id != row.id):
			return None, [f"Another {ENTRY_KINDS[kind]['singular']} already uses the slug '{rec['slug']}'."]
		if row is None:
			top = db.query(Entry).filter(Entry.kind == kind).count()
			row = Entry(kind=kind, sort_order=(top + 1) * 10)
			db.add(row)
		row.slug, row.name, row.group, row.status = rec["slug"], rec["name"], group, status
		row.data = json.dumps(rec, ensure_ascii=False)
		db.commit()
		return _row_to_record(row), []
	except SQLAlchemyError as e:
		db.rollback()
		return None, [f"Could not save: {e}"]
	finally:
		db.close()


def delete_entry(kind: str, slug: str) -> bool:
	db = db_manager.get_session()
	try:
		row = db.query(Entry).filter(Entry.kind == kind, Entry.slug == slug).first()
		if not row:
			return False
		db.delete(row)
		db.commit()
		return True
	except SQLAlchemyError:
		db.rollback()
		return False
	finally:
		db.close()


def reorder_entries(kind: str, slugs: List[str]) -> None:
	db = db_manager.get_session()
	try:
		for i, slug in enumerate(slugs):
			row = db.query(Entry).filter(Entry.kind == kind, Entry.slug == slug).first()
			if row:
				row.sort_order = (i + 1) * 10
		db.commit()
	except SQLAlchemyError:
		db.rollback()
	finally:
		db.close()


def import_entries(kind: str, records: Any, replace: bool = False) -> Tuple[int, int]:
	"""Add or update records from a faculty.json-style list. Returns (added, updated)."""
	if not isinstance(records, list):
		raise ValueError("Expected a JSON list of records.")
	added = updated = 0
	if replace:
		db = db_manager.get_session()
		try:
			db.query(Entry).filter(Entry.kind == kind).delete()
			db.commit()
		finally:
			db.close()
	for raw in records:
		if not isinstance(raw, dict):
			continue
		rec = normalize_entry(kind, raw)
		if not rec["slug"]:
			continue
		exists = get_entry(kind, rec["slug"], include_drafts=True) is not None
		saved, errs = save_entry(kind, raw, original_slug=rec["slug"] if exists else None)
		if saved:
			updated += exists
			added += not exists
	return added, updated


def export_entries(kind: str) -> List[Dict[str, Any]]:
	"""Records in the faculty.json shape (without the database columns)."""
	out = []
	for rec in list_entries(kind, include_drafts=True):
		for key in ("id", "kind", "sort_order"):
			rec.pop(key, None)
		out.append(rec)
	return out


def pretty_date(value: Any, with_time: bool = False) -> str:
	"""Format a datetime, or a stored 'YYYY-MM-DD[THH:MM]' string, for display ('20 September 2026, 10:00 AM')."""
	if not value:
		return ""
	if isinstance(value, str):
		value = parse_post_date(value[:16])
		if value is None:
			return ""
	text = f"{value.day} {value.strftime('%B %Y')}"
	if with_time:
		text += value.strftime(", %I:%M %p").replace(", 0", ", ")
	return text


POST_SORTS = {
	"newest": ("Newest date first", lambda: (Post.published_at.desc(), Post.created_at.desc())),
	"oldest": ("Oldest date first", lambda: (Post.published_at.asc(), Post.created_at.asc())),
	"edited": ("Recently edited", lambda: (Post.updated_at.desc(),)),
	"title": ("Title A–Z", lambda: (Post.title.asc(),)),
}
ADMIN_PAGE_SIZE = 20


def search_posts(
	q: str = "", category: str = "", status: str = "", author_id: Optional[int] = None,
	date_from: str = "", date_to: str = "", sort: str = "newest", page: Any = 1,
	only_author_id: Optional[int] = None, per_page: int = ADMIN_PAGE_SIZE,
) -> Dict[str, Any]:
	"""Posts for the admin list. ``only_author_id`` restricts to one author's posts (contributors)."""
	from sqlalchemy import or_
	db = db_manager.get_session()
	try:
		query = db.query(Post).outerjoin(Category, Post.category_id == Category.id)
		term = (q or "").strip()
		if term:
			like = f"%{term}%"
			query = query.filter(or_(Post.title.ilike(like), Post.slug.ilike(like), Post.excerpt.ilike(like), Post.content.ilike(like)))
		if category == "none":
			query = query.filter(Post.category_id.is_(None))
		elif category:
			query = query.filter(Category.slug == category)
		if status in ("published", "draft"):
			query = query.filter(Post.status == status)
		if only_author_id:
			query = query.filter(Post.author_id == only_author_id)
		elif author_id:
			query = query.filter(Post.author_id == author_id)
		start, end = parse_post_date(date_from), parse_post_date(date_to)
		if start:
			query = query.filter(func_coalesce(Post.published_at, Post.created_at) >= start)
		if end:
			query = query.filter(func_coalesce(Post.published_at, Post.created_at) < end + datetime.timedelta(days=1))
		total = query.count()
		pages = max(1, math.ceil(total / per_page))
		try:
			page = max(1, min(int(page), pages))
		except (TypeError, ValueError):
			page = 1
		order = POST_SORTS.get(sort, POST_SORTS["newest"])[1]()
		rows = query.order_by(*order).offset((page - 1) * per_page).limit(per_page).all()
		records = []
		for p in rows:
			d = p.to_dict()
			d["id"] = p.id
			d["public_url"] = post_url(d)
			d["date"] = p.published_at or p.created_at
			records.append(d)
		return {"records": records, "total": total, "page": page, "pages": pages, "per_page": per_page}
	finally:
		db.close()


def func_coalesce(*cols):
	from sqlalchemy import func
	return func.coalesce(*cols)


def bulk_update_posts(
	action: str, ids: List[int], can_publish: bool, can_edit_others: bool, user_id: int,
	category_slug: str = "",
) -> Tuple[int, int]:
	"""
	Apply ``action`` (publish | draft | delete | category) to posts. Returns (changed, skipped).
	Posts the user may not touch (other people's posts without permission) are skipped.
	"""
	changed = skipped = 0
	db = db_manager.get_session()
	try:
		category = None
		if action == "category":
			category = db.query(Category).filter(Category.slug == category_slug).first() if category_slug else None
		for post in db.query(Post).filter(Post.id.in_(ids)).all():
			if not can_edit_others and post.author_id != user_id:
				skipped += 1
				continue
			if action == "publish":
				if not can_publish:
					skipped += 1
					continue
				post.status = "published"
				post.published_at = post.published_at or datetime.datetime.utcnow()
			elif action == "draft":
				post.status = "draft"
			elif action == "category":
				post.category_id = category.id if category else None
			elif action == "delete":
				db.delete(post)
			else:
				skipped += 1
				continue
			changed += 1
		db.commit()
	except SQLAlchemyError as e:
		db.rollback()
		print(f"Bulk update failed: {e}")
		return 0, len(ids)
	finally:
		db.close()
	return changed, skipped
