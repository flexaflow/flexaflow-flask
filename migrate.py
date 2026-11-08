"""
Move a site between FlexaFlow and WordPress / Blogger.

Import  (parse_*  ->  Parsed  ->  apply_import)
	WordPress   Tools > Export > "All content"  (WXR .xml)
	Blogger     Settings > Back up content      (Atom .xml, also the Google Takeout feed)

Export  (export_*)
	WordPress   a WXR 1.2 file for Tools > Import > WordPress
	Blogger     an Atom backup file for Settings > Back up content > Restore

Design notes
	* Imported HTML is run through an allow-list sanitizer (migrate_utils.sanitize_html).
	* Nothing is overwritten unless asked: conflicts are skipped, renamed or updated by choice.
	* Users and passwords are never imported or exported.
	* Comments are not part of FlexaFlow and are not imported (they are counted in the report).
"""

import datetime
import html
import json
import os
import re
import time
import zlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse
from xml.sax.saxutils import escape

from sqlalchemy.exc import SQLAlchemyError

from data_store import Category, Media, Page, Post, SiteSetting, Tag, User, db_manager
from migrate_utils import (
	MigrationError, html_to_wp, image_urls, local, make_absolute, parse_xml_bytes, safe_fetch,
	sanitize_html, slugify, strip_tags, wp_to_html,
)

WP_PUBLISHED = {"publish"}
WP_DRAFT_LIKE = {"draft", "pending", "private", "future"}
WP_SKIPPED_STATUS = {"trash", "auto-draft", "inherit"}
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp")
GOOGLE_IMAGE_HOSTS = ("blogger.googleusercontent.com", "googleusercontent.com", "bp.blogspot.com", "blogspot.com", "ggpht.com")


@dataclass
class Item:
	kind: str
	title: str
	slug: str
	content: str
	excerpt: str = ""
	published: bool = True
	date: Optional[datetime.datetime] = None
	categories: List[Dict[str, str]] = field(default_factory=list)
	tags: List[str] = field(default_factory=list)
	cover_url: str = ""
	source_id: str = ""
	parent: str = ""


@dataclass
class Parsed:
	source: str
	site_title: str = ""
	site_description: str = ""
	site_url: str = ""
	items: List[Item] = field(default_factory=list)
	menu: List[Dict[str, Any]] = field(default_factory=list)
	skipped: Dict[str, int] = field(default_factory=dict)
	media_hosts: Set[str] = field(default_factory=set)

	def skip(self, what: str, n: int = 1):
		self.skipped[what] = self.skipped.get(what, 0) + n


def _parse_dt(*values) -> Optional[datetime.datetime]:
	for v in values:
		v = (v or "").strip()
		if not v or v.startswith("0000"):
			continue
		for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
			try:
				d = datetime.datetime.strptime(v.replace("Z", "+0000"), fmt)
				if d.tzinfo:
					d = d.astimezone(datetime.timezone.utc).replace(tzinfo=None)
				return d
			except ValueError:
				continue
	return None


def _kids(el, name):
	return [c for c in el if local(c.tag) == name]


def _text(el, name, default=""):
	for c in el:
		if local(c.tag) == name:
			return (c.text or "").strip()
	return default


def _host(url: str) -> str:
	return (urlparse(url or "").hostname or "").lower()


def detect_format(data: bytes) -> Optional[str]:
	"""'wordpress' | 'blogger' | 'flexaflow' | None, from the start of an XML file."""
	head = data[:6000].decode("utf-8", "ignore")
	if "<flexaflow_export" in head:
		return "flexaflow"
	if "wordpress.org/export" in head or "<wp:wxr_version" in head:
		return "wordpress"
	if "<feed" in head and ("blogger" in head.lower()):
		return "blogger"
	if "<rss" in head and "wp:" in data[:60000].decode("utf-8", "ignore"):
		return "wordpress"
	return None


def parse_wordpress(data: bytes) -> Parsed:
	root = parse_xml_bytes(data)
	channel = next((c for c in root if local(c.tag) == "channel"), None)
	if channel is None:
		raise MigrationError("This does not look like a WordPress export (no <channel>).")
	out = Parsed("wordpress", site_title=_text(channel, "title"), site_description=_text(channel, "description"),
		site_url=_text(channel, "base_site_url") or _text(channel, "link"))
	if out.site_url:
		out.media_hosts.add(_host(out.site_url))
	blog_url = _text(channel, "base_blog_url")
	if blog_url:
		out.media_hosts.add(_host(blog_url))

	raw: List[Dict[str, Any]] = []
	for el in _kids(channel, "item"):
		meta = {}
		for pm in _kids(el, "postmeta"):
			meta[_text(pm, "meta_key")] = _text(pm, "meta_value")
		cats, tags, menus = [], [], []
		for c in _kids(el, "category"):
			name, nice, domain = (c.text or "").strip(), c.get("nicename") or "", c.get("domain") or ""
			if not name:
				continue
			if domain == "category":
				cats.append({"name": name, "slug": slugify(nice) or slugify(name)})
			elif domain == "post_tag":
				tags.append(name)
			elif domain == "nav_menu":
				menus.append(nice or slugify(name))
		raw.append({
			"id": _text(el, "post_id"), "type": _text(el, "post_type"), "status": _text(el, "status"),
			"title": _text(el, "title"), "slug": _text(el, "post_name"),
			"content": next((c.text or "" for c in el if local(c.tag) == "encoded" and "content" in c.tag), ""),
			"excerpt": next((c.text or "" for c in el if local(c.tag) == "encoded" and "excerpt" in c.tag), ""),
			"date": _parse_dt(_text(el, "post_date_gmt"), _text(el, "post_date")),
			"parent": _text(el, "post_parent"), "order": int(_text(el, "menu_order") or 0),
			"attachment_url": _text(el, "attachment_url"), "meta": meta, "cats": cats, "tags": tags, "menus": menus,
		})

	attachments = {r["id"]: r["attachment_url"] for r in raw if r["type"] == "attachment" and r["attachment_url"]}
	for url in attachments.values():
		out.media_hosts.add(_host(url))
	by_id = {r["id"]: r for r in raw}

	for r in raw:
		kind = r["type"]
		if kind == "attachment":
			continue
		if kind == "nav_menu_item":
			continue
		if kind not in ("post", "page"):
			out.skip(f"'{kind or 'unknown'}' items (custom post types)")
			continue
		if r["status"] in WP_SKIPPED_STATUS:
			out.skip(f"{r['status']} items")
			continue
		cats = [c for c in r["cats"] if c["slug"] != "uncategorized"]
		tags = list(r["tags"]) + [c["name"] for c in cats[1:]]
		thumb = r["meta"].get("_thumbnail_id", "")
		out.items.append(Item(
			kind=kind, title=r["title"] or "(untitled)", slug=slugify(r["slug"]) if r["slug"] else "",
			content=wp_to_html(r["content"]), excerpt=strip_tags(wp_to_html(r["excerpt"])),
			published=r["status"] in WP_PUBLISHED, date=r["date"], categories=cats[:1] if kind == "post" else [],
			tags=list(dict.fromkeys(tags)) if kind == "post" else [], cover_url=attachments.get(thumb, ""),
			source_id=r["id"], parent=r["parent"] if r["parent"] != "0" else ""))

	by_menu: Dict[str, List[Dict[str, Any]]] = {}
	for r in raw:
		if r["type"] == "nav_menu_item":
			for m in r["menus"] or ["menu"]:
				by_menu.setdefault(m, []).append(r)
	if by_menu:
		entries = sorted(max(by_menu.values(), key=len), key=lambda r: r["order"])
		built: Dict[str, Dict[str, Any]] = {}
		for r in entries:
			m = r["meta"]
			label, url = r["title"], m.get("_menu_item_url", "")
			target = by_id.get(m.get("_menu_item_object_id", ""))
			if m.get("_menu_item_type") == "post_type" and target:
				label = label or target["title"]
				slug = slugify(target["slug"]) or slugify(target["title"])
				url = f"/{slug}" if target["type"] == "page" else f"/post/{slug}"
			elif m.get("_menu_item_type") == "taxonomy" and m.get("_menu_item_object") == "category":
				url = f"/category/{slugify(label)}"
			elif url and _host(url) in out.media_hosts:
				url = urlparse(url).path or "/"
			if label and url:
				built[r["id"]] = {"label": label[:100], "url": url, "children": [], "parent": m.get("_menu_item_menu_item_parent", "0")}
		for key, node in built.items():
			parent = built.get(node["parent"])
			if parent and node["parent"] != key:
				parent["children"].append(node)
		out.menu = [_menu_node(n) for n in built.values() if n["parent"] not in built]
	return out


def _menu_node(n):
	d = {"label": n["label"], "url": n["url"]}
	if n["children"]:
		d["children"] = [{"label": c["label"], "url": c["url"]} for c in n["children"]]
	return d


def parse_blogger(data: bytes) -> Parsed:
	root = parse_xml_bytes(data)
	if local(root.tag) != "feed":
		raise MigrationError("This does not look like a Blogger backup (expected an Atom <feed>).")
	out = Parsed("blogger", site_title=_text(root, "title"), site_description=strip_tags(_text(root, "subtitle")))
	for link in _kids(root, "link"):
		if link.get("rel") == "alternate" and link.get("href"):
			out.site_url = link.get("href")
	out.media_hosts.update(GOOGLE_IMAGE_HOSTS)
	if out.site_url:
		out.media_hosts.add(_host(out.site_url))

	for entry in _kids(root, "entry"):
		kind_term, labels = "", []
		for c in _kids(entry, "category"):
			scheme, term = c.get("scheme") or "", c.get("term") or ""
			if scheme.endswith("#kind"):
				kind_term = term.rsplit("#", 1)[-1]
			elif term:
				labels.append(term)
		btype = _text(entry, "type").lower()
		kind = kind_term or btype
		if kind in ("comment", "settings", "template", "layout"):
			out.skip(f"Blogger {kind}s" if kind != "settings" else "Blogger settings")
			continue
		if kind not in ("post", "page"):
			out.skip("other Blogger entries")
			continue
		status = _text(entry, "status").upper()
		draft = status == "DRAFT" or any((_text(c, "draft").lower() == "yes") for c in _kids(entry, "control"))
		if status == "DELETED":
			out.skip("deleted entries")
			continue
		content_el = next(iter(_kids(entry, "content")), None)
		body = content_el.text if content_el is not None and content_el.text else ""
		if content_el is not None and content_el.get("type") == "xhtml" and len(content_el):
			body = "".join(ET.tostring(c, encoding="unicode") for c in content_el)
		filename = _text(entry, "filename")
		slug = slugify(os.path.splitext(os.path.basename(filename))[0]) if filename else ""
		title = _text(entry, "title") or "(untitled)"
		out.items.append(Item(
			kind=kind, title=title, slug=slug, content=sanitize_html(body),
			excerpt=_text(entry, "metaDescription"), published=not draft,
			date=_parse_dt(_text(entry, "published"), _text(entry, "created")),
			tags=list(dict.fromkeys(labels)), source_id=_text(entry, "id")))
	return out


@dataclass
class ImportOptions:
	conflict: str = "skip"
	include_drafts: bool = True
	import_pages: bool = True
	import_settings: bool = False
	import_menu: bool = False
	download_media: bool = False
	max_downloads: int = 150
	time_budget: float = 120.0
	author_id: Optional[int] = None
	reserved_slugs: Set[str] = field(default_factory=set)


StoreMedia = Callable[[bytes, str, str], Optional[str]]


def _unique(base: str, used: Set[str], fallback: str) -> str:
	base = (base or fallback)[:240]
	slug, n = base, 2
	while slug in used:
		slug = f"{base}-{n}"
		n += 1
	return slug


def _download_media(parsed: Parsed, opts: ImportOptions, store: StoreMedia, report: List[str]) -> Dict[str, str]:
	"""Download images that live on the old site; returns {old url as it appears in HTML: local url}."""
	wanted: List[str] = []
	for it in parsed.items:
		urls = image_urls(it.content) + [html.escape(it.cover_url, quote=True)] * bool(it.cover_url)
		urls += re.findall(r'<a\b[^>]*?\bhref="([^"]+)"', it.content, re.I)
		for u in urls:
			plain = html.unescape(u)
			host = _host(plain)
			path = urlparse(plain).path.lower()
			is_google = any(host == h or host.endswith("." + h) for h in GOOGLE_IMAGE_HOSTS)
			if plain.startswith("http") and host in parsed.media_hosts or is_google:
				if path.endswith(IMAGE_EXT) or is_google or "/wp-content/uploads/" in path:
					wanted.append(u)
	wanted = list(dict.fromkeys(wanted))
	mapping: Dict[str, str] = {}
	started, failed = time.time(), 0
	for u in wanted:
		if len(mapping) >= opts.max_downloads or time.time() - started > opts.time_budget:
			break
		plain = html.unescape(u)
		try:
			data, mime = safe_fetch(plain)
			name = os.path.basename(urlparse(plain).path) or "image"
			local_url = store(data, name, mime)
			if local_url:
				mapping[u] = local_url
			else:
				failed += 1
		except MigrationError:
			failed += 1
	left = len(wanted) - len(mapping) - failed
	report.append(f"Media: {len(mapping)} file(s) copied from the old site"
		+ (f", {failed} could not be downloaded" if failed else "")
		+ (f", {left} left on the old site (download limit or time reached; run the command-line import to copy them all)" if left > 0 else "") + ".")
	return mapping


def apply_import(parsed: Parsed, opts: ImportOptions, *, store_media: Optional[StoreMedia] = None) -> List[str]:
	report: List[str] = []
	db = db_manager.get_session()
	try:
		media_map: Dict[str, str] = {}
		if opts.download_media and store_media:
			media_map = _download_media(parsed, opts, store_media, report)
		elif opts.download_media:
			report.append("Media could not be copied here; images still point at the old site.")

		post_slugs = {s for (s,) in db.query(Post.slug).all()}
		page_slugs = {s for (s,) in db.query(Page.slug).all()}
		section_paths = {p for (p,) in db.query(Category.section_path).filter(Category.section_path.isnot(None)).all()}
		cat_by_slug = {c.slug: c for c in db.query(Category).all()}
		tag_by_name = {t.name.lower(): t for t in db.query(Tag).all()}
		counts = {"post": [0, 0, 0], "page": [0, 0, 0]}
		new_cats = 0

		def rewrite(text: str) -> str:
			for old, new in media_map.items():
				text = text.replace(old, new)
			return text

		for it in parsed.items:
			if it.kind == "page" and not opts.import_pages:
				continue
			if not it.published and not opts.include_drafts:
				counts[it.kind][2] += 1
				continue
			used = post_slugs if it.kind == "post" else page_slugs
			slug = it.slug or slugify(it.title) or f"{it.kind}-{it.source_id or len(used) + 1}"
			if it.kind == "page" and (slug in opts.reserved_slugs or slug in section_paths):
				slug += "-page"
			row = None
			if slug in used:
				if opts.conflict == "skip":
					counts[it.kind][2] += 1
					continue
				if opts.conflict == "update":
					row = (db.query(Post) if it.kind == "post" else db.query(Page)).filter_by(slug=slug).first()
				else:
					slug = _unique(slug, used, it.kind)
			content = rewrite(it.content)
			cover = rewrite(html.escape(it.cover_url, quote=True)) if it.cover_url else ""
			cover = html.unescape(cover)
			status = "published" if it.published else "draft"
			if row is None:
				row = Post(slug=slug) if it.kind == "post" else Page(slug=slug)
				db.add(row)
				counts[it.kind][0] += 1
			else:
				counts[it.kind][1] += 1
			used.add(slug)
			row.title, row.content, row.status = it.title[:255], content, status
			if it.date:
				row.created_at = it.date
			row.author_id = opts.author_id
			if it.kind == "page":
				row.description = it.excerpt or strip_tags(content)[:160]
				row.template = row.template or "default"
				continue
			row.excerpt = it.excerpt
			row.cover_image = cover or None
			row.published_at = (it.date or datetime.datetime.utcnow()) if it.published else None
			row.meta = row.meta or "{}"
			category = None
			if it.categories:
				c = it.categories[0]
				category = cat_by_slug.get(c["slug"])
				if not category:
					category = Category(name=c["name"][:255], slug=c["slug"][:255])
					db.add(category)
					cat_by_slug[c["slug"]] = category
					new_cats += 1
			row.category = category
			row.tags = []
			for name in it.tags:
				name = name.strip()[:255]
				tag = tag_by_name.get(name.lower())
				if not tag:
					tag = Tag(name=name)
					db.add(tag)
					tag_by_name[name.lower()] = tag
				if tag not in row.tags:
					row.tags.append(tag)

		if opts.import_settings:
			for key, value in (("site_title", parsed.site_title), ("site_description", parsed.site_description)):
				if value:
					s = db.query(SiteSetting).filter_by(key=key).first()
					if s:
						s.value = value
					else:
						db.add(SiteSetting(key=key, value=value))
			report.append("Site title and description updated.")
		if opts.import_menu and parsed.menu:
			s = db.query(SiteSetting).filter_by(key="menu_items").first()
			payload = json.dumps(parsed.menu, ensure_ascii=False)
			if s:
				s.value = payload
			else:
				db.add(SiteSetting(key="menu_items", value=payload))
			report.append(f"Navigation menu replaced with {len(parsed.menu)} item(s) from the old site.")
		db.commit()

		for kind, label in (("post", "post"), ("page", "page")):
			a, u, s = counts[kind]
			if a or u or s:
				report.insert(0, f"{label.capitalize()}s: {a} added" + (f", {u} updated" if u else "")
					+ (f", {s} skipped (same address already exists or drafts excluded)" if s else "") + ".")
		if new_cats:
			report.append(f"{new_cats} categor{'y' if new_cats == 1 else 'ies'} created.")
		for what, n in parsed.skipped.items():
			report.append(f"Not imported: {n} {what}.")
		if parsed.source == "wordpress" and any(i.kind == "page" and i.parent for i in parsed.items):
			report.append("WordPress child pages were imported as ordinary pages (FlexaFlow page addresses are flat).")
		report.append("Imported content is set to your account as author. Comments are not imported.")
		return report
	except SQLAlchemyError as e:
		db.rollback()
		raise MigrationError(f"Import failed, nothing was saved: {e}")
	finally:
		db.close()


def import_file(data: bytes, source: str, opts: ImportOptions, **kw) -> List[str]:
	parsed = parse_wordpress(data) if source == "wordpress" else parse_blogger(data)
	if not parsed.items:
		raise MigrationError("No posts or pages were found in that file.")
	lines = apply_import(parsed, opts, **kw)
	return [f"Read {len([i for i in parsed.items if i.kind == 'post'])} post(s) and "
		f"{len([i for i in parsed.items if i.kind == 'page'])} page(s) from {'WordPress' if source == 'wordpress' else 'Blogger'}."] + lines


_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _x(text) -> str:
	return escape(_BAD_XML.sub("", str(text if text is not None else "")))


def _cd(text) -> str:
	return "<![CDATA[" + _BAD_XML.sub("", str(text if text is not None else "")).replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _wp_date(d: Optional[datetime.datetime]) -> str:
	return (d or datetime.datetime.utcnow()).strftime("%Y-%m-%d %H:%M:%S")


def _rfc822(d: Optional[datetime.datetime]) -> str:
	return (d or datetime.datetime.utcnow()).strftime("%a, %d %b %Y %H:%M:%S +0000")


def _iso(d: Optional[datetime.datetime]) -> str:
	return (d or datetime.datetime.utcnow()).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _post_path(post) -> str:
	sec = post.category.section_path if post.category and post.category.section_path else None
	return f"/{sec}/{post.slug}" if sec else f"/post/{post.slug}"


def _settings(db) -> Dict[str, Any]:
	out = {}
	for s in db.query(SiteSetting).all():
		try:
			out[s.key] = json.loads(s.value) if s.value else ""
		except (TypeError, ValueError):
			out[s.key] = s.value
	return out


def _flat_menu(items):
	"""Top-level menu entries that have both a label and an address (children are handled by the exporter)."""
	return [(it, 0) for it in (items if isinstance(items, list) else [])
		if isinstance(it, dict) and it.get("label") and it.get("url")]


def export_wordpress(base_url: str, *, include_drafts=True, include_media=True, include_menu=True, include_pages=True) -> bytes:
	base = base_url.rstrip("/")
	db = db_manager.get_session()
	try:
		cfg = _settings(db)
		title, desc = cfg.get("site_title") or "FlexaFlow site", cfg.get("site_description") or ""
		posts = db.query(Post).order_by(Post.id).all()
		pages = db.query(Page).order_by(Page.id).all() if include_pages else []
		if not include_drafts:
			posts = [p for p in posts if p.status == "published"]
			pages = [p for p in pages if p.status == "published"]
		cats = db.query(Category).order_by(Category.id).all()
		tags = db.query(Tag).order_by(Tag.id).all()
		users = {u.id: u for u in db.query(User).all()}

		seq = [0]

		def nid():
			seq[0] += 1
			return seq[0]

		o: List[str] = []
		o.append('<?xml version="1.0" encoding="UTF-8" ?>')
		o.append('<rss version="2.0" xmlns:excerpt="http://wordpress.org/export/1.2/excerpt/" '
			'xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:wfw="http://wellformedweb.org/CommentAPI/" '
			'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:wp="http://wordpress.org/export/1.2/">')
		o.append("<channel>")
		o.append(f"<title>{_x(title)}</title><link>{_x(base)}</link><description>{_x(desc)}</description>")
		o.append(f"<pubDate>{_rfc822(None)}</pubDate><language>en-US</language><wp:wxr_version>1.2</wp:wxr_version>")
		o.append(f"<wp:base_site_url>{_x(base)}</wp:base_site_url><wp:base_blog_url>{_x(base)}</wp:base_blog_url>")
		o.append("<generator>https://wordpress.org/?v=6.5 (generated by FlexaFlow)</generator>")

		author_login = {}
		for u in users.values():
			if any(p.author_id == u.id for p in posts) or any(p.author_id == u.id for p in pages):
				author_login[u.id] = u.username
				o.append(f"<wp:author><wp:author_id>{nid()}</wp:author_id><wp:author_login>{_cd(u.username)}</wp:author_login>"
					f"<wp:author_email>{_cd('')}</wp:author_email><wp:author_display_name>{_cd(u.display_name or u.username)}</wp:author_display_name>"
					f"<wp:author_first_name>{_cd('')}</wp:author_first_name><wp:author_last_name>{_cd('')}</wp:author_last_name></wp:author>")
		for c in cats:
			o.append(f"<wp:category><wp:term_id>{nid()}</wp:term_id><wp:category_nicename>{_cd(c.slug)}</wp:category_nicename>"
				f"<wp:category_parent>{_cd('')}</wp:category_parent><wp:cat_name>{_cd(c.name)}</wp:cat_name>"
				+ (f"<wp:category_description>{_cd(c.description)}</wp:category_description>" if c.description else "") + "</wp:category>")
		for t in tags:
			o.append(f"<wp:tag><wp:term_id>{nid()}</wp:term_id><wp:tag_slug>{_cd(slugify(t.name) or t.name)}</wp:tag_slug>"
				f"<wp:tag_name>{_cd(t.name)}</wp:tag_name></wp:tag>")

		menu_items = _flat_menu(cfg.get("menu_items")) if include_menu else []
		if menu_items:
			o.append(f"<wp:term><wp:term_id>{nid()}</wp:term_id><wp:term_taxonomy>{_cd('nav_menu')}</wp:term_taxonomy>"
				f"<wp:term_slug>{_cd('main-menu')}</wp:term_slug><wp:term_name>{_cd('Main Menu')}</wp:term_name></wp:term>")

		def meta(key, value):
			return f"<wp:postmeta><wp:meta_key>{_cd(key)}</wp:meta_key><wp:meta_value>{_cd(value)}</wp:meta_value></wp:postmeta>"

		def abs_url(u):
			return urljoin(base + "/", u) if u and u.startswith("/") and not u.startswith("//") else u

		attachment_ids: Dict[str, int] = {}
		attachments: List[str] = []

		def attachment(url: str, name: str, parent_id: int = 0, created=None, mime: str = "") -> int:
			url = abs_url(url)
			if url in attachment_ids:
				return attachment_ids[url]
			aid = nid()
			attachment_ids[url] = aid
			attachments.append(
				f"<item><title>{_x(name)}</title><link>{_x(url)}</link><pubDate>{_rfc822(created)}</pubDate><dc:creator>{_cd('admin')}</dc:creator>"
				f"<guid isPermaLink=\"false\">{_x(url)}</guid><description></description><content:encoded>{_cd('')}</content:encoded>"
				f"<excerpt:encoded>{_cd('')}</excerpt:encoded><wp:post_id>{aid}</wp:post_id><wp:post_date>{_cd(_wp_date(created))}</wp:post_date>"
				f"<wp:post_date_gmt>{_cd(_wp_date(created))}</wp:post_date_gmt><wp:comment_status>{_cd('closed')}</wp:comment_status>"
				f"<wp:ping_status>{_cd('closed')}</wp:ping_status><wp:post_name>{_cd(slugify(name) or 'file')}</wp:post_name>"
				f"<wp:status>{_cd('inherit')}</wp:status><wp:post_parent>{parent_id}</wp:post_parent><wp:menu_order>0</wp:menu_order>"
				f"<wp:post_type>{_cd('attachment')}</wp:post_type><wp:post_password>{_cd('')}</wp:post_password><wp:is_sticky>0</wp:is_sticky>"
				f"<wp:attachment_url>{_cd(url)}</wp:attachment_url></item>")
			return aid

		body: List[str] = []

		def entry(kind, row, link, parent_cats="", extra_meta=""):
			pid = nid()
			status = "publish" if row.status == "published" else "draft"
			created = row.created_at or datetime.datetime.utcnow()
			published = getattr(row, "published_at", None) or created
			login = author_login.get(row.author_id, "admin")
			excerpt = getattr(row, "excerpt", None) or (row.description if kind == "page" else "") or ""
			thumb = ""
			cover = getattr(row, "cover_image", None)
			if cover:
				thumb = meta("_thumbnail_id", attachment(cover, os.path.basename(cover) or "cover", pid, created))
			body.append(
				f"<item><title>{_x(row.title)}</title><link>{_x(base + link)}</link><pubDate>{_rfc822(published)}</pubDate>"
				f"<dc:creator>{_cd(login)}</dc:creator><guid isPermaLink=\"false\">{_x(base)}/?p={pid}</guid><description></description>"
				f"<content:encoded>{_cd(html_to_wp(row.content or '', base))}</content:encoded><excerpt:encoded>{_cd(excerpt)}</excerpt:encoded>"
				f"<wp:post_id>{pid}</wp:post_id><wp:post_date>{_cd(_wp_date(published))}</wp:post_date><wp:post_date_gmt>{_cd(_wp_date(published))}</wp:post_date_gmt>"
				f"<wp:post_modified>{_cd(_wp_date(row.updated_at))}</wp:post_modified><wp:post_modified_gmt>{_cd(_wp_date(row.updated_at))}</wp:post_modified_gmt>"
				f"<wp:comment_status>{_cd('open' if kind == 'post' else 'closed')}</wp:comment_status><wp:ping_status>{_cd('closed')}</wp:ping_status>"
				f"<wp:post_name>{_cd(row.slug)}</wp:post_name><wp:status>{_cd(status)}</wp:status><wp:post_parent>0</wp:post_parent>"
				f"<wp:menu_order>0</wp:menu_order><wp:post_type>{_cd(kind)}</wp:post_type><wp:post_password>{_cd('')}</wp:post_password>"
				f"<wp:is_sticky>0</wp:is_sticky>{parent_cats}{thumb}{extra_meta}</item>")
			return pid

		for p in pages:
			entry("page", p, f"/{p.slug}")
		for p in posts:
			terms = ""
			if p.category:
				terms += f'<category domain="category" nicename="{_x(p.category.slug)}">{_cd(p.category.name)}</category>'
			for t in p.tags:
				terms += f'<category domain="post_tag" nicename="{_x(slugify(t.name) or t.name)}">{_cd(t.name)}</category>'
			try:
				fields = json.loads(p.meta) if p.meta else {}
			except ValueError:
				fields = {}
			extra = "".join(meta(f"flexaflow_{k}", v) for k, v in fields.items() if isinstance(v, (str, int, float)) and str(v).strip())
			entry("post", p, _post_path(p), terms, extra)

		if include_media:
			for m in db.query(Media).all():
				attachment(f"/uploads/{m.filename}", m.original_filename or m.filename, 0, m.created_at, m.mime_type or "")

		menu_xml: List[str] = []
		if menu_items:
			ids = {id(it): nid() for it, _ in menu_items}
			for it, parent in menu_items:
				mid = ids[id(it)]
				url = abs_url(str(it["url"]))
				menu_xml.append(
					f"<item><title>{_x(it['label'])}</title><link>{_x(url)}</link><pubDate>{_rfc822(None)}</pubDate><dc:creator>{_cd('admin')}</dc:creator>"
					f"<guid isPermaLink=\"false\">{_x(base)}/?p={mid}</guid><description></description><content:encoded>{_cd('')}</content:encoded>"
					f"<excerpt:encoded>{_cd('')}</excerpt:encoded><wp:post_id>{mid}</wp:post_id><wp:post_date>{_cd(_wp_date(None))}</wp:post_date>"
					f"<wp:post_date_gmt>{_cd(_wp_date(None))}</wp:post_date_gmt><wp:comment_status>{_cd('closed')}</wp:comment_status>"
					f"<wp:ping_status>{_cd('closed')}</wp:ping_status><wp:post_name>{mid}</wp:post_name><wp:status>{_cd('publish')}</wp:status>"
					f"<wp:post_parent>0</wp:post_parent><wp:menu_order>{mid}</wp:menu_order><wp:post_type>{_cd('nav_menu_item')}</wp:post_type>"
					f"<wp:post_password>{_cd('')}</wp:post_password><wp:is_sticky>0</wp:is_sticky>"
					f'<category domain="nav_menu" nicename="main-menu">{_cd("Main Menu")}</category>'
					+ meta("_menu_item_type", "custom") + meta("_menu_item_menu_item_parent", "0")
					+ meta("_menu_item_object_id", mid) + meta("_menu_item_object", "custom") + meta("_menu_item_target", "")
					+ meta("_menu_item_classes", 'a:1:{i:0;s:0:"";}') + meta("_menu_item_xfn", "") + meta("_menu_item_url", url) + "</item>")
				for child in (it.get("children") or []):
					if isinstance(child, dict) and child.get("label") and child.get("url"):
						cid = nid()
						curl = abs_url(str(child["url"]))
						menu_xml.append(
							f"<item><title>{_x(child['label'])}</title><link>{_x(curl)}</link><pubDate>{_rfc822(None)}</pubDate><dc:creator>{_cd('admin')}</dc:creator>"
							f"<guid isPermaLink=\"false\">{_x(base)}/?p={cid}</guid><description></description><content:encoded>{_cd('')}</content:encoded>"
							f"<excerpt:encoded>{_cd('')}</excerpt:encoded><wp:post_id>{cid}</wp:post_id><wp:post_date>{_cd(_wp_date(None))}</wp:post_date>"
							f"<wp:post_date_gmt>{_cd(_wp_date(None))}</wp:post_date_gmt><wp:comment_status>{_cd('closed')}</wp:comment_status>"
							f"<wp:ping_status>{_cd('closed')}</wp:ping_status><wp:post_name>{cid}</wp:post_name><wp:status>{_cd('publish')}</wp:status>"
							f"<wp:post_parent>0</wp:post_parent><wp:menu_order>{cid}</wp:menu_order><wp:post_type>{_cd('nav_menu_item')}</wp:post_type>"
							f"<wp:post_password>{_cd('')}</wp:post_password><wp:is_sticky>0</wp:is_sticky>"
							f'<category domain="nav_menu" nicename="main-menu">{_cd("Main Menu")}</category>'
							+ meta("_menu_item_type", "custom") + meta("_menu_item_menu_item_parent", mid)
							+ meta("_menu_item_object_id", cid) + meta("_menu_item_object", "custom") + meta("_menu_item_target", "")
							+ meta("_menu_item_classes", 'a:1:{i:0;s:0:"";}') + meta("_menu_item_xfn", "") + meta("_menu_item_url", curl) + "</item>")

		o.extend(attachments)
		o.extend(body)
		o.extend(menu_xml)
		o.append("</channel></rss>")
		return "\n".join(o).encode("utf-8")
	finally:
		db.close()


def export_blogger(base_url: str, *, include_drafts=True, include_pages=True) -> bytes:
	base = base_url.rstrip("/")
	db = db_manager.get_session()
	try:
		cfg = _settings(db)
		title, desc = cfg.get("site_title") or "FlexaFlow site", cfg.get("site_description") or ""
		posts = db.query(Post).order_by(Post.id).all()
		pages = db.query(Page).order_by(Page.id).all() if include_pages else []
		if not include_drafts:
			posts = [p for p in posts if p.status == "published"]
			pages = [p for p in pages if p.status == "published"]
		users = {u.id: (u.display_name or u.username) for u in db.query(User).all()}
		blog_id = "1" + str(zlib.crc32(title.encode("utf-8"))).zfill(15)
		now = _iso(None)
		o = ["<?xml version='1.0' encoding='UTF-8'?>",
			'<?xml-stylesheet href="http://www.blogger.com/styles/atom.css" type="text/css"?>',
			"<feed xmlns='http://www.w3.org/2005/Atom' xmlns:openSearch='http://a9.com/-/spec/opensearchrss/1.0/' "
			"xmlns:blogger='http://schemas.google.com/blogger/2008' xmlns:georss='http://www.georss.org/georss' "
			"xmlns:gd=\"http://schemas.google.com/g/2005\" xmlns:thr='http://purl.org/syndication/thread/1.0'>",
			f"<id>tag:blogger.com,1999:blog-{blog_id}</id><updated>{now}</updated>",
			f"<title type='text'>{_x(title)}</title><subtitle type='html'>{_x(desc)}</subtitle>",
			f"<link rel='alternate' type='text/html' href='{_x(base)}'/>",
			"<generator version='7.00' uri='http://www3.blogger.com'>Blogger</generator>"]

		def ascii_slug(s):
			return s if re.fullmatch(r"[a-z0-9][a-z0-9\-]*", s or "") else ""

		def entry(kind, row, n, labels):
			live = row.status == "published"
			created = (getattr(row, "published_at", None) or row.created_at or datetime.datetime.utcnow())
			slug = ascii_slug(row.slug)
			if kind == "post":
				filename = f"/{created:%Y/%m}/{slug}.html" if slug and live else ""
			else:
				filename = f"/p/{slug}.html" if slug and live else ""
			author = _x(users.get(row.author_id, "Admin"))
			cats = (f"<category scheme='http://schemas.google.com/g/2005#kind' term='http://schemas.google.com/blogger/2008/kind#{kind}'/>"
				+ "".join(f"<category scheme='http://www.blogger.com/atom/ns#' term='{_x(l)}'/>" for l in labels))
			o.append(
				f"<entry><id>tag:blogger.com,1999:blog-{blog_id}.{kind}-{n}</id><published>{_iso(created)}</published>"
				f"<updated>{_iso(row.updated_at)}</updated>{cats}"
				+ ("" if live else "<app:control xmlns:app='http://www.w3.org/2007/app'><app:draft>yes</app:draft></app:control>")
				+ f"<title type='text'>{_x(row.title)}</title><content type='html'>{_x(make_absolute(row.content or '', base))}</content>"
				f"<author><name>{author}</name></author>"
				+ (f"<blogger:metaDescription>{_x(row.excerpt)}</blogger:metaDescription>" if getattr(row, "excerpt", None) else "")
				+ f"<blogger:created>{_iso(row.created_at)}</blogger:created><blogger:type>{kind.upper()}</blogger:type>"
				f"<blogger:status>{'LIVE' if live else 'DRAFT'}</blogger:status>"
				+ (f"<blogger:filename>{_x(filename)}</blogger:filename>" if filename else "") + "</entry>")

		n = 0
		for p in posts:
			n += 1
			labels = ([p.category.name] if p.category else []) + [t.name for t in p.tags]
			entry("post", p, n, list(dict.fromkeys(l.replace(",", " ") for l in labels)))
		for p in pages:
			n += 1
			entry("page", p, n, [])
		o.append("</feed>")
		return "\n".join(o).encode("utf-8")
	finally:
		db.close()
