"""Admin: Site > Export / Import (site packages)."""

import datetime
import io
import os
import re

from flask import flash, redirect, render_template, request, send_file, url_for

import extension_manager as em
import migrate as mg
import site_package as sp
import theme_manager as tm
from auth import capability_required
from data_store import get_site_settings


def _checked(name, default=False):
	if request.form:
		return request.form.get(name) == "1"
	return default


def init(app, manager, current_theme, legacy_xml_import):
	@app.route("/admin/export", methods=["GET", "POST"])
	@capability_required("manage_settings")
	def export_site():
		installed = [e for e in em.list_installed() if not e.get("error")]
		themes = [t for t in tm.list_themes() if t["slug"] != "default" and not t["extension"]]
		if request.method == "POST":
			form = request.form
			title = (get_site_settings().get("site_title") or "site")
			slug = re.sub(r"[^a-z0-9]+", "-", str(title).lower()).strip("-") or "site"
			wants_ext = form.get("packages_extensions") == "1"
			wants_theme = form.get("packages_themes") == "1"
			data = sp.export_package(
				title, manager.active_slugs(), current_theme(),
				content=form.get("content") == "1", settings=form.get("settings") == "1",
				extension_data=form.get("extension_data") == "1", media=form.get("media") == "1",
				extensions=wants_ext, themes=wants_theme,
				selected_extensions=set(form.getlist("ext")) if wants_ext else None,
				selected_themes=set(form.getlist("theme")) if wants_theme else None,
				extension_exports=manager.export_extension_data() if form.get("extension_data") == "1" else None,
			)
			stamp = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M")
			return send_file(io.BytesIO(data), mimetype="application/zip", as_attachment=True,
				download_name=f"flexaflow-{slug}-{stamp}.zip")
		return render_template("export.html", extensions=installed, themes=themes, active=manager.active_slugs())

	@app.route("/admin/import", methods=["GET", "POST"])
	@capability_required("manage_settings")
	def import_site():
		if request.method == "GET":
			return render_template("import.html")
		upload = request.files.get("file")
		if not upload:
			flash("Choose a file to import.", "error")
			return redirect(url_for("import_site"))
		if upload.filename.lower().endswith(".xml"):
			kind = mg.detect_format(upload.stream.read(70000))
			upload.stream.seek(0)
			if kind in ("wordpress", "blogger"):
				flash(f"That is a {kind.capitalize()} export. Import it here, where you can choose how it is imported.", "success")
				return redirect(url_for("migrate_page"))
			return legacy_xml_import()
		form = request.form
		try:
			manifest = sp.read_manifest(upload.stream)
			upload.stream.seek(0)
			lines = sp.import_package(
				upload.stream,
				content=form.get("content") == "1", settings=form.get("settings") == "1",
				extension_data=form.get("extension_data") == "1", media=form.get("media") == "1",
				extensions=form.get("packages_extensions") == "1", themes=form.get("packages_themes") == "1",
				overwrite=form.get("overwrite") == "1", restore_state=form.get("restore_state") == "1",
				write_setting=manager.write_setting,
				extension_importer=manager.import_extension_data,
			)
			if form.get("restore_state") == "1":
				manager.sync(force=True)
			lines.insert(0, f"Package from FlexaFlow {manifest.get('core_version', '?')} ({manifest.get('exported_at', '')}).")
			manager.on_changed()
			for line in lines:
				flash(line, "success")
		except em.ExtensionError as e:
			flash(str(e), "error")
		return redirect(url_for("import_site"))
