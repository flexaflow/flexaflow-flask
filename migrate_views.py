"""Admin: Site > Migrate (WordPress / Blogger import and export)."""

import datetime
import io
import os
import re
import uuid
from urllib.parse import urlparse

from flask import Request, flash, redirect, render_template, request, send_file, session, url_for

import migrate as mg
from auth import capability_required, current_user
from data_store import db_manager, get_site_settings
from migrate_utils import MigrationError

MAX_MIGRATION_UPLOAD = 128 * 1024 * 1024
MIME_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}


class MigrationRequest(Request):
	"""Allows big WordPress/Blogger export files, but only for signed-in users on the import route."""

	@property
	def max_content_length(self):
		if self.path == "/admin/migrate/import" and session.get("user_id"):
			return MAX_MIGRATION_UPLOAD
		return self.app_limit()

	@max_content_length.setter
	def max_content_length(self, value):
		self._max_len = value

	def app_limit(self):
		from flask import current_app
		return getattr(self, "_max_len", None) or current_app.config.get("MAX_CONTENT_LENGTH")


def media_storer():
	"""Returns store(data, original_name, mime) -> '/uploads/<file>' that registers the file in the media library."""
	import app as core
	from PIL import Image

	def store(data: bytes, original_name: str, mime: str):
		ext = MIME_EXT.get(mime)
		if not ext:
			return None
		filename = f"{uuid.uuid4()}{ext}"
		path = os.path.join(core.app.config["UPLOAD_FOLDER"], filename)
		os.makedirs(core.app.config["UPLOAD_FOLDER"], exist_ok=True)
		with open(path, "wb") as fh:
			fh.write(data)
		try:
			with Image.open(path) as img:
				img.verify()
			with Image.open(path) as img:
				width, height = img.size
			thumbs = core.create_image_thumbnails(path, filename)
			base = os.path.splitext(re.sub(r"[^\w.\-]", "_", original_name))[0][:200] or "image"
			ok = db_manager.add_media({
				"filename": filename, "original_filename": base + ext, "mime_type": mime, "file_size": len(data),
				"width": width, "height": height, "alt_text": base, "caption": "", "title": base,
				"description": "", "thumbnail": thumbs.get("thumbnail"),
			})
			if not ok:
				raise ValueError("database refused the media record")
			return f"/uploads/{filename}"
		except Exception:
			try:
				os.remove(path)
			except OSError:
				pass
			return None

	return store


def _flag(name, default=False):
	return request.form.get(name) == "1" if request.form else default


def _base_url(raw: str) -> str:
	raw = (raw or "").strip().rstrip("/")
	parts = urlparse(raw if "://" in raw else "https://" + raw)
	if not parts.hostname:
		raise MigrationError("Enter the public address of the site, for example https://example.com")
	return f"{parts.scheme}://{parts.netloc}"


def init(app, reserved_slugs):
	@app.route("/admin/migrate", methods=["GET"])
	@capability_required("manage_settings")
	def migrate_page():
		site = (get_site_settings().get("site_domain") or "").strip() or request.host_url.rstrip("/")
		return render_template("migrate.html", default_base=site)

	@app.route("/admin/migrate/import", methods=["POST"])
	@capability_required("manage_settings")
	def migrate_import():
		try:
			upload = request.files.get("file")
			if not upload or not upload.filename:
				raise MigrationError("Choose the export file first.")
			data = upload.stream.read()
			source = request.form.get("source", "auto")
			if source == "auto":
				source = mg.detect_format(data) or ""
			if source == "flexaflow":
				raise MigrationError("That is a FlexaFlow export. Use Site > Import for it.")
			if source not in ("wordpress", "blogger"):
				raise MigrationError("Could not tell whether this is a WordPress or a Blogger export. Pick the source from the list.")
			user = current_user()
			opts = mg.ImportOptions(
				conflict=request.form.get("conflict", "skip") if request.form.get("conflict") in ("skip", "rename", "update") else "skip",
				include_drafts=_flag("drafts", True), import_pages=_flag("pages", True),
				import_settings=_flag("settings"), import_menu=_flag("menu"), download_media=_flag("media"),
				author_id=user["id"] if user else None, reserved_slugs=set(reserved_slugs),
			)
			for line in mg.import_file(data, source, opts, store_media=media_storer() if opts.download_media else None):
				flash(line, "success")
		except MigrationError as e:
			flash(str(e), "error")
		return redirect(url_for("migrate_page"))

	@app.route("/admin/migrate/export", methods=["POST"])
	@capability_required("manage_settings")
	def migrate_export():
		try:
			target = request.form.get("target")
			base = _base_url(request.form.get("base_url", ""))
			drafts, pages = _flag("drafts", True), _flag("pages", True)
			stamp = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M")
			if target == "wordpress":
				data = mg.export_wordpress(base, include_drafts=drafts, include_pages=pages,
					include_media=_flag("media", True), include_menu=_flag("menu", True))
				name = f"wordpress-export-{stamp}.xml"
			elif target == "blogger":
				data = mg.export_blogger(base, include_drafts=drafts, include_pages=pages)
				name = f"blogger-backup-{stamp}.xml"
			else:
				raise MigrationError("Choose WordPress or Blogger.")
			return send_file(io.BytesIO(data), mimetype="application/xml", as_attachment=True, download_name=name)
		except MigrationError as e:
			flash(str(e), "error")
			return redirect(url_for("migrate_page"))
