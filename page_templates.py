"""
Page templates with custom fields (WordPress "page templates" + ACF-style fields).

A theme can ship page templates under:

    themes/<theme>/page_templates/<key>.json     the field schema
    themes/<theme>/templates/page_templates/<key>.html   the Jinja template

The JSON schema looks like:

    {
      "label": "Department landing page",
      "description": "Home page for the department",
      "fields": [
        {"name": "hero_title", "label": "Hero title", "type": "text", "default": "..."},
        {"name": "programs", "label": "Programs", "type": "repeater",
         "fields": [{"name": "title", "type": "text"},
                    {"name": "url", "type": "url"}]}
      ]
    }

Supported field types: text, textarea, richtext, image, file (an uploaded
document such as a PDF), url, email, number, date, datetime,
select (needs "options": [{"value": ..., "label": ...}]) and repeater (needs
"fields": a list of simple fields; repeaters cannot nest).

Values are stored on the page as JSON, keyed by field name, and reach the
template as ``f`` (for example ``{{ f.hero_title }}`` or ``{% for p in f.programs %}``).
"""

import json
import os
import re
from typing import Any, Dict, List

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
THEMES_DIR = os.path.join(BASE_DIR, "themes")

SIMPLE_TYPES = {"text", "textarea", "richtext", "image", "file", "url", "email", "number", "select", "date", "datetime"}
FIELD_TYPES = SIMPLE_TYPES | {"repeater"}
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_LENGTH = {"text": 500, "textarea": 5000, "richtext": 200000, "url": 1000,
              "email": 254, "number": 32, "select": 100, "image": 1000, "file": 1000,
              "date": 10, "datetime": 16}
MAX_REPEATER_ROWS = 200

DEFAULT_TEMPLATE_KEY = "default"


def _validate_simple(field: Dict[str, Any], where: str) -> Dict[str, Any]:
	name = field.get("name", "")
	if not NAME_RE.match(name):
		raise ValueError(f"{where}: invalid field name {name!r} (use lowercase letters, digits, underscores)")
	ftype = field.get("type", "text")
	if ftype not in SIMPLE_TYPES:
		raise ValueError(f"{where}: field {name!r} has unsupported type {ftype!r}")
	normalized = {
		"name": name,
		"label": field.get("label") or name.replace("_", " ").capitalize(),
		"type": ftype,
		"help": field.get("help", ""),
		"default": field.get("default", ""),
	}
	if ftype == "select":
		options = field.get("options") or []
		normalized["options"] = [
			{"value": str(o.get("value", "")), "label": o.get("label") or str(o.get("value", ""))}
			for o in options
		]
	return normalized


def _validate_schema(schema: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
	fields = []
	seen = set()
	for raw in schema.get("fields", []):
		if raw.get("type") == "repeater":
			name = raw.get("name", "")
			if not NAME_RE.match(name):
				raise ValueError(f"{key}: invalid repeater name {name!r}")
			subs = [_validate_simple(s, f"{key}.{name}") for s in raw.get("fields", [])]
			if not subs:
				raise ValueError(f"{key}: repeater {name!r} has no sub-fields")
			field = {
				"name": name,
				"label": raw.get("label") or name.replace("_", " ").capitalize(),
				"type": "repeater",
				"help": raw.get("help", ""),
				"fields": subs,
				"default": raw.get("default", []),
			}
		else:
			field = _validate_simple(raw, key)
		if field["name"] in seen:
			raise ValueError(f"{key}: duplicate field {field['name']!r}")
		seen.add(field["name"])
		fields.append(field)
	return fields


def load_page_templates(theme_name: str) -> Dict[str, Dict[str, Any]]:
	"""
	All page templates available to the active theme, keyed by template key.
	The built-in "default" template (plain content) is always present.
	"""
	templates = {
		DEFAULT_TEMPLATE_KEY: {
			"key": DEFAULT_TEMPLATE_KEY,
			"label": "Default page",
			"description": "Title and rich-text content.",
			"fields": [],
			"template_file": "main/master.html",
		}
	}
	folder = os.path.join(THEMES_DIR, theme_name, "page_templates")
	if not os.path.isdir(folder):
		return templates
	for filename in sorted(os.listdir(folder)):
		if not filename.endswith(".json"):
			continue
		key = filename[:-5]
		try:
			with open(os.path.join(folder, filename), encoding="utf-8") as fh:
				schema = json.load(fh)
			if not NAME_RE.match(key):
				raise ValueError(f"invalid template key {key!r}")
			templates[key] = {
				"key": key,
				"label": schema.get("label") or key,
				"description": schema.get("description", ""),
				"fields": _validate_schema(schema, key),
				"template_file": f"page_templates/{key}.html",
			}
		except (OSError, ValueError, json.JSONDecodeError) as e:
			print(f"Skipping page template '{key}': {e}")
	return templates


def default_values(fields: List[Dict[str, Any]]) -> Dict[str, Any]:
	values: Dict[str, Any] = {}
	for field in fields:
		if field["type"] == "repeater":
			values[field["name"]] = list(field.get("default") or [])
		else:
			values[field["name"]] = field.get("default", "")
	return values


def fill_defaults(stored: Dict[str, Any], fields: List[Dict[str, Any]]) -> Dict[str, Any]:
	"""Stored values merged over the template defaults (for display)."""
	values = default_values(fields)
	for key, value in (stored or {}).items():
		if key in values:
			values[key] = value
	return values


def _clean_simple(field: Dict[str, Any], raw: str) -> str:
	ftype = field["type"]
	value = (raw or "").strip() if ftype != "richtext" else (raw or "").strip()
	value = value[: MAX_LENGTH.get(ftype, 500)]
	if ftype == "url":
		if value and not re.match(r"^(https?://|mailto:|tel:|/|#)", value):
			return ""
	elif ftype == "email":
		if value and not EMAIL_RE.match(value):
			return ""
	elif ftype == "image":
		if value and not re.match(r"^(/uploads/|/static/|https?://)", value):
			return ""
	elif ftype == "file":
		if value and not re.match(r"^(/uploads/|/static/|https?://)", value):
			return ""
	elif ftype == "date":
		if value and not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
			return ""
	elif ftype == "datetime":
		if value and not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", value):
			return ""
	elif ftype == "number":
		if value and not re.match(r"^-?\d+(\.\d+)?$", value):
			return ""
	elif ftype == "select":
		allowed = {o["value"] for o in field.get("options", [])}
		if value not in allowed:
			return ""
	return value


def parse_fields(fields: List[Dict[str, Any]], form) -> Dict[str, Any]:
	"""
	Read the custom-field inputs from a submitted form.

	Inputs are named ``field[<name>]`` for simple fields and
	``field[<name>][<row>][<sub>]`` for repeater rows. Rows keep the order in
	which they appear in the form; empty rows are dropped.
	"""
	values: Dict[str, Any] = {}
	for field in fields:
		name = field["name"]
		if field["type"] != "repeater":
			values[name] = _clean_simple(field, form.get(f"field[{name}]", ""))
			continue

		pattern = re.compile(r"^field\[" + re.escape(name) + r"\]\[([^\]]+)\]\[([a-z][a-z0-9_]*)\]$")
		rows: Dict[str, Dict[str, str]] = {}
		for form_key in form.keys():
			match = pattern.match(form_key)
			if not match:
				continue
			row_id, sub = match.group(1), match.group(2)
			rows.setdefault(row_id, {})[sub] = form.get(form_key, "")

		cleaned = []
		for row in rows.values():
			item = {}
			for sub in field["fields"]:
				item[sub["name"]] = _clean_simple(sub, row.get(sub["name"], ""))
			if any(item.values()):
				cleaned.append(item)
		values[name] = cleaned[:MAX_REPEATER_ROWS]
	return values


def dump_fields(values: Dict[str, Any]) -> str:
	return json.dumps(values, ensure_ascii=False)
