"""Creating, editing, previewing and deleting pages and posts."""
import sys as _sys

from flask import Blueprint

bp = Blueprint("editor", __name__)

# Names defined in app.py (helpers, stores, settings) are shared with this module.
# This file is imported at the end of app.py, once all of them exist.
_core = _sys.modules["app"]
globals().update({k: v for k, v in vars(_core).items() if not k.startswith("__") and k not in ("bp",)})


@bp.route("/page/add", methods=["GET", "POST"])
@capability_required("manage_pages")
def add_page_route():
	"""
	Create a page. The page template chosen on the form decides which custom
	fields are shown and stored (see page_templates.py).
	"""
	errors = []
	if request.method == "POST":
		template_key = _resolve_template(request.form.get("template") or "default")
		template = _core.PAGE_TEMPLATES[template_key]
		title = (request.form.get("title") or "").strip()
		slug = slugify(request.form.get("slug") or title)
		description = (request.form.get("description") or "").strip()
		content = sanitize_html(request.form.get("content") or "")
		status, publish_at, seo = publish_fields(request.form, True)

		if not title:
			errors.append("A page title is required.")
		if not slug:
			errors.append("A permalink (slug) is required.")
		elif slug in RESERVED_SLUGS or get_section_by_path(slug) or content_store.directory_for_path(slug)[0]:
			errors.append(f"'{slug}' is reserved. Choose another permalink.")
		elif get_page_by_slug(slug):
			errors.append(f"A page already exists at '/{slug}'.")

		fields = parse_fields(template["fields"], request.form)
		if request.form.get("action") == "switch_template":
			return render_template(
				"page_form.html", mode="add", slug=slug, errors=[],
				page={"title": title, "description": description, "content": content,
				      "status": status, "template": template_key},
				templates=_core.PAGE_TEMPLATES, template_key=template_key,
				values=fill_defaults(fields, template["fields"]),
			)
		if not errors:
			saved = add_page(slug, {
				"publish_at": publish_at,
				"seo": seo,
				"title": title,
				"content": content,
				"description": description,
				"status": status,
				"template": template_key,
				"fields": fields,
				"author_id": current_user()["id"],
			})
			if saved:
				generate_and_store_sitemap()
				flash("Page created.", "success")
				return redirect(url_for("editor.edit_page", slug=slug))
			errors.append("The page could not be saved. Please try again.")

		page = {"title": title, "description": description, "content": content,
		        "status": status, "template": template_key}
		return render_template(
			"page_form.html", mode="add", page=page, slug=slug, errors=errors,
			templates=_core.PAGE_TEMPLATES, template_key=template_key,
			values=fill_defaults(fields, template["fields"]),
		)

	template_key = _resolve_template(request.args.get("template") or "default")
	return render_template(
		"page_form.html", mode="add", page={"title": "", "description": "", "content": "",
		                                    "status": "draft", "template": template_key},
		slug="", errors=errors, templates=_core.PAGE_TEMPLATES, template_key=template_key,
		values=default_values(_core.PAGE_TEMPLATES[template_key]["fields"]),
	)


@bp.route("/page/edit/<slug>", methods=["GET", "POST"])
@capability_required("manage_pages")
def edit_page(slug):
	"""Edit a page's content, permalink, status, template and custom fields."""
	page = get_page_by_slug(slug)
	if not page:
		return "<h1>Page not found</h1>", 404

	errors = []
	if request.method == "POST":
		template_key = _resolve_template(request.form.get("template") or "default")
		template = _core.PAGE_TEMPLATES[template_key]
		title = (request.form.get("title") or "").strip()
		new_slug = slugify(request.form.get("slug") or title) or slug
		description = (request.form.get("description") or "").strip()
		content = sanitize_html(request.form.get("content") or "")
		status, publish_at, seo = publish_fields(request.form, True)

		if not title:
			errors.append("A page title is required.")
		if new_slug != slug:
			if new_slug in RESERVED_SLUGS or get_section_by_path(new_slug) or content_store.directory_for_path(new_slug)[0]:
				errors.append(f"'{new_slug}' is reserved. Choose another permalink.")
			elif get_page_by_slug(new_slug):
				errors.append(f"A page already exists at '/{new_slug}'.")

		fields = parse_fields(template["fields"], request.form)
		if request.form.get("action") == "switch_template":
			return render_template(
				"page_form.html", mode="edit", slug=slug, errors=[],
				page=dict(page, title=title, description=description, content=content,
				          status=status, template=template_key),
				templates=_core.PAGE_TEMPLATES, template_key=template_key,
				values=fill_defaults(fields, template["fields"]),
			)
		if not errors:
			saved = update_page_by_slug(slug, {
				"publish_at": publish_at,
				"seo": seo,
				"title": title,
				"content": content,
				"description": description,
				"status": status,
				"new_slug": new_slug,
				"template": template_key,
				"fields": fields,
			})
			if saved:
				generate_and_store_sitemap()
				flash("Page saved.", "success")
				return redirect(url_for("editor.edit_page", slug=new_slug))
			errors.append("The page could not be saved. Please try again.")

		page = dict(page, title=title, description=description, content=content,
		            status=status, template=template_key)
		return render_template(
			"page_form.html", mode="edit", page=page, slug=slug, errors=errors,
			templates=_core.PAGE_TEMPLATES, template_key=template_key,
			values=fill_defaults(fields, template["fields"]),
		)

	template_key = _resolve_template(page.get("template"))
	template = _core.PAGE_TEMPLATES[template_key]
	return render_template(
		"page_form.html", mode="edit", page=page, slug=slug, errors=errors,
		templates=_core.PAGE_TEMPLATES, template_key=template_key,
		values=fill_defaults(page.get("fields") or {}, template["fields"]),
	)


@bp.route("/page/delete/<slug>", methods=["POST"])
@capability_required("manage_pages")
def delete_page_route(slug):
	"""Delete a page. POST only, so a link or image on another site cannot delete content."""
	if delete_page(slug):
		generate_and_store_sitemap()
		flash("Page deleted.", "success")
	else:
		flash("Error deleting page.", "error")
	return redirect(url_for("admin.admin"))


@bp.route("/page/preview/<slug>")
@capability_required("manage_pages")
def preview_page(slug):
	"""Provides a preview of a page before publishing.  Features: - Live content preview - Theme integration - Metadata display - Draft preview support - Custom styling options  URL Parameters: slug (str): Page identifier to preview  Template Context: page: Complete page data theme: Theme configuration..."""


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
		template_key = _resolve_template(page.get("template"))
		if template_key != DEFAULT_TEMPLATE_KEY:
			template = _core.PAGE_TEMPLATES[template_key]
			values = fill_defaults(page.get("fields") or {}, template["fields"])
			return render_template(template["template_file"], page=page, f=values)
		return render_template("preview.html", page=page, theme=theme)
	else:
		return "<h1>Page not found</h1>", 404


@bp.route("/page/preview_draft", methods=["POST"])
@login_required
def preview_draft():
	"""Previews a draft post/page before saving.  Features: - Shows how content will look when published - Uses actual theme templates - Displays all content sections - No database interaction required  Form Parameters: title (str): Draft title description (str): Draft description/excerpt content..."""

	title = request.form.get("title")
	description = request.form.get("description")
	content = sanitize_html(request.form.get("content") or "")
	page = {"title": title, "description": description, "content": content}
	return render_template("preview.html", page=page)


@bp.route("/post/add", methods=["GET", "POST"])
@capability_required("create_posts")
def add_post_route():
	"""Create a post. Contributors' posts are saved as drafts regardless of the status chosen."""
	errors = []
	form = {"slug": "", "title": "", "description": "", "content": "",
	        "category": "", "tags": "", "status": "draft", "cover_image": "",
	        "pdate": "", "pdatetime": ""}
	meta_values = {}
	if request.method == "GET":
		wanted = get_section_by_path(request.args.get("section", "")) or get_section_by_slug(request.args.get("category", ""))
		if wanted:
			form["category"] = wanted["slug"]
	if request.method == "POST":
		form = {key: (request.form.get(key) or "") for key in form}
		title = form["title"].strip()
		content = sanitize_html(form["content"].strip())
		slug = slugify(form["slug"].strip() or title)
		if not title or not content:
			errors.append("Title and content are required.")
		if not slug:
			errors.append("A permalink (slug) is required.")
		elif get_post_by_slug(slug):
			errors.append(f"A post with slug '{slug}' already exists.")

		meta_values, cover_image, published_at = _post_extras(form["category"].strip())
		if not errors:
			tags = [t.strip() for t in form["tags"].split(",") if t.strip()]
			status, publish_at, seo = publish_fields(request.form, can("publish_posts"))
			saved = add_post(slug, {
				"publish_at": publish_at,
				"seo": seo,
				"content": content,
				"title": title,
				"excerpt": form["description"].strip() or title[:200],
				"status": status,
				"category": form["category"].strip(),
				"tags": tags,
				"author_id": current_user()["id"],
				"cover_image": cover_image,
				"meta": meta_values,
				"published_at": published_at,
			})
			if saved:
				update_tag_counts()
				generate_and_store_sitemap()
				section = get_section_by_slug(form["category"].strip())
				noun = (section["singular_name"] if section else "Post")
				if status == "pending":
					notify_review_requested(title, slug, current_user()["display_name"] or current_user()["username"])
				flash(f"{noun} created." + {"published": "", "scheduled": " It will be published at the scheduled time.",
				                             "pending": " It was sent for review."}.get(status, " It was saved as a draft."), "success")
				return redirect(url_for("admin.admin"))
			errors.append("The post could not be saved. Please try again.")

	return render_template(
		"post_form.html", mode="add", post=None, slug="", form=form, errors=errors,
		categories=get_categories() or {}, can_publish=can("publish_posts"),
		meta_values=meta_values,
	)


@bp.route("/post/edit/<slug>", methods=["GET", "POST"])
@login_required
def edit_post(slug):
	"""Edit a post, enforcing ownership and status rules for the current role."""
	post = get_post_by_slug(slug)
	if not post:
		return "<h1>Post not found</h1>", 404
	me = current_user()
	if not _can_edit_post(post, me):
		abort(403)

	errors = []
	if request.method == "POST":
		title = (request.form.get("title") or "").strip()
		content = sanitize_html(request.form.get("content") or "")
		if not title or not content.strip():
			errors.append("Title and content are required.")
		if not errors:
			tags = [t.strip() for t in (request.form.get("tags") or "").split(",") if t.strip()]
			meta_values, cover_image, published_at = _post_extras((request.form.get("category") or "").strip())
			status, publish_at, seo = publish_fields(request.form, can("publish_posts"))
			if post.get("status") in ("published", "scheduled") and not can("publish_posts"):
				status = post["status"]  # an author-level edit must not unpublish through the form
			success = update_post_by_slug(slug, {
				"publish_at": publish_at,
				"seo": seo,
				"content": content,
				"title": title,
				"excerpt": (request.form.get("description") or "").strip(),
				"status": status,
				"category": request.form.get("category") or "",
				"tags": tags,
				"cover_image": cover_image,
				"meta": meta_values,
				"published_at": published_at,
			})
			if success:
				update_tag_counts()
				generate_and_store_sitemap()
				if status == "pending" and post.get("status") != "pending":
					notify_review_requested(title, slug, me["display_name"] or me["username"])
				flash("Post saved.", "success")
				return redirect(url_for("editor.edit_post", slug=slug))
			errors.append("The post could not be saved. Please try again.")
		post = dict(post, title=title, content=content,
		            excerpt=request.form.get("description") or "",
		            category=request.form.get("category") or "")

	post["tags_string"] = ", ".join(post.get("tags") or []) if isinstance(post.get("tags"), list) else ""
	return render_template(
		"post_form.html", mode="edit", post=post, slug=slug, form=None, errors=errors,
		categories=get_categories() or {}, can_publish=can("publish_posts"),
		meta_values=post.get("meta") or {},
	)


@bp.route("/post/delete/<slug>", methods=["POST"])
@login_required
def delete_post_route(slug):
	"""Delete a post (POST only). Contributors and authors can delete only their own posts."""
	post = get_post_by_slug(slug)
	if not post:
		flash("Post not found.", "error")
		return redirect(url_for("admin.admin"))
	me = current_user()
	if not can("edit_others_content") and post.get("author_id") != me["id"]:
		abort(403)
	if delete_post_by_slug(slug):
		update_tag_counts()
		generate_and_store_sitemap()
		flash("Post deleted.", "success")
	else:
		flash("Error deleting post.", "error")
	return redirect(url_for("admin.admin"))
