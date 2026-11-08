"""
Site export / import ("site package").

A package is a .zip with:
	manifest.json         what it contains and which FlexaFlow / extension / theme versions made it
	content.json          sections & categories, pages, posts (tags, cover image, section fields)
	settings.json         site settings (menu, title, ...)          [option]
	extension_data.json   records that extensions own (e.g. faculty) and extension settings   [option]
	media.json + uploads/ the media library and its files           [option]
	packages/extensions/<slug>/...   installed extensions           [option]
	packages/themes/<name>/...       installed themes               [option]

Importing never activates anything by itself: extensions and themes in a package are installed
but stay inactive (unless the administrator ticks "restore active state"), so an import cannot
clash with what is already running. Users and passwords are never exported.
"""

import datetime
import io
import json
import os
import shutil
import tempfile
import zipfile
from typing import Any, Dict, List

from sqlalchemy.exc import SQLAlchemyError

import extension_manager as em
import theme_manager as tm
from data_store import (
	Category, Entry, Media, MediaLibrary, Page, Post, SiteSetting, Tag, db_manager, update_tag_counts,
)

FORMAT = "flexaflow-package"
FORMAT_VERSION = 1
UPLOADS = "uploads"
STATE_KEYS = {"active_extensions", "theme"}
SKIP_DIRS = {"__pycache__", ".git"}


def _iso(value):
	return value.isoformat() if isinstance(value, datetime.datetime) else None


def _dt(value):
	try:
		return datetime.datetime.fromisoformat(value) if value else None
	except (TypeError, ValueError):
		return None


def _json(text, default):
	try:
		return json.loads(text) if text else default
	except (TypeError, ValueError):
		return default


def _add_tree(zf: zipfile.ZipFile, src: str, dest: str) -> int:
	count = 0
	for root, dirs, files in os.walk(src):
		dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
		for name in files:
			if name.endswith(".pyc") or name == em.THEME_MARKER:
				continue
			full = os.path.join(root, name)
			zf.write(full, os.path.join(dest, os.path.relpath(full, src)).replace(os.sep, "/"))
			count += 1
	return count


def export_package(
	site_title: str, active_extensions: List[str], active_theme: str, *,
	content=True, settings=True, extension_data=True, media=False, extensions=False, themes=False,
	selected_extensions=None, selected_themes=None,
) -> bytes:
	db = db_manager.get_session()
	buf = io.BytesIO()
	try:
		with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
			manifest: Dict[str, Any] = {
				"format": FORMAT, "format_version": FORMAT_VERSION, "core_version": em.CORE_VERSION,
				"exported_at": datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
				"site_title": site_title,
				"includes": {
					"content": content, "settings": settings, "extension_data": extension_data,
					"media": media, "extensions": extensions, "themes": themes,
				},
				"active_extensions": active_extensions, "active_theme": active_theme,
				"extensions": [], "themes": [],
			}
			rows = db.query(SiteSetting).all()
			if content:
				cats = db.query(Category).order_by(Category.id).all()
				zf.writestr("content.json", json.dumps({
					"categories": [{
						"name": c.name, "slug": c.slug, "description": c.description or "",
						"section_path": c.section_path, "singular_name": c.singular_name,
						"fields_schema": _json(c.fields_schema, []), "sort_order": c.sort_order or 0,
						"date_label": c.date_label, "show_time": bool(c.show_time),
					} for c in cats],
					"pages": [{
						"slug": p.slug, "title": p.title, "content": p.content or "", "description": p.description or "",
						"status": p.status, "template": p.template, "fields": _json(p.fields, {}),
						"created_at": _iso(p.created_at),
					} for p in db.query(Page).order_by(Page.id).all()],
					"posts": [{
						"slug": p.slug, "title": p.title, "content": p.content or "", "excerpt": p.excerpt or "",
						"status": p.status, "category": p.category.slug if p.category else None,
						"tags": [t.name for t in p.tags], "cover_image": p.cover_image or "",
						"meta": _json(p.meta, {}), "published_at": _iso(p.published_at), "created_at": _iso(p.created_at),
					} for p in db.query(Post).order_by(Post.id).all()],
				}, ensure_ascii=False, indent=1))
			settings_all = {}
			for row in rows:
				settings_all[row.key] = _json(row.value, row.value)
			if settings:
				zf.writestr("settings.json", json.dumps({
					k: v for k, v in settings_all.items() if not k.startswith("ext_") and k not in STATE_KEYS
				}, ensure_ascii=False, indent=1))
			if extension_data:
				zf.writestr("extension_data.json", json.dumps({
					"entries": [{
						"kind": e.kind, "slug": e.slug, "name": e.name, "group": e.group, "status": e.status,
						"sort_order": e.sort_order or 0, "data": _json(e.data, {}),
					} for e in db.query(Entry).order_by(Entry.kind, Entry.sort_order, Entry.id).all()],
					"settings": {k: v for k, v in settings_all.items() if k.startswith("ext_")},
				}, ensure_ascii=False, indent=1))
			if media:
				media_rows = []
				for table, model in (("media", Media), ("library", MediaLibrary)):
					for m in db.query(model).all():
						media_rows.append({"table": table, **{
							c: getattr(m, c) for c in (
								"filename", "original_filename", "mime_type", "file_size", "width", "height",
								"alt_text", "caption", "title", "description", "thumbnail")
						}})
				zf.writestr("media.json", json.dumps(media_rows, ensure_ascii=False, indent=1))
				if os.path.isdir(UPLOADS):
					_add_tree(zf, UPLOADS, "uploads")
			if extensions:
				for e in em.list_installed():
					if e.get("error") or (selected_extensions is not None and e["slug"] not in selected_extensions):
						continue
					_add_tree(zf, os.path.join(em.EXTENSIONS_DIR, e["folder"]), f"packages/extensions/{e['slug']}")
					manifest["extensions"].append({"slug": e["slug"], "name": e["name"], "version": e["version"]})
			if themes:
				for t in tm.list_themes():
					if t["slug"] == "default" or t.get("extension") or (selected_themes is not None and t["slug"] not in selected_themes):
						continue
					_add_tree(zf, os.path.join(tm.THEMES_DIR, t["slug"]), f"packages/themes/{t['slug']}")
					manifest["themes"].append({"slug": t["slug"], "name": t["name"], "version": t["version"]})
			zf.writestr("manifest.json", json.dumps(manifest, indent=1))
	finally:
		db.close()
	return buf.getvalue()


def read_manifest(path_or_stream) -> Dict[str, Any]:
	try:
		with zipfile.ZipFile(path_or_stream) as zf:
			return json.loads(zf.read("manifest.json"))
	except (zipfile.BadZipFile, KeyError, ValueError):
		raise em.ExtensionError("This is not a FlexaFlow site package (.zip).")


def import_package(stream, *, content=True, settings=True, extension_data=True, media=True,
		extensions=True, themes=True, overwrite=False, restore_state=False, write_setting=None) -> List[str]:
	"""Returns a list of human-readable result lines."""
	report: List[str] = []
	work = tempfile.mkdtemp(prefix="pkg-")
	try:
		try:
			with zipfile.ZipFile(stream) as zf:
				if "manifest.json" not in zf.namelist():
					raise em.ExtensionError("This is not a FlexaFlow site package (.zip).")
				em._safe_extract(zf, work, em.MAX_ZIP_BYTES * 8)
		except zipfile.BadZipFile:
			raise em.ExtensionError("That file is not a valid .zip archive.")
		manifest = json.load(open(os.path.join(work, "manifest.json"), encoding="utf-8"))
		if manifest.get("format") != FORMAT:
			raise em.ExtensionError("This is not a FlexaFlow site package.")
		if int(manifest.get("format_version", 1)) > FORMAT_VERSION:
			raise em.ExtensionError("This package was made by a newer FlexaFlow. Update FlexaFlow first.")

		def load(name, default):
			p = os.path.join(work, name)
			return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else default

		if extensions:
			base = os.path.join(work, "packages", "extensions")
			for slug in sorted(os.listdir(base)) if os.path.isdir(base) else []:
				try:
					m = em.install_dir(os.path.join(base, slug), replace=overwrite)
					report.append(f"Extension {m['name']} {m['version']} installed (inactive).")
				except em.ExtensionError as e:
					report.append(f"Extension {slug} skipped: {e}")
		if themes:
			base = os.path.join(work, "packages", "themes")
			for slug in sorted(os.listdir(base)) if os.path.isdir(base) else []:
				try:
					m = tm.install_dir(os.path.join(base, slug), replace=overwrite)
					report.append(f"Theme {m['name']} {m['version']} installed (not activated).")
				except em.ExtensionError as e:
					report.append(f"Theme {slug} skipped: {e}")

		db = db_manager.get_session()
		try:
			if media and os.path.exists(os.path.join(work, "media.json")):
				copied = 0
				src = os.path.join(work, "uploads")
				if os.path.isdir(src):
					for root, _d, files in os.walk(src):
						for name in files:
							rel = os.path.relpath(os.path.join(root, name), src)
							dest = os.path.join(UPLOADS, rel)
							if overwrite or not os.path.exists(dest):
								os.makedirs(os.path.dirname(dest), exist_ok=True)
								shutil.copy2(os.path.join(root, name), dest)
								copied += 1
				added = 0
				for m in load("media.json", []):
					model = Media if m.get("table") == "media" else MediaLibrary
					if db.query(model).filter(model.filename == m["filename"]).first():
						continue
					db.add(model(**{k: v for k, v in m.items() if k != "table"}))
					added += 1
				db.commit()
				report.append(f"Media: {copied} file(s) copied, {added} library item(s) added.")

			if content and os.path.exists(os.path.join(work, "content.json")):
				data = load("content.json", {})
				c_add = c_upd = 0
				for c in data.get("categories", []):
					row = db.query(Category).filter(Category.slug == c["slug"]).first()
					if row and not overwrite:
						continue
					path = c.get("section_path")
					if path and db.query(Category).filter(Category.section_path == path, Category.slug != c["slug"]).first():
						path = None
					if not row:
						row = Category(slug=c["slug"], name=c["name"])
						db.add(row)
						c_add += 1
					else:
						c_upd += 1
					row.name, row.description = c["name"], c.get("description", "")
					row.section_path, row.singular_name = path, c.get("singular_name")
					row.fields_schema = json.dumps(c.get("fields_schema") or [], ensure_ascii=False)
					row.sort_order, row.date_label = c.get("sort_order", 0), c.get("date_label")
					row.show_time = bool(c.get("show_time"))
				db.flush()
				p_add = p_upd = 0
				for p in data.get("pages", []):
					row = db.query(Page).filter(Page.slug == p["slug"]).first()
					if row and not overwrite:
						continue
					if not row:
						row = Page(slug=p["slug"])
						db.add(row)
						p_add += 1
					else:
						p_upd += 1
					row.title, row.content, row.description = p["title"], p.get("content", ""), p.get("description", "")
					row.status, row.template = p.get("status", "published"), p.get("template") or "default"
					row.fields = json.dumps(p.get("fields") or {}, ensure_ascii=False)
				n_add = n_upd = 0
				for p in data.get("posts", []):
					row = db.query(Post).filter(Post.slug == p["slug"]).first()
					if row and not overwrite:
						continue
					if not row:
						row = Post(slug=p["slug"])
						db.add(row)
						n_add += 1
					else:
						n_upd += 1
					cat = db.query(Category).filter(Category.slug == p.get("category")).first() if p.get("category") else None
					row.title, row.content, row.excerpt = p["title"], p.get("content", ""), p.get("excerpt", "")
					row.status, row.category_id = p.get("status", "published"), cat.id if cat else None
					row.cover_image = p.get("cover_image") or None
					row.meta = json.dumps(p.get("meta") or {}, ensure_ascii=False)
					row.published_at = _dt(p.get("published_at"))
					if _dt(p.get("created_at")):
						row.created_at = _dt(p["created_at"])
					row.tags = []
					for name in p.get("tags", []):
						tag = db.query(Tag).filter(Tag.name == name).first()
						if not tag:
							tag = Tag(name=name)
							db.add(tag)
							db.flush()
						row.tags.append(tag)
				db.commit()
				update_tag_counts()
				report.append(
					f"Content: {c_add + c_upd} section/category, {p_add + p_upd} page and {n_add + n_upd} post record(s) imported"
					f"{'' if overwrite else ' (existing ones with the same address were kept)'}.")

			if settings and os.path.exists(os.path.join(work, "settings.json")):
				n = 0
				for key, value in load("settings.json", {}).items():
					if key in STATE_KEYS and not restore_state:
						continue
					row = db.query(SiteSetting).filter(SiteSetting.key == key).first()
					if row and not overwrite:
						continue
					payload = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
					if row:
						row.value = payload
					else:
						db.add(SiteSetting(key=key, value=payload))
					n += 1
				db.commit()
				report.append(f"Settings: {n} value(s) imported.")

			if extension_data and os.path.exists(os.path.join(work, "extension_data.json")):
				data = load("extension_data.json", {})
				added = updated = 0
				for e in data.get("entries", []):
					row = db.query(Entry).filter(Entry.kind == e["kind"], Entry.slug == e["slug"]).first()
					if row and not overwrite:
						continue
					if not row:
						row = Entry(kind=e["kind"], slug=e["slug"])
						db.add(row)
						added += 1
					else:
						updated += 1
					row.name, row.group, row.status = e["name"], e.get("group", "active"), e.get("status", "published")
					row.sort_order = e.get("sort_order", 0)
					row.data = json.dumps(e.get("data") or {}, ensure_ascii=False)
				for key, value in (data.get("settings") or {}).items():
					row = db.query(SiteSetting).filter(SiteSetting.key == key).first()
					if row and not overwrite:
						continue
					payload = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
					if row:
						row.value = payload
					else:
						db.add(SiteSetting(key=key, value=payload))
				db.commit()
				report.append(f"Extension data: {added + updated} record(s) imported. They show up when their extension is active.")
		except SQLAlchemyError as e:
			db.rollback()
			raise em.ExtensionError(f"Import failed: {e}")
		finally:
			db.close()

		if restore_state and write_setting:
			if manifest.get("active_extensions"):
				write_setting("active_extensions", [s for s in manifest["active_extensions"] if os.path.isdir(os.path.join(em.EXTENSIONS_DIR, s))])
			if manifest.get("active_theme") and os.path.isdir(os.path.join(tm.THEMES_DIR, manifest["active_theme"])):
				write_setting("theme", manifest["active_theme"])
			report.append("Active extensions and theme restored from the package.")
		return report
	finally:
		shutil.rmtree(work, ignore_errors=True)
