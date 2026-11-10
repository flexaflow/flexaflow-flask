"""
Routes for sections (/notices/, /events/, ...), the faculty and staff directories,
and the admin screens that manage them.

Registered from app.py as a blueprint. This module never imports app.py, so it works
whether the site is started with ``python app.py`` or through server.py.
"""

import io
from urllib.parse import urlencode
import json
import os
import images
import logging
import uuid

from flask import (
	Blueprint, abort, current_app, flash, jsonify, make_response, redirect,
	render_template, request, url_for,
)
from werkzeug.utils import secure_filename

import content_store as cs
from auth import can, capability_required, current_user, login_required
from data_store import db_manager, get_categories

bp = Blueprint("content", __name__)

IMAGE_DOC_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
DOCUMENT_EXTENSIONS = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "csv", "txt", "zip", "jpg", "jpeg", "png", "webp"}


logger = logging.getLogger(__name__)


def _sitemap():
	hook = current_app.config.get("REGENERATE_SITEMAP")
	if hook:
		hook()


def _section_form_defaults():
	return {
		"name": "", "singular_name": "", "section_path": "", "description": "",
		"date_label": "Date", "show_time": False, "fields_schema": [],
	}


def _section_from_request():
	form = request.form
	raw_fields = form.get("fields_json") or "[]"
	return {
		"name": form.get("name", ""),
		"singular_name": form.get("singular_name", ""),
		"section_path": form.get("section_path", ""),
		"description": form.get("description", ""),
		"date_label": form.get("date_label", ""),
		"show_time": form.get("show_time") == "1",
		"fields_schema": raw_fields,
	}


@bp.route("/admin/sections")
@capability_required("manage_settings")
def sections_index():
	sections = cs.list_sections()
	used = {s["section_path"] for s in sections}
	presets = {k: v for k, v in cs.section_presets().items() if v["section_path"] not in used}
	return render_template("sections.html", sections=sections, presets=presets)


@bp.route("/admin/sections/new", methods=["GET", "POST"])
@capability_required("manage_settings")
def section_new():
	errors = []
	section = _section_form_defaults()
	preset = request.args.get("preset")
	if request.method == "GET" and preset in cs.section_presets():
		section.update(cs.section_presets()[preset])
	if request.method == "POST":
		section = _section_from_request()
		saved, errors = cs.save_section(section)
		if saved:
			flash(f"Section created. Admins will now see “New {saved['singular_name'].lower()}” in the menu.", "success")
			return redirect(url_for("content.sections_index"))
		try:
			section["fields_schema"] = json.loads(section["fields_schema"] or "[]")
		except ValueError:
			section["fields_schema"] = []
	return render_template("section_form.html", section=section, errors=errors, mode="add", category_id=None)


@bp.route("/admin/sections/<int:category_id>", methods=["GET", "POST"])
@capability_required("manage_settings")
def section_edit(category_id):
	current = next((s for s in cs.list_sections() if s["id"] == category_id), None)
	if not current:
		abort(404)
	errors = []
	section = current
	if request.method == "POST":
		section = _section_from_request()
		saved, errors = cs.save_section(section, category_id)
		if saved:
			flash("Section saved.", "success")
			return redirect(url_for("content.sections_index"))
		try:
			section["fields_schema"] = json.loads(section["fields_schema"] or "[]")
		except ValueError:
			section["fields_schema"] = []
	return render_template("section_form.html", section=section, errors=errors, mode="edit", category_id=category_id)


@bp.route("/admin/sections/<int:category_id>/remove", methods=["POST"])
@capability_required("manage_settings")
def section_remove(category_id):
	if cs.detach_section(category_id):
		_sitemap()
		flash("The section no longer has its own address. Its posts were kept and stay reachable at /post/…", "success")
	else:
		flash("Section not found.", "error")
	return redirect(url_for("content.sections_index"))


@bp.route("/admin/sections/reorder", methods=["POST"])
@capability_required("manage_settings")
def sections_reorder():
	ids = [int(i) for i in (request.get_json(silent=True) or {}).get("ids", []) if str(i).isdigit()]
	cs.reorder_sections(ids)
	return jsonify({"status": "success"})


@bp.route("/admin/upload_file", methods=["POST"])
@capability_required("upload_media")
def upload_file():
	upload = request.files.get("file")
	if not upload or not upload.filename:
		return jsonify({"success": False, "error": "No file was sent."}), 400
	name = secure_filename(upload.filename)
	ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
	if ext not in DOCUMENT_EXTENSIONS:
		return jsonify({"success": False, "error": "This file type is not allowed. Use PDF, Word, Excel, PowerPoint, image, CSV or ZIP."}), 400
	folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "files")
	os.makedirs(folder, exist_ok=True)
	if ext in IMAGE_DOC_EXTENSIONS:
		# Images must really be images; they are re-encoded and named after the verified format.
		try:
			stored, _mime, _w, _h = images.store_verified_image(upload.stream, folder, uuid.uuid4().hex[:8] + "-" + os.path.splitext(name)[0][:80])
		except images.UnsafeImage:
			return jsonify({"success": False, "error": "The file is not a valid image."}), 400
	else:
		stored = f"{uuid.uuid4().hex[:8]}-{name}"
		upload.save(os.path.join(folder, stored))
	return jsonify({"success": True, "url": f"/uploads/files/{stored}", "name": name})


def _kind_or_404(kind):
	if kind not in cs.ENTRY_KINDS:
		abort(404)
	return cs.ENTRY_KINDS[kind]


@bp.route("/admin/people/<kind>")
@capability_required("manage_people")
def people_editor(kind):
	cfg = _kind_or_404(kind)
	records = cs.list_entries(kind, include_drafts=True)
	return render_template(
		"people.html", kind=kind, cfg=cfg, records=records,
		blank=cs.blank_entry(kind),
	)


@bp.route("/admin/people/<kind>/save", methods=["POST"])
@capability_required("manage_people")
def people_save(kind):
	_kind_or_404(kind)
	payload = request.get_json(silent=True) or {}
	saved, errors = cs.save_entry(kind, payload.get("record") or {}, payload.get("original_slug") or None)
	if not saved:
		return jsonify({"status": "error", "errors": errors}), 400
	_sitemap()
	return jsonify({"status": "success", "record": saved})


@bp.route("/admin/people/<kind>/delete", methods=["POST"])
@capability_required("manage_people")
def people_delete(kind):
	_kind_or_404(kind)
	slug = (request.get_json(silent=True) or {}).get("slug", "")
	if cs.delete_entry(kind, slug):
		_sitemap()
		return jsonify({"status": "success"})
	return jsonify({"status": "error", "errors": ["Not found."]}), 404


@bp.route("/admin/people/<kind>/reorder", methods=["POST"])
@capability_required("manage_people")
def people_reorder(kind):
	_kind_or_404(kind)
	cs.reorder_entries(kind, [str(s) for s in (request.get_json(silent=True) or {}).get("slugs", [])])
	return jsonify({"status": "success"})


@bp.route("/admin/people/<kind>/export")
@capability_required("manage_people")
def people_export(kind):
	_kind_or_404(kind)
	body = json.dumps(cs.export_entries(kind), ensure_ascii=False, indent=2)
	response = make_response(body)
	response.headers["Content-Type"] = "application/json; charset=utf-8"
	filename = cs.ENTRY_KINDS[kind].get("export_filename") or f"{kind}.json"
	response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
	return response


@bp.route("/admin/people/<kind>/import", methods=["POST"])
@capability_required("manage_people")
def people_import(kind):
	_kind_or_404(kind)
	upload = request.files.get("file")
	if not upload:
		flash("Choose a JSON file first.", "error")
		return redirect(url_for("content.people_editor", kind=kind))
	try:
		records = json.load(io.TextIOWrapper(upload.stream, encoding="utf-8-sig"))
		added, updated = cs.import_entries(kind, records, replace=request.form.get("replace") == "1")
	except (ValueError, UnicodeDecodeError) as e:
		flash(f"Could not read that file: {e}", "error")
		return redirect(url_for("content.people_editor", kind=kind))
	_sitemap()
	flash(f"Import finished: {added} added, {updated} updated.", "success")
	return redirect(url_for("content.people_editor", kind=kind))


def _directory_index(kind, groups, title):
	cfg = cs.ENTRY_KINDS[kind]
	labels = dict(cfg["groups"])
	records = cs.list_entries(kind, groups=groups)
	grouped = []
	for g in groups:
		members = [r for r in records if r["group"] == g]
		if members:
			grouped.append((labels[g], members))
	return render_template(
		"directory_index.html", kind=kind, cfg=cfg, title=title, grouped=grouped,
		active_section=kind, total=len(records),
	)


def directory_index_view(kind):
	cfg = cs.ENTRY_KINDS[kind]
	return _directory_index(kind, cfg["public_groups"], cfg["label"])


def directory_detail_view(kind, slug):
	record = cs.get_entry(kind, slug)
	if not record:
		abort(404)
	return render_template("directory_detail.html", kind=kind, cfg=cs.ENTRY_KINDS[kind], record=record, active_section=kind)


@bp.route("/<base>/group/<group>")
def directory_group(base, group):
	kind, cfg = cs.directory_for_path(base)
	if not kind or group not in dict(cfg["groups"]):
		abort(404)
	return _directory_index(kind, [group], dict(cfg["groups"])[group])


def section_index_view(section_path):
	"""Listing of a section. Also called from app.view_page for the address without a trailing slash."""
	kind, _cfg = cs.directory_for_path(section_path)
	if kind:
		return directory_index_view(kind)
	section = cs.get_section_by_path(section_path)
	if not section:
		abort(404)
	pagination = cs.list_section_posts(section["slug"], request.args.get("page", 1))
	return render_template("section_index.html", section=section, pagination=pagination, active_section=section_path)


@bp.route("/<section_path>/")
def section_index(section_path):
	return section_index_view(section_path)


@bp.route("/<section_path>/<slug>")
def section_detail(section_path, slug):
	kind, _cfg = cs.directory_for_path(section_path)
	if kind:
		return directory_detail_view(kind, slug)
	section = cs.get_section_by_path(section_path)
	if not section:
		abort(404)
	item = cs.get_section_post(section_path, slug)
	if not item:
		abort(404)
	return render_template("section_detail.html", section=section, item=item, active_section=section_path)


def _int(value):
	try:
		return int(value)
	except (TypeError, ValueError):
		return None


@bp.route("/admin/posts")
@login_required
def posts_list():
	me = current_user()
	only_mine = not can("edit_others_content")
	args = request.args
	result = cs.search_posts(
		q=args.get("q", ""), category=args.get("category", ""), status=args.get("status", ""),
		author_id=_int(args.get("author")), date_from=args.get("from", ""), date_to=args.get("to", ""),
		sort=args.get("sort", "newest"), page=args.get("page", 1),
		only_author_id=me["id"] if only_mine else None,
	)
	users = {u["id"]: u for u in db_manager.list_users()}
	for p in result["records"]:
		author = users.get(p.get("author_id"))
		p["author_name"] = author["display_name"] if author else "Unknown"
		p["can_modify"] = (not only_mine) or p.get("author_id") == me["id"]
	filters = {k: args.get(k, "") for k in ("q", "category", "status", "author", "from", "to", "sort")}
	query_string = urlencode({k: v for k, v in request.args.items() if k != "page" and v})
	return render_template(
		"posts.html", result=result, filters=filters, users=list(users.values()),
		categories=get_categories() or {}, sorts=cs.POST_SORTS, only_mine=only_mine, query_string=query_string,
		can_publish=can("publish_posts"), can_bulk_delete=True,
	)


@bp.route("/admin/posts/bulk", methods=["POST"])
@login_required
def posts_bulk():
	me = current_user()
	action = request.form.get("action", "")
	ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
	back = request.form.get("back") or url_for("content.posts_list")
	if not back.startswith("/admin/posts"):
		back = url_for("content.posts_list")
	if not ids or action not in ("publish", "draft", "delete", "category"):
		flash("Select at least one post and choose an action.", "error")
		return redirect(back)
	changed, skipped = cs.bulk_update_posts(
		action, ids, can("publish_posts"), can("edit_others_content"), me["id"],
		category_slug=request.form.get("category", ""),
	)
	_sitemap()
	verb = {"publish": "published", "draft": "moved to draft", "delete": "deleted", "category": "moved"}[action]
	msg = f"{changed} post(s) {verb}."
	if skipped:
		msg += f" {skipped} skipped (not allowed)."
	flash(msg, "success" if changed else "error")
	return redirect(back)
