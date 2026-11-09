"""Dashboard, settings, menu, import/export and categories/tags administration."""
import sys as _sys

from flask import Blueprint

bp = Blueprint("admin", __name__)

# Names defined in app.py (helpers, stores, settings) are shared with this module.
# This file is imported at the end of app.py, once all of them exist.
_core = _sys.modules["app"]
globals().update({k: v for k, v in vars(_core).items() if not k.startswith("__") and k not in ("bp",)})


@bp.route("/admin")
@login_required
def admin():
	"""
	Admin dashboard view that displays all pages and posts.
	
	Shows both published and draft content. Requires user to be logged in.
	
	Returns:
		str: Rendered admin dashboard template with list of all pages and posts
	"""	
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

	me = current_user()
	if not can("edit_others_content"):
		all_posts = [p for p in all_posts if p.get("author_id") == me["id"]]
	if not can("manage_pages"):
		all_pages = []
	users_by_id = {u["id"]: u for u in db_manager.list_users()}
	for p in all_posts:
		p["public_url"] = post_url(p)
	all_posts.sort(key=lambda p: str(p.get("created_at") or ""), reverse=True)
	per_page = 10
	total_posts = len(all_posts)
	total_pages = max(1, (total_posts + per_page - 1) // per_page)
	try:
		post_page = int(request.args.get("ppage", 1))
	except (TypeError, ValueError):
		post_page = 1
	post_page = max(1, min(post_page, total_pages))
	post_rows = all_posts[(post_page - 1) * per_page: post_page * per_page]
	return render_template(
		"admin.html", pages=all_pages, posts=all_posts, post_rows=post_rows,
		post_page=post_page, post_pages=total_pages, post_per_page=per_page,
		users_by_id=users_by_id, role_labels=ROLE_LABELS,
	)


@bp.route("/admin/settings")
@capability_required("manage_settings")
def settings():
	"""Manages site-wide settings and configuration.  Features: - Site configuration management - Theme settings - 2FA status display - Custom analytics integration - Page selection for special roles  Settings Categories: - General site settings - Theme configuration - Security settings (2FA) -..."""
	site_settings = get_site_settings()
	site_settings["two_fa_enabled"] = bool(current_user()["two_fa_enabled"])
	return render_template(
		"settings.html",
		settings=site_settings,
		pages=get_pages(),
		themes=available_themes(),
	)


@bp.route("/admin/menu")
@capability_required("manage_settings")
def menu_editor():
	"""Provides interface for managing site navigation menu.  Features: - Visual menu builder - Drag-and-drop ordering - Page selection integration - Custom link support - Nested menu structure  Data Structure: menu_items: List of menu entries with: - title: Display text - url: Link target - order:..."""
	settings = get_site_settings()
	menu_items = settings.get("menu_items", [])
	quick_links = [{"label": s["name"], "url": f"/{s['section_path']}/"} for s in list_sections()]
	for kind, cfg in content_store.ENTRY_KINDS.items():
		quick_links.append({"label": cfg["label"], "url": cfg["base_path"]})
		quick_links += [{"label": label, "url": f"{cfg['base_path']}group/{g}"} for g, label in cfg["groups"] if g != cfg["groups"][0][0] and g in cfg.get("public_groups", [])]
	return render_template("menu-editor.html", menu_items=menu_items, pages=get_pages(), quick_links=quick_links)


@bp.route("/admin/category/add", methods=["POST"])
@capability_required("manage_taxonomy")
def add_category():
	"""Handles addition of new post categories.  Features: - Validates category name - Auto-generates category slug - Prevents duplicate categories - Provides feedback messages - Database transaction handling  Form Parameters: name (str): Category name to add  Processing: 1. Name validation 2. Slug..."""
	name = request.form.get("name", "").strip()
	if not name:
		flash("Category name required!", "error")
		return redirect(url_for("admin.manage_tags_and_catagories"))
	success = add_category_to_db(name)
	if success:
		flash("Category added successfully!", "success")
	else:
		flash("Error adding category! (Maybe already exists)", "error")
	return redirect(url_for("admin.manage_tags_and_catagories"))


@bp.route("/admin/export/xml", methods=["GET"])
@capability_required("manage_settings")
def export_content():
	"""Exports all site content as XML for backup or migration.  Features: - Exports all posts and pages - Preserves metadata and relationships - Maintains categories and tags - Generates unique filenames - Uses standardized XML format  Export Format: - Root element with version and timestamp - Pages..."""

	FLEXAFLOW_VERSION = "1.0.0"
	export_time = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
	site_settings = get_site_settings()
	site_title = site_settings.get("site_title", "site").strip().lower().replace(" ", "_")
	timestamp = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
	filename = f"flexaflow_{FLEXAFLOW_VERSION}_export_{site_title}_{timestamp}.xml"

	root = ET.Element("flexaflow_export", version="1.0", exported_at=export_time)

	pages_elem = ET.SubElement(root, "pages")
	for slug, page in get_pages().items():
		page_elem = ET.SubElement(pages_elem, "page", slug=slug)
		for k, v in page.items():
			child = ET.SubElement(page_elem, k)
			child.text = str(v) if v is not None else ""

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

	xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
	return send_file(
		io.BytesIO(xml_bytes),
		mimetype="application/xml",
		as_attachment=True,
		download_name=filename,
	)


@bp.route("/admin/import/xml", methods=["GET", "POST"])
@capability_required("manage_settings")
def import_content():
	"""Handles import of content from XML file.  Features: - Imports both posts and pages - Validates XML format - Skips duplicate content based on slugs - Maintains relationships (categories, tags) - Updates tag counts after import  Args: file: Uploaded XML file through request.files  Returns: GET:..."""
	if request.method == "POST":
		file = request.files.get("file")
		if not file or not file.filename.endswith(".xml"):
			flash("Please upload a valid XML file.", "error")
			return redirect(url_for("admin.import_content"))
		try:
			tree = ET.parse(file)
			root = tree.getroot()
			existing_page_slugs = set(get_pages().keys())
			existing_post_slugs = set(get_posts().keys())
			skipped_pages = []
			skipped_posts = []
			imported_pages = 0
			imported_posts = 0
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
		return redirect(url_for("admin.admin"))
	return render_template("import.html")


@bp.route("/admin/tags-and-catagories", methods=["GET", "POST"])
@capability_required("manage_taxonomy")
def manage_tags_and_catagories():
	"""Manages post categories and tags.  Features: - Lists all categories and tags - Shows usage count for each - Identifies unused categories/tags - Supports deletion of unused items - Prevents deletion of items in use  Methods: GET: Display management interface POST: Handle category/tag..."""
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


@bp.route("/admin/category/delete/<slug>", methods=["POST"])
@capability_required("manage_taxonomy")
def delete_category(slug):
	"""Handles deletion of unused categories.  Features: - Validates category exists - Prevents deletion of used categories - Database transaction handling - User feedback - Referential integrity  Args: slug (str): URL slug of category to delete  Returns: 302: Redirect to category management with: -..."""
	categories = get_categories() or {}
	if slug in categories and categories[slug].get("post_count", 0) == 0:
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
	return redirect(url_for("admin.manage_tags_and_catagories"))


@bp.route("/admin/tag/add", methods=["POST"])
@capability_required("manage_taxonomy")
def add_tag():
	"""Handles creation of new post tags.  Features: - Tag name validation - Duplicate prevention - Database transaction handling - Error feedback - Automatic count initialization  Form Parameters: name (str): Name of the new tag  Processing: 1. Name validation and sanitization 2. Duplicate check 3...."""

	name = request.form.get("name", "").strip()
	if not name:
		flash("Tag name required.", "error")
		return redirect(url_for("admin.manage_tags_and_catagories"))
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
	return redirect(url_for("admin.manage_tags_and_catagories"))


@bp.route("/admin/tag/delete/<int:tag_id>", methods=["POST"])
@capability_required("manage_taxonomy")
def delete_tag(tag_id):
	"""Handles deletion of unused tags.  Features: - Safe tag removal - Usage validation - Database cleanup - Referential integrity - User feedback  Processing Steps: 1. Verify tag exists 2. Check usage count 3. Remove if unused 4. Update post relationships 5. Clean up database  URL Parameters: tag_id..."""

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
	return redirect(url_for("admin.manage_tags_and_catagories"))
