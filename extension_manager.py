"""
FlexaFlow extension system.

An extension is a folder (or a .zip of it) containing an ``extension.json`` manifest and,
usually, an ``extension.py`` with a ``register(api)`` function. Extensions are installed
to ``extensions/<slug>/`` and switched on and off from Admin > Site > Extensions without
restarting the server.

    extensions/my-extension/
        extension.json        name, slug, version, description, author, requires_core, settings
        extension.py          def register(api): ...
        templates/            used as render_template("my-extension/<file>")
        static/               served at /ext/my-extension/static/<file>
        themes/<theme>/       copied into themes/ while the extension is active
        ...                   anything else the extension needs (python modules, seed data)

What ``register(api)`` can do is listed on ExtensionAPI below and in docs/EXTENSIONS.md.

SECURITY: an extension is Python code that runs with the full rights of the site. Only
administrators can install one, and only code from a source you trust should be installed.
Set FLEXAFLOW_ALLOW_EXTENSION_UPLOAD=0 to turn browser uploads off (extensions can still be
copied into extensions/ on the server).
"""

import importlib
import json
import os
import logging
import re
import shutil
import sys
import tempfile
import threading
import traceback
import types
import zipfile
from typing import Any, Callable, Dict, List, Optional, Tuple

from markupsafe import Markup
from werkzeug.routing import Map, Rule
from werkzeug.exceptions import NotFound, MethodNotAllowed

CORE_VERSION = "1.1.0"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXTENSIONS_DIR = os.environ.get("FLEXAFLOW_EXTENSIONS_DIR") or os.path.join(BASE_DIR, "extensions")
THEMES_DIR = os.path.join(BASE_DIR, "themes")
STATIC_DIR = os.path.join(BASE_DIR, "static")

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
MAX_ZIP_BYTES = 60 * 1024 * 1024
MAX_FILES = 3000
THEME_MARKER = ".flexaflow-extension"

CORE_PREFIXES = ("admin", "static", "uploads", "login", "logout", "api", "post", "page", "category", "tag", "search", "ext")


logger = logging.getLogger(__name__)


class ExtensionError(Exception):
	"""A problem with an extension that should be shown to the administrator."""


def version_tuple(text: str) -> Tuple[int, ...]:
	parts = re.findall(r"\d+", str(text or "0"))[:3]
	return tuple(int(p) for p in parts) + (0,) * (3 - len(parts))


def read_manifest(folder: str) -> Dict[str, Any]:
	path = os.path.join(folder, "extension.json")
	try:
		with open(path, encoding="utf-8") as fh:
			data = json.load(fh)
	except FileNotFoundError:
		raise ExtensionError("extension.json is missing.")
	except (OSError, ValueError) as e:
		raise ExtensionError(f"extension.json could not be read: {e}")
	if not isinstance(data, dict):
		raise ExtensionError("extension.json must contain a JSON object.")
	slug = str(data.get("slug") or "")
	if not SLUG_RE.match(slug):
		raise ExtensionError("The slug in extension.json must be 2-41 characters: lowercase letters, digits and hyphens.")
	if not str(data.get("name") or "").strip():
		raise ExtensionError("extension.json needs a name.")
	manifest = {
		"slug": slug,
		"name": str(data["name"]).strip()[:100],
		"version": str(data.get("version") or "0.0.0")[:20],
		"description": str(data.get("description") or "")[:600],
		"author": str(data.get("author") or "")[:100],
		"homepage": str(data.get("homepage") or "")[:300],
		"requires_core": str(data.get("requires_core") or "1.0.0")[:20],
		"tested_up_to": str(data.get("tested_up_to") or "")[:20],
		"entry": str(data.get("entry") or "extension.py"),
		"settings": data.get("settings") if isinstance(data.get("settings"), list) else [],
	}
	if "/" in manifest["entry"] or "\\" in manifest["entry"] or not manifest["entry"].endswith(".py"):
		raise ExtensionError("'entry' must be a .py file in the extension's top folder.")
	if version_tuple(manifest["requires_core"]) > version_tuple(CORE_VERSION):
		raise ExtensionError(f"Needs FlexaFlow {manifest['requires_core']} or newer (this is {CORE_VERSION}).")
	return manifest


def folder_digest(folder: str) -> str:
	"""SHA-256 over every file's relative path and bytes (skips caches). Lets an admin compare a package with the author's published value."""
	import hashlib
	h = hashlib.sha256()
	for root, dirs, files in os.walk(folder):
		dirs[:] = sorted(d for d in dirs if d not in ("__pycache__", ".git"))
		for name in sorted(files):
			if name.endswith(".pyc") or name == THEME_MARKER:
				continue
			full = os.path.join(root, name)
			h.update(os.path.relpath(full, folder).replace(os.sep, "/").encode())
			try:
				with open(full, "rb") as fh:
					h.update(fh.read())
			except OSError:
				pass
	return h.hexdigest()


def list_installed() -> List[Dict[str, Any]]:
	"""Manifests of every folder in extensions/ (broken ones are listed with an 'error')."""
	found = []
	if not os.path.isdir(EXTENSIONS_DIR):
		return found
	for name in sorted(os.listdir(EXTENSIONS_DIR)):
		folder = os.path.join(EXTENSIONS_DIR, name)
		if not os.path.isdir(folder) or name.startswith((".", "_")):
			continue
		try:
			manifest = read_manifest(folder)
			manifest["folder"] = name
			manifest["digest"] = folder_digest(folder)
			found.append(manifest)
		except ExtensionError as e:
			found.append({"slug": name, "folder": name, "name": name, "version": "?", "description": "", "author": "", "error": str(e), "settings": []})
	return found


def _safe_extract(zf: zipfile.ZipFile, dest: str, limit: Optional[int] = None) -> None:
	members = zf.infolist()
	if len(members) > MAX_FILES:
		raise ExtensionError("The archive contains too many files.")
	if sum(m.file_size for m in members) > (limit or MAX_ZIP_BYTES * 3):
		raise ExtensionError("The archive is too large when unpacked.")
	root = os.path.realpath(dest)
	for m in members:
		name = m.filename.replace("\\", "/")
		if name.startswith("/") or ".." in name.split("/") or re.match(r"^[A-Za-z]:", name):
			raise ExtensionError(f"Unsafe path in archive: {m.filename}")
		if (m.external_attr >> 16) & 0o170000 == 0o120000:
			raise ExtensionError(f"Links are not allowed in extensions: {m.filename}")
		target = os.path.realpath(os.path.join(root, name))
		if not (target == root or target.startswith(root + os.sep)):
			raise ExtensionError(f"Unsafe path in archive: {m.filename}")
	zf.extractall(root)


def install_zip(stream, replace: bool = True) -> Dict[str, Any]:
	"""
	Install an extension from an uploaded .zip. Returns its manifest (plus ``replaced``).
	The archive may have extension.json at its top level or inside a single folder.
	"""
	os.makedirs(EXTENSIONS_DIR, exist_ok=True)
	tmp = tempfile.mkdtemp(prefix="ext-", dir=EXTENSIONS_DIR)
	try:
		try:
			with zipfile.ZipFile(stream) as zf:
				if sum(m.compress_size for m in zf.infolist()) > MAX_ZIP_BYTES:
					raise ExtensionError("The file is too large.")
				_safe_extract(zf, tmp)
		except zipfile.BadZipFile:
			raise ExtensionError("That file is not a valid .zip archive.")
		root = tmp
		if not os.path.exists(os.path.join(root, "extension.json")):
			entries = [e for e in os.listdir(tmp) if not e.startswith(("__MACOSX", "."))]
			if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])):
				root = os.path.join(tmp, entries[0])
		manifest = read_manifest(root)
		if not os.path.exists(os.path.join(root, manifest["entry"])) and not os.path.isdir(os.path.join(root, "themes")) and not os.path.isdir(os.path.join(root, "templates")):
			raise ExtensionError(f"{manifest['entry']} was not found in the extension.")
		dest = os.path.join(EXTENSIONS_DIR, manifest["slug"])
		replaced = os.path.isdir(dest)
		if replaced and not replace:
			raise ExtensionError("An extension with this slug is already installed.")
		if replaced:
			shutil.rmtree(dest)
		shutil.move(root, dest)
		manifest["replaced"] = replaced
		return manifest
	finally:
		shutil.rmtree(tmp, ignore_errors=True)


def install_dir(root: str, replace: bool = True) -> Dict[str, Any]:
	"""Install an already unpacked extension folder (used by site-package import)."""
	manifest = read_manifest(root)
	dest = os.path.join(EXTENSIONS_DIR, manifest["slug"])
	replaced = os.path.isdir(dest)
	if replaced and not replace:
		raise ExtensionError("already installed (tick Overwrite to replace it)")
	os.makedirs(EXTENSIONS_DIR, exist_ok=True)
	if replaced:
		shutil.rmtree(dest)
	shutil.copytree(root, dest)
	manifest["replaced"] = replaced
	return manifest


def remove_files(slug: str) -> None:
	if not SLUG_RE.match(slug):
		raise ExtensionError("Invalid slug.")
	folder = os.path.join(EXTENSIONS_DIR, slug)
	if os.path.isdir(folder):
		shutil.rmtree(folder)


class Registration:
	"""Everything one active extension has registered (so it can all be removed again)."""

	def __init__(self, manifest: Dict[str, Any], path: str):
		self.manifest = manifest
		self.slug = manifest["slug"]
		self.path = path
		self.directories: Dict[str, Dict[str, Any]] = {}
		self.section_presets: Dict[str, Dict[str, Any]] = {}
		self.routes: List[Tuple[Rule, Callable]] = []
		self.admin_menus: List[Dict[str, Any]] = []
		self.actions: Dict[str, List[Tuple[int, Callable]]] = {}
		self.filters: Dict[str, List[Tuple[int, Callable]]] = {}
		self.template_globals: Dict[str, Any] = {}
		self.demo: Optional[Dict[str, Any]] = None
		self.on_activate: List[Callable] = []
		self.on_deactivate: List[Callable] = []
		self.themes: List[str] = []
		self.exporter: Optional[Tuple[Callable, Callable]] = None  # (export_fn, import_fn)


class ExtensionAPI:
	"""The object passed to ``register(api)``. Everything an extension can do goes through it."""

	def __init__(self, manager: "ExtensionManager", registration: Registration):
		self._manager = manager
		self._reg = registration
		self.slug = registration.slug
		self.path = registration.path
		self.manifest = registration.manifest
		self.settings = _Settings(manager, registration.slug)
		self.core_version = CORE_VERSION

	def register_directory(
		self, key: str, *, label: str, singular: str, base_path: str, groups: List[Tuple[str, str]],
		public_groups: Optional[List[str]] = None, sections: Optional[List[str]] = None,
		defaults: Optional[Dict[str, Any]] = None, export_filename: Optional[str] = None,
	) -> None:
		if not re.match(r"^[a-z][a-z0-9_]{1,30}$", key):
			raise ExtensionError(f"Invalid directory key {key!r}.")
		base = "/" + base_path.strip("/") + "/"
		if not re.match(r"^/[a-z0-9][a-z0-9-]*/$", base) or base.strip("/") in CORE_PREFIXES:
			raise ExtensionError(f"Invalid base_path {base_path!r}.")
		if not groups:
			groups = [("active", label)]
		self._reg.directories[key] = {
			"label": label, "singular": singular, "base_path": base,
			"groups": [tuple(g) for g in groups],
			"public_groups": public_groups or [g[0] for g in groups[:1]],
			"sections": sections or ["profiles", "biography", "education", "research", "publications"],
			"defaults": dict(defaults or {}), "export_filename": export_filename or f"{key}.json",
			"extension": self.slug,
		}

	def register_section_preset(self, key: str, preset: Dict[str, Any]) -> None:
		self._reg.section_presets[key] = dict(preset, extension=self.slug)

	def route(self, rule: str, methods=("GET",)) -> Callable:
		first = rule.strip("/").split("/")[0]
		if first in CORE_PREFIXES or not rule.startswith("/"):
			raise ExtensionError(f"Public route {rule!r} is not allowed (reserved or not absolute).")

		def decorator(fn: Callable) -> Callable:
			self._reg.routes.append((Rule(rule, methods=list(methods), endpoint=f"{self.slug}:{fn.__name__}"), fn))
			return fn
		return decorator

	def admin_route(self, rule: str, capability: str = "manage_settings", methods=("GET",)) -> Callable:
		def decorator(fn: Callable) -> Callable:
			full = f"/admin/ext/{self.slug}/" + rule.strip("/")
			full = full.rstrip("/") if rule.strip("/") else full
			self._reg.routes.append((Rule(full, methods=list(methods), endpoint=f"{self.slug}:admin:{fn.__name__}"), _guard(fn, capability)))
			return fn
		return decorator

	def add_admin_menu(self, label: str, items: Any, capability: Optional[str] = None) -> None:
		self._reg.admin_menus.append({"label": label, "items": items, "capability": capability, "extension": self.slug})

	def add_dashboard_widget(self, fn: Callable) -> None:
		self.add_action("admin_dashboard", fn)

	def render(self, template: str, **context: Any) -> str:
		from flask import render_template
		return render_template(f"{self.slug}/{template}", **context)

	def static_url(self, path: str) -> str:
		return f"/ext/{self.slug}/static/{path.lstrip('/')}"

	def add_action(self, name: str, fn: Callable, priority: int = 10) -> None:
		self._reg.actions.setdefault(name, []).append((priority, fn))

	def add_filter(self, name: str, fn: Callable, priority: int = 10) -> None:
		self._reg.filters.setdefault(name, []).append((priority, fn))

	def add_template_global(self, name: str, value: Any) -> None:
		self._reg.template_globals[name] = value

	def on_activate(self, fn: Callable) -> None:
		self._reg.on_activate.append(fn)

	def on_deactivate(self, fn: Callable) -> None:
		self._reg.on_deactivate.append(fn)

	def register_demo_content(self, fn: Callable, label: str = "Install sample content", description: str = "") -> None:
		self._reg.demo = {"fn": fn, "label": label, "description": description}

	def register_export(self, export_fn: Callable, import_fn: Callable) -> None:
		"""Include the extension's own tables in site exports.

		export_fn() returns JSON-serialisable data. import_fn(data, overwrite) stores it again and
		returns how many records it imported. If the extension is not active when a package is
		imported, the data waits and is applied the next time it is activated.
		"""
		self._reg.exporter = (export_fn, import_fn)

	def activate_theme(self, name: str) -> bool:
		"""Make a theme (usually one this extension ships in themes/) the site's active theme."""
		return self._manager.switch_theme(name)

	def create_tables(self, base=None) -> None:
		"""Create tables for models the extension declared on the core's SQLAlchemy Base."""
		from data_store import Base, db_manager
		(base or Base).metadata.create_all(db_manager.engine)


def _guard(fn: Callable, capability: str) -> Callable:
	from auth import capability_required

	return capability_required(capability)(fn)


class _Settings:
	"""Per-extension settings stored in the site settings table."""

	def __init__(self, manager: "ExtensionManager", slug: str):
		self._m = manager
		self._key = f"ext_{slug.replace('-', '_')}"

	def all(self) -> Dict[str, Any]:
		value = self._m.read_setting(self._key, {})
		return value if isinstance(value, dict) else {}

	def get(self, key: str, default: Any = None) -> Any:
		return self.all().get(key, default)

	def set(self, key: str, value: Any) -> None:
		data = self.all()
		data[key] = value
		self._m.write_setting(self._key, data)

	def replace(self, data: Dict[str, Any]) -> None:
		self._m.write_setting(self._key, dict(data))


class ExtensionManager:
	"""Loads active extensions and merges what they registered into the running app."""

	def __init__(self) -> None:
		self.registrations: Dict[str, Registration] = {}
		self.errors: Dict[str, str] = {}
		self._lock = threading.RLock()
		self._url_map = Map([])
		self._route_views: Dict[str, Callable] = {}
		self._installed_globals: Dict[str, Any] = {}
		self.read_setting: Callable[[str, Any], Any] = lambda key, default=None: default
		self.write_setting: Callable[[str, Any], None] = lambda key, value: None
		self.on_changed: Callable[[], None] = lambda: None
		self.apply_directories: Callable[[Dict[str, Dict[str, Any]]], None] = lambda d: None
		self.jinja_env: Any = None
		self.switch_theme: Callable[[str], bool] = lambda name: False

	def export_extension_data(self) -> Dict[str, Any]:
		"""{slug: data} for every active extension that registered an exporter."""
		out: Dict[str, Any] = {}
		for slug, reg in self.registrations.items():
			if reg.exporter:
				try:
					out[slug] = reg.exporter[0]()
				except Exception as e:
					logger.error(f"[extensions] export of {slug} failed: {e}")
		return out

	def _pending_key(self, slug: str) -> str:
		return f"ext_pending_import_{slug.replace('-', '_')}"

	def import_extension_data(self, slug: str, data: Any, overwrite: bool = False) -> str:
		"""Apply exported data now if the extension is active, otherwise keep it until it is."""
		reg = self.registrations.get(slug)
		if reg is not None and reg.exporter:
			try:
				count = reg.exporter[1](data, overwrite)
				return f"{slug}: {count} record(s) imported."
			except Exception as e:
				logger.error(f"[extensions] import for {slug} failed: {e}")
				return f"{slug}: import failed ({e})."
		self.write_setting(self._pending_key(slug), {"data": data, "overwrite": overwrite})
		return f"{slug}: data saved; it is imported when that extension is activated."

	def _apply_pending_import(self, slug: str) -> None:
		pending = self.read_setting(self._pending_key(slug), None)
		reg = self.registrations.get(slug)
		if not isinstance(pending, dict) or reg is None or not reg.exporter:
			return
		try:
			reg.exporter[1](pending.get("data"), bool(pending.get("overwrite")))
			self.write_setting(self._pending_key(slug), "")
		except Exception as e:
			logger.error(f"[extensions] pending import for {slug} failed: {e}")

	def active_slugs(self) -> List[str]:
		value = self.read_setting("active_extensions", [])
		return [s for s in value if isinstance(s, str)] if isinstance(value, list) else []

	def set_active_slugs(self, slugs: List[str]) -> None:
		self.write_setting("active_extensions", sorted(set(slugs)))

	def is_active(self, slug: str) -> bool:
		return slug in self.registrations

	def sync(self, force: bool = False) -> bool:
		"""Make the loaded extensions match the saved list. Returns True if anything changed."""
		with self._lock:
			wanted = self.active_slugs()
			current = set(self.registrations)
			if not force and current == set(wanted):
				return False
			for slug in sorted(current - set(wanted)):
				self._unload(slug)
			for slug in wanted:
				if slug not in self.registrations:
					self._load(slug)
			self._rebuild()
			return True

	def _load(self, slug: str) -> None:
		folder = os.path.join(EXTENSIONS_DIR, slug)
		pkg = f"flexaflow_ext_{slug.replace('-', '_')}"
		try:
			manifest = read_manifest(folder)
			if manifest["slug"] != slug:
				raise ExtensionError("The folder name and the slug in extension.json differ.")
			reg = Registration(manifest, folder)
			self._install_themes(reg)
			entry = os.path.join(folder, manifest["entry"])
			if os.path.exists(entry):
				for name in [m for m in sys.modules if m == pkg or m.startswith(pkg + ".")]:
					del sys.modules[name]
				package = types.ModuleType(pkg)
				package.__path__ = [folder]
				package.__file__ = os.path.join(folder, "__init__.py")
				sys.modules[pkg] = package
				module = importlib.import_module(f"{pkg}.{manifest['entry'][:-3]}")
				register = getattr(module, "register", None)
				if not callable(register):
					raise ExtensionError(f"{manifest['entry']} has no register(api) function.")
				register(ExtensionAPI(self, reg))
			self.registrations[slug] = reg
			self.errors.pop(slug, None)
		except BaseException as e:
			if isinstance(e, (KeyboardInterrupt, SystemExit)):
				raise
			self.errors[slug] = str(e) if isinstance(e, ExtensionError) else f"{type(e).__name__}: {e}"
			logger.info(f"[extensions] could not load {slug}:\n{traceback.format_exc()}")
			self._remove_themes(slug)

	def _unload(self, slug: str) -> None:
		reg = self.registrations.pop(slug, None)
		if reg is not None:
			self._remove_themes(slug)

	def _install_themes(self, reg: Registration) -> None:
		source = os.path.join(reg.path, "themes")
		if not os.path.isdir(source):
			return
		for name in sorted(os.listdir(source)):
			src = os.path.join(source, name)
			if not os.path.isdir(src) or not re.match(r"^[a-zA-Z0-9_-]+$", name):
				continue
			dest = os.path.join(THEMES_DIR, name)
			marker = os.path.join(dest, THEME_MARKER)
			stamp = f"{reg.slug}@{reg.manifest['version']}"
			if os.path.isdir(dest):
				try:
					with open(marker, encoding="utf-8") as fh:
						owner = fh.read().strip()
				except OSError:
					raise ExtensionError(f"A theme named '{name}' already exists and does not belong to this extension.")
				if not owner.startswith(reg.slug + "@"):
					raise ExtensionError(f"The theme '{name}' belongs to another extension.")
				if owner == stamp:
					reg.themes.append(name)
					continue
				shutil.rmtree(dest)
			shutil.copytree(src, dest)
			with open(os.path.join(dest, THEME_MARKER), "w", encoding="utf-8") as fh:
				fh.write(stamp)
			reg.themes.append(name)

	def _remove_themes(self, slug: str) -> None:
		if not os.path.isdir(THEMES_DIR):
			return
		for name in os.listdir(THEMES_DIR):
			marker = os.path.join(THEMES_DIR, name, THEME_MARKER)
			try:
				with open(marker, encoding="utf-8") as fh:
					owner = fh.read().strip()
			except OSError:
				continue
			if owner.startswith(slug + "@"):
				shutil.rmtree(os.path.join(THEMES_DIR, name), ignore_errors=True)
				shutil.rmtree(os.path.join(STATIC_DIR, name), ignore_errors=True)

	def _rebuild(self) -> None:
		rules = []
		self._route_views = {}
		directories: Dict[str, Dict[str, Any]] = {}
		for reg in self.registrations.values():
			directories.update(reg.directories)
			for rule, view in reg.routes:
				rules.append(rule.empty())
				self._route_views[rule.endpoint] = view
		self._url_map = Map(rules, strict_slashes=False)
		self.apply_directories(directories)
		if self.jinja_env is not None:
			for name in list(self._installed_globals):
				self.jinja_env.globals.pop(name, None)
			self._installed_globals = {}
			for reg in self.registrations.values():
				for name, value in reg.template_globals.items():
					self.jinja_env.globals[name] = value
					self._installed_globals[name] = value
			self.jinja_env.cache.clear()
		self.on_changed()

	def section_presets(self) -> Dict[str, Dict[str, Any]]:
		out: Dict[str, Dict[str, Any]] = {}
		for reg in self.registrations.values():
			out.update(reg.section_presets)
		return out

	def admin_menus(self) -> List[Dict[str, Any]]:
		"""Menus for the current user (callables are evaluated now)."""
		from auth import can
		menus = []
		for reg in self.registrations.values():
			for menu in reg.admin_menus:
				if menu["capability"] and not can(menu["capability"]):
					continue
				items = menu["items"]
				try:
					items = items() if callable(items) else items
				except Exception as e:
					logger.error(f"[extensions] menu of {reg.slug} failed: {e}")
					items = []
				menus.append({"label": menu["label"], "items": list(items or []), "extension": reg.slug})
		return menus

	def template_dir(self, slug: str) -> Optional[str]:
		reg = self.registrations.get(slug)
		folder = os.path.join(reg.path, "templates") if reg else None
		return folder if folder and os.path.isdir(folder) else None

	def static_dir(self, slug: str) -> Optional[str]:
		reg = self.registrations.get(slug)
		folder = os.path.join(reg.path, "static") if reg else None
		return folder if folder and os.path.isdir(folder) else None

	def do_action(self, name: str, *args: Any, **kwargs: Any) -> Markup:
		"""Run every handler. Handlers that return text have it joined (as trusted HTML)."""
		chunks = []
		handlers = sorted(
			[(p, reg.slug, fn) for reg in self.registrations.values() for p, fn in reg.actions.get(name, [])],
			key=lambda t: t[0],
		)
		for _, slug, fn in handlers:
			try:
				out = fn(*args, **kwargs)
				if out:
					chunks.append(str(out))
			except Exception as e:
				logger.error(f"[extensions] action {name} of {slug} failed: {e}")
		return Markup("".join(chunks))

	def apply_filters(self, name: str, value: Any, *args: Any) -> Any:
		handlers = sorted(
			[(p, reg.slug, fn) for reg in self.registrations.values() for p, fn in reg.filters.get(name, [])],
			key=lambda t: t[0],
		)
		for _, slug, fn in handlers:
			try:
				value = fn(value, *args)
			except Exception as e:
				logger.error(f"[extensions] filter {name} of {slug} failed: {e}")
		return value

	def dispatch(self, path: str, method: str):
		"""Return (view, kwargs) if an extension route matches this request, else None."""
		if not self._route_views:
			return None
		adapter = self._url_map.bind("")
		try:
			endpoint, kwargs = adapter.match(path, method=method)
		except (NotFound, MethodNotAllowed):
			return None
		except Exception:
			return None
		return self._route_views[endpoint], kwargs

	def activate(self, slug: str) -> None:
		if not SLUG_RE.match(slug) or not os.path.isdir(os.path.join(EXTENSIONS_DIR, slug)):
			raise ExtensionError("That extension is not installed.")
		manifest = read_manifest(os.path.join(EXTENSIONS_DIR, slug))
		active = self.active_slugs()
		if slug not in active:
			active.append(slug)
		self.set_active_slugs(active)
		self.sync(force=slug in self.registrations)
		if slug not in self.registrations:
			message = self.errors.get(slug, "It could not be loaded.")
			self.set_active_slugs([s for s in active if s != slug])
			raise ExtensionError(message)
		reg = self.registrations[slug]
		api = ExtensionAPI(self, reg)
		for fn in reg.on_activate:
			fn(api)
		self._apply_pending_import(slug)
		self.on_changed()

	def deactivate(self, slug: str) -> None:
		reg = self.registrations.get(slug)
		if reg is not None:
			api = ExtensionAPI(self, reg)
			for fn in reg.on_deactivate:
				try:
					fn(api)
				except Exception as e:
					logger.error(f"[extensions] on_deactivate of {slug} failed: {e}")
		self.set_active_slugs([s for s in self.active_slugs() if s != slug])
		self.sync()

	def run_demo(self, slug: str, overwrite: bool = False) -> str:
		reg = self.registrations.get(slug)
		if not reg or not reg.demo:
			raise ExtensionError("This extension has no sample content.")
		result = reg.demo["fn"](ExtensionAPI(self, reg), overwrite=overwrite)
		self.on_changed()
		return str(result or "Sample content installed.")


manager = ExtensionManager()
