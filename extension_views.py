"""Admin screens for extensions: list, upload, activate, deactivate, settings, delete."""

import os

from flask import flash, redirect, render_template, request, url_for, abort

import extension_manager as em
from auth import capability_required


def _setting_fields(manifest):
	fields = []
	for f in manifest.get("settings") or []:
		if isinstance(f, dict) and f.get("name"):
			fields.append({
				"name": str(f["name"]), "label": str(f.get("label") or f["name"]),
				"type": f.get("type") if f.get("type") in ("text", "textarea", "url", "email", "number", "checkbox", "select") else "text",
				"help": str(f.get("help") or ""), "default": f.get("default", ""),
				"options": [str(o) for o in f.get("options", [])] if isinstance(f.get("options"), list) else [],
			})
	return fields


def init(app, manager):
	allow_upload = os.environ.get("FLEXAFLOW_ALLOW_EXTENSION_UPLOAD", "0") == "1"

	@app.route("/admin/extensions")
	@capability_required("manage_extensions")
	def extensions_index():
		items = em.list_installed()
		for item in items:
			item["active"] = manager.is_active(item["slug"])
			item["load_error"] = manager.errors.get(item["slug"])
			reg = manager.registrations.get(item["slug"])
			item["has_demo"] = bool(reg and reg.demo)
			item["demo"] = reg.demo if reg else None
			item["has_settings"] = bool(item.get("settings"))
			item["provides"] = []
			if reg:
				item["provides"] += [f"Directory: {d['label']}" for d in reg.directories.values()]
				item["provides"] += [f"Theme: {t}" for t in reg.themes]
				item["provides"] += [f"Section preset: {p.get('name', k)}" for k, p in reg.section_presets.items()]
		return render_template("extensions.html", items=items, core_version=em.CORE_VERSION, allow_upload=allow_upload)

	@app.route("/admin/extensions/upload", methods=["POST"])
	@capability_required("manage_extensions")
	def extensions_upload():
		if not allow_upload:
			flash("Uploading extensions is turned off on this server.", "error")
			return redirect(url_for("extensions_index"))
		upload = request.files.get("file")
		if not upload or not upload.filename.lower().endswith(".zip"):
			flash("Choose an extension .zip file.", "error")
			return redirect(url_for("extensions_index"))
		was_active = False
		try:
			manifest = em.install_zip(upload.stream)
			was_active = manager.is_active(manifest["slug"])
			if was_active:
				manager.sync(force=True)
			flash(f"{manifest['name']} {manifest['version']} was {'updated' if manifest['replaced'] else 'installed'}. "
				+ ("It is still active." if was_active else "Click Activate to switch it on."), "success")
		except em.ExtensionError as e:
			flash(str(e), "error")
		return redirect(url_for("extensions_index"))

	@app.route("/admin/extensions/<slug>/activate", methods=["POST"])
	@capability_required("manage_extensions")
	def extension_activate(slug):
		try:
			manager.activate(slug)
			flash("Extension activated.", "success")
		except em.ExtensionError as e:
			flash(f"Could not activate: {e}", "error")
		return redirect(url_for("extensions_index"))

	@app.route("/admin/extensions/<slug>/deactivate", methods=["POST"])
	@capability_required("manage_extensions")
	def extension_deactivate(slug):
		manager.deactivate(slug)
		flash("Extension deactivated. Your content is kept; it is hidden until you activate the extension again.", "success")
		return redirect(url_for("extensions_index"))

	@app.route("/admin/extensions/<slug>/delete", methods=["POST"])
	@capability_required("manage_extensions")
	def extension_delete(slug):
		try:
			manager.deactivate(slug)
			em.remove_files(slug)
			flash("Extension deleted from the server. Content it created is kept.", "success")
		except em.ExtensionError as e:
			flash(str(e), "error")
		return redirect(url_for("extensions_index"))

	@app.route("/admin/extensions/<slug>/demo", methods=["POST"])
	@capability_required("manage_extensions")
	def extension_demo(slug):
		try:
			flash(manager.run_demo(slug, overwrite=request.form.get("overwrite") == "1"), "success")
		except em.ExtensionError as e:
			flash(str(e), "error")
		return redirect(url_for("extensions_index"))

	@app.route("/admin/extensions/<slug>/settings", methods=["GET", "POST"])
	@capability_required("manage_extensions")
	def extension_settings(slug):
		reg = manager.registrations.get(slug)
		if not reg:
			abort(404)
		fields = _setting_fields(reg.manifest)
		api = em.ExtensionAPI(manager, reg)
		if request.method == "POST":
			data = {}
			for f in fields:
				if f["type"] == "checkbox":
					data[f["name"]] = request.form.get(f["name"]) == "on"
				else:
					data[f["name"]] = (request.form.get(f["name"]) or "")[:5000]
			api.settings.replace(data)
			manager.on_changed()
			flash("Settings saved.", "success")
			return redirect(url_for("extension_settings", slug=slug))
		current = api.settings.all()
		return render_template("extension_settings.html", ext=reg.manifest, fields=fields, values={f["name"]: current.get(f["name"], f["default"]) for f in fields})
