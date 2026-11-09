"""Backups: build a site package on demand and keep the newest few in instance/backups/."""
import datetime
import os
import re

from flask import flash, redirect, send_file

import extension_manager as em
import site_package as sp
from data_store import get_site_settings

KEEP = 10
NAME_RE = re.compile(r"^backup-\d{8}-\d{6}\.zip$")


def backup_dir() -> str:
	path = os.environ.get("FLEXAFLOW_BACKUP_DIR") or os.path.join(em.BASE_DIR, "instance", "backups")
	os.makedirs(path, exist_ok=True)
	return path


def list_backups():
	folder = backup_dir()
	rows = []
	for name in sorted((n for n in os.listdir(folder) if NAME_RE.match(n)), reverse=True):
		st = os.stat(os.path.join(folder, name))
		rows.append({"name": name, "size_mb": round(st.st_size / 1048576, 2),
		             "when": datetime.datetime.utcfromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")})
	return rows


def create_backup(manager_active, theme: str, extension_exports=None) -> str:
	settings = get_site_settings()
	data = sp.export_package(settings.get("site_title") or "site", manager_active, theme,
	                         content=True, settings=True, extension_data=True, media=True, extension_exports=extension_exports)
	name = "backup-" + datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S") + ".zip"
	with open(os.path.join(backup_dir(), name), "wb") as fh:
		fh.write(data)
	for old in list_backups()[KEEP:]:
		try:
			os.remove(os.path.join(backup_dir(), old["name"]))
		except OSError:
			pass
	return name


def register(api):
	manager = api._manager

	@api.admin_route("/", capability="manage_settings")
	def index():
		from flask import session
		return api.render("index.html", backups=list_backups(), keep=KEEP, csrf=session.get("csrf_token", ""))

	@api.admin_route("/create", capability="manage_settings", methods=("POST",))
	def create():
		theme = get_site_settings().get("theme", "default")
		name = create_backup(manager.active_slugs(), theme, manager.export_extension_data())
		flash(f"Backup {name} created.", "success")
		return redirect("/admin/ext/backups/")

	@api.admin_route("/download/<name>", capability="manage_settings")
	def download(name):
		if not NAME_RE.match(name) or not os.path.isfile(os.path.join(backup_dir(), name)):
			from flask import abort
			abort(404)
		return send_file(os.path.join(backup_dir(), name), as_attachment=True, download_name=name)

	api.add_admin_menu("Backups", [{"label": "Site backups", "url": "/admin/ext/backups/"}], capability="manage_settings")
