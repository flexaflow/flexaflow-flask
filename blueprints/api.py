"""JSON endpoints used by the admin screens."""
import sys as _sys

from flask import Blueprint

bp = Blueprint("api", __name__)

# Names defined in app.py (helpers, stores, settings) are shared with this module.
# This file is imported at the end of app.py, once all of them exist.
_core = _sys.modules["app"]
globals().update({k: v for k, v in vars(_core).items() if not k.startswith("__") and k not in ("bp",)})


@bp.route("/api/settings", methods=["GET", "POST"])
@capability_required("manage_settings")
def api_settings():
	"""RESTful API endpoint for managing site settings.  Features: - Retrieves current settings - Updates site configuration - Handles theme settings - Manages site metadata - Validates configuration  Methods: GET: Retrieve all site settings POST: Update site settings  Request (POST): JSON payload with..."""
	if request.method == "GET":
		return jsonify(get_site_settings())
	elif request.method == "POST":
		data = request.json or {}
		if "theme" in data and data["theme"] not in available_themes():
			return jsonify({"status": "error", "message": "Unknown theme"}), 400
		success = update_site_settings(data)
		if success and "theme" in data and data["theme"] != _core.THEME_NAME:
			apply_theme(data["theme"])
		if success:
			return jsonify({"status": "success", "message": "Settings updated"})
		else:
			return (
				jsonify({"status": "error", "message": "Failed to update settings"}),
				500,
			)


@bp.route('/api/check_slug')
@login_required
def api_check_slug():
	"""Check if a slug is available for posts or pages and return a suggested slug if taken."""
	slug = request.args.get('slug', '').strip()
	obj_type = request.args.get('type', 'post')
	if not slug:
		return jsonify({'unique': False, 'suggested_slug': ''})

	def exists_in(collection):
		if not isinstance(collection, dict):
			return False
		return slug in collection

	taken = False
	if obj_type == 'post':
		taken = exists_in(get_posts()) or exists_in(get_draft_posts())
	else:
		taken = exists_in(get_pages()) or exists_in(get_draft_pages())

	suggested = slug
	if taken:
		base = re.sub(r"[^a-z0-9-]", '', slug.lower())
		suffix = 2
		while True:
			candidate = f"{base}-{suffix}"
			if obj_type == 'post':
				if not (candidate in get_posts() or candidate in get_draft_posts()):
					suggested = candidate
					break
			else:
				if not (candidate in get_pages() or candidate in get_draft_pages()):
					suggested = candidate
					break
			suffix += 1

	return jsonify({'unique': not taken, 'suggested_slug': suggested})


@bp.route("/api/menu", methods=["GET", "POST"])
@capability_required("manage_settings")
def api_menu():
	"""RESTful API endpoint for managing site navigation menu.  Features: - Retrieves current menu structure - Updates menu organization - Handles nested menu items - Maintains menu state - Validates menu structure  Methods: GET: Retrieve current menu configuration POST: Update menu structure  Returns:..."""
	if request.method == "GET":
		settings = get_site_settings()
		return jsonify(settings.get("menu_items", []))
	elif request.method == "POST":
		data = _clean_menu(request.json)
		settings = get_site_settings()
		settings["menu_items"] = data
		success = update_site_settings(settings)
		if success:
			return jsonify({"status": "success", "message": "Menu updated"})
		else:
			return jsonify({"status": "error", "message": "Failed to update menu"}), 500
