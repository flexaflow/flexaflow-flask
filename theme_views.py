"""Admin: Site > Themes (upload, activate, delete)."""

import os

from flask import abort, flash, redirect, render_template, request, url_for

import extension_manager as em
import theme_manager as tm
from auth import capability_required


def init(app, current_theme, switch_theme):
	allow_upload = os.environ.get("FLEXAFLOW_ALLOW_EXTENSION_UPLOAD", "1") != "0"

	@app.route("/admin/themes")
	@capability_required("manage_extensions")
	def themes_index():
		return render_template("themes.html", themes=tm.list_themes(), active=current_theme(), core_version=em.CORE_VERSION, allow_upload=allow_upload)

	@app.route("/admin/themes/upload", methods=["POST"])
	@capability_required("manage_extensions")
	def themes_upload():
		upload = request.files.get("file")
		if not allow_upload:
			flash("Uploading is turned off on this server.", "error")
		elif not upload or not upload.filename.lower().endswith(".zip"):
			flash("Choose a theme .zip file.", "error")
		else:
			try:
				info = tm.install_zip(upload.stream)
				if info["slug"] == current_theme():
					switch_theme(info["slug"])
				flash(f"Theme {info['name']} {info['version']} installed. Click Activate to use it.", "success")
			except em.ExtensionError as e:
				flash(str(e), "error")
		return redirect(url_for("themes_index"))

	@app.route("/admin/themes/<slug>/activate", methods=["POST"])
	@capability_required("manage_extensions")
	def theme_activate(slug):
		match = [t for t in tm.list_themes() if t["slug"] == slug]
		if not match:
			abort(404)
		if not match[0]["compatible"]:
			flash(f"This theme needs FlexaFlow {match[0]['requires_core']} or newer.", "error")
		elif switch_theme(slug):
			flash(f"{match[0]['name']} is now the active theme.", "success")
		else:
			flash("The theme could not be activated.", "error")
		return redirect(url_for("themes_index"))

	@app.route("/admin/themes/<slug>/delete", methods=["POST"])
	@capability_required("manage_extensions")
	def theme_delete(slug):
		if slug == current_theme():
			flash("Activate another theme before deleting this one.", "error")
		else:
			try:
				tm.delete_theme(slug)
				flash("Theme deleted.", "success")
			except em.ExtensionError as e:
				flash(str(e), "error")
		return redirect(url_for("themes_index"))
