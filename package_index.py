"""Site > Packages: browse and install the extensions and themes listed in packages/index.json.

The index records each zip's SHA-256. A zip whose hash does not match is refused, so a file that was
swapped or damaged on disk is not installed. Installing never activates anything; the administrator
still reads the trust warning on the Extensions or Themes screen and activates it there.
"""
import hashlib
import json
import logging
import os
import re

from flask import abort, flash, redirect, render_template, request, url_for

import extension_manager as em
import theme_manager as tm
from auth import capability_required, log_event

logger = logging.getLogger(__name__)
PACKAGES_DIR = os.path.join(em.BASE_DIR, "packages")
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.zip$")


def load_index() -> list:
	try:
		with open(os.path.join(PACKAGES_DIR, "index.json"), encoding="utf-8") as fh:
			data = json.load(fh)
	except (OSError, ValueError):
		return []
	items = data.get("packages") if isinstance(data, dict) else None
	return [p for p in items if isinstance(p, dict) and p.get("slug") and p.get("file")] if isinstance(items, list) else []


def _status(entry: dict, installed: dict) -> dict:
	have = installed.get((entry.get("kind"), entry["slug"]))
	needs = entry.get("requires_core") or ""
	compatible = not needs or em.version_tuple(needs) <= em.version_tuple(em.CORE_VERSION)
	if have is None:
		state = "available"
	elif em.version_tuple(str(have)) < em.version_tuple(str(entry.get("version", "0"))):
		state = "update"
	else:
		state = "installed"
	return dict(entry, installed_version=have, state=state, compatible=compatible)


def init(app):
	@app.route("/admin/packages")
	@capability_required("manage_extensions")
	def packages_index():
		installed = {("extension", e["slug"]): e.get("version") for e in em.list_installed() if not e.get("error")}
		installed.update({("theme", t["slug"]): t.get("version") or "0" for t in tm.list_themes()})
		items = [_status(p, installed) for p in load_index()]
		return render_template("packages.html", packages=items)

	@app.route("/admin/packages/install", methods=["POST"])
	@capability_required("manage_extensions")
	def packages_install():
		slug, kind = request.form.get("slug", ""), request.form.get("kind", "")
		entry = next((p for p in load_index() if p["slug"] == slug and p.get("kind") == kind), None)
		if not entry or not FILE_RE.match(str(entry["file"])):
			abort(404)
		path = os.path.join(PACKAGES_DIR, entry["file"])
		if not os.path.isfile(path):
			flash("The package file is missing on the server.", "error")
			return redirect(url_for("packages_index"))
		with open(path, "rb") as fh:
			blob = fh.read()
		if hashlib.sha256(blob).hexdigest() != entry.get("sha256"):
			log_event("package_hash_mismatch", package=slug)
			flash("The file does not match the checksum in the index, so it was not installed.", "error")
			return redirect(url_for("packages_index"))
		import io
		try:
			(em.install_zip if kind == "extension" else tm.install_zip)(io.BytesIO(blob))
		except em.ExtensionError as e:
			flash(str(e), "error")
			return redirect(url_for("packages_index"))
		log_event("package_installed", package=slug, kind=kind, version=entry.get("version"))
		flash(f"{entry.get('name', slug)} {entry.get('version', '')} installed. Review it on the "
		      f"{'Extensions' if kind == 'extension' else 'Themes'} screen, then activate it.", "success")
		return redirect(url_for("packages_index"))
