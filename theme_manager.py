"""
Themes: upload, list, activate, delete.

A theme is a folder in themes/ with a templates/ directory and (recommended) a theme.json:

	{"name": "My Theme", "slug": "my-theme", "version": "1.0.0", "author": "...",
	 "description": "...", "requires_core": "1.1.0"}

A theme may also contain page_templates/ (custom page fields), static/ and functions/.
Themes installed by an extension are listed but managed by that extension.
"""

import json
import os
import re
import shutil
import tempfile
import zipfile
from typing import Any, Dict, List

import extension_manager as em

THEMES_DIR = em.THEMES_DIR
SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,40}$")


def read_theme(folder: str, slug: str) -> Dict[str, Any]:
	meta: Dict[str, Any] = {}
	try:
		with open(os.path.join(folder, "theme.json"), encoding="utf-8") as fh:
			meta = json.load(fh)
		if not isinstance(meta, dict):
			meta = {}
	except (OSError, ValueError):
		pass
	info = {
		"slug": slug,
		"name": str(meta.get("name") or slug),
		"version": str(meta.get("version") or ""),
		"author": str(meta.get("author") or ""),
		"description": str(meta.get("description") or ""),
		"requires_core": str(meta.get("requires_core") or ""),
		"has_theme_json": bool(meta),
	}
	info["compatible"] = not info["requires_core"] or em.version_tuple(info["requires_core"]) <= em.version_tuple(em.CORE_VERSION)
	try:
		with open(os.path.join(folder, em.THEME_MARKER), encoding="utf-8") as fh:
			info["extension"] = fh.read().strip().split("@")[0]
	except OSError:
		info["extension"] = ""
	return info


def list_themes() -> List[Dict[str, Any]]:
	themes = []
	if os.path.isdir(THEMES_DIR):
		for name in sorted(os.listdir(THEMES_DIR)):
			folder = os.path.join(THEMES_DIR, name)
			if os.path.isdir(os.path.join(folder, "templates")):
				themes.append(read_theme(folder, name))
	return themes


def install_dir(root: str, replace: bool = True) -> Dict[str, Any]:
	if not os.path.isdir(os.path.join(root, "templates")):
		raise em.ExtensionError("A theme needs a templates/ folder.")
	slug = read_theme(root, os.path.basename(root.rstrip("/\\")))["slug"]
	try:
		with open(os.path.join(root, "theme.json"), encoding="utf-8") as fh:
			slug = str(json.load(fh).get("slug") or slug)
	except (OSError, ValueError, AttributeError):
		pass
	if not SLUG_RE.match(slug):
		raise em.ExtensionError("The theme name may only contain letters, digits, hyphens and underscores.")
	if slug == "default":
		raise em.ExtensionError("The built-in default theme cannot be replaced.")
	info = read_theme(root, slug)
	if not info["compatible"]:
		raise em.ExtensionError(f"{info['name']} needs FlexaFlow {info['requires_core']} or newer (this is {em.CORE_VERSION}).")
	dest = os.path.join(THEMES_DIR, slug)
	if os.path.isdir(dest):
		if read_theme(dest, slug)["extension"]:
			raise em.ExtensionError(f"'{slug}' is provided by an extension.")
		if not replace:
			raise em.ExtensionError("already installed (tick Overwrite to replace it)")
		shutil.rmtree(dest)
	shutil.copytree(root, dest)
	return info


def install_zip(stream) -> Dict[str, Any]:
	tmp = tempfile.mkdtemp(prefix="theme-")
	try:
		try:
			with zipfile.ZipFile(stream) as zf:
				em._safe_extract(zf, tmp)
		except zipfile.BadZipFile:
			raise em.ExtensionError("That file is not a valid .zip archive.")
		root = tmp
		if not os.path.isdir(os.path.join(root, "templates")):
			entries = [e for e in os.listdir(tmp) if not e.startswith(("__MACOSX", "."))]
			if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])):
				root = os.path.join(tmp, entries[0])
		return install_dir(root)
	finally:
		shutil.rmtree(tmp, ignore_errors=True)


def delete_theme(slug: str) -> None:
	if not SLUG_RE.match(slug) or slug == "default":
		raise em.ExtensionError("This theme cannot be deleted.")
	folder = os.path.join(THEMES_DIR, slug)
	if not os.path.isdir(folder):
		raise em.ExtensionError("Theme not found.")
	if read_theme(folder, slug)["extension"]:
		raise em.ExtensionError("This theme belongs to an extension. Deactivate or delete the extension instead.")
	shutil.rmtree(folder)
	shutil.rmtree(os.path.join(em.STATIC_DIR, slug), ignore_errors=True)
