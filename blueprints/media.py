"""Media library, image upload and serving of uploaded files."""
import mimetypes
import sys as _sys

from flask import Blueprint

bp = Blueprint("media", __name__)

# Names defined in app.py (helpers, stores, settings) are shared with this module.
# This file is imported at the end of app.py, once all of them exist.
_core = _sys.modules["app"]
globals().update({k: v for k, v in vars(_core).items() if not k.startswith("__") and k not in ("bp",)})


def _discard_upload(filename):
	"""Remove a just-stored file and its thumbnails after a failure, so nothing is left in the upload folder."""
	if not filename:
		return
	folder = app.config["UPLOAD_FOLDER"]
	name = os.path.splitext(filename)[0]
	paths = [os.path.join(folder, filename)]
	thumbs = os.path.join(folder, "thumbnails")
	if os.path.isdir(thumbs):
		paths += [os.path.join(thumbs, f) for f in os.listdir(thumbs) if f.startswith(name + "-")]
	for path in paths:
		try:
			os.remove(path)
		except OSError:
			pass


@bp.route("/admin/media")
@capability_required("upload_media")
def media_library():
	"""Displays and manages the media library.  Features: - Paged display of media items - Search functionality - JSON API support for AJAX requests - Displays image metadata - Shows thumbnails and image sizes  Query Parameters: page (int): Page number for pagination (default: 1) search (str): Optional..."""
	page = max(1, int(request.args.get("page", 1)))
	search = request.args.get("search")
	# group=<id> shows one group, group=none shows images that are in no group, anything else shows all.
	group_arg = (request.args.get("group") or "").strip()
	group_id = int(group_arg) if group_arg.isdigit() else None
	ungrouped = group_arg == "none"
	media = db_manager.get_media_library(page=page, search=search, group_id=group_id, ungrouped=ungrouped) or {
		"items": [], "total": 0, "page": 1, "total_pages": 1, "per_page": 10,
	}

	if request.args.get("format") == "json":
		return jsonify(media)

	return render_template("media-library.html", media=media, group_filter=group_arg, groups=db_manager.list_media_groups())


@bp.route("/admin/media/upload", methods=["POST"])
@capability_required("upload_media")
def upload_media():
	"""Handles bulk media file uploads for the media library.  Features: - Multiple file upload support - Automatic thumbnail generation - Metadata extraction - Database integration - Error handling per file  Request: files[]: Array of file uploads  Processing: 1. Validates each file 2. Generates..."""
	if "files[]" not in request.files:
		return jsonify({"success": False, "error": "No file part"})

	# Optional: put every image in this upload into an existing group (checked before anything is stored).
	group_id = None
	raw_group = (request.form.get("group_id") or "").strip()
	if raw_group:
		if not raw_group.isdigit() or not db_manager.get_media_group(int(raw_group)):
			return jsonify({"success": False, "error": "That image group no longer exists. Reload the page and try again."}), 400
		group_id = int(raw_group)

	files = request.files.getlist("files[]")
	results = []

	for file in files:
		if file.filename == "":
			continue

		if file:
			original_filename = secure_filename(file.filename)
			if not allowed_file(original_filename):
				results.append({"success": False, "error": f"{original_filename or 'File'}: only PNG, JPG, GIF and WEBP images are allowed"})
				continue
			filename = None
			try:
				# Verified and re-encoded by Pillow; extension and MIME type come from the decoded format, not the client.
				filename, mime_type, width, height = images.store_verified_image(file.stream, app.config["UPLOAD_FOLDER"], str(uuid.uuid4()))
				file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
				os.makedirs(os.path.join(app.config["UPLOAD_FOLDER"], "thumbnails"), exist_ok=True)
				thumbnails = create_image_thumbnails(file_path, filename)
				file_size = os.path.getsize(file_path)

				media_data = {
					"filename": filename,
					"original_filename": original_filename,
					"mime_type": mime_type,
					"file_size": file_size,
					"width": width,
					"height": height,
					"alt_text": os.path.splitext(original_filename)[0],
					"caption": "",
					"title": os.path.splitext(original_filename)[0],
					"description": "",
					"thumbnail": thumbnails.get("thumbnail"),
				}

				result = db_manager.add_media(media_data)
				if result:
					if group_id:
						db_manager.add_media_to_group(group_id, [result["id"]])
					result["thumbnails"] = thumbnails
					results.append({"success": True, "file": result})
				else:
					_discard_upload(filename)
					results.append({"success": False, "error": f"Failed to save {original_filename} to database"})

			except images.UnsafeImage as e:
				_discard_upload(filename)
				results.append({"success": False, "error": f"{original_filename}: {e}"})
			except Exception:
				_discard_upload(filename)
				app.logger.exception("Media upload error")
				results.append({"success": False, "error": f"Error processing {original_filename}"})

	return jsonify({"success": True, "files": results})


@bp.route("/admin/media/<int:media_id>", methods=["GET", "PUT", "DELETE"])
@capability_required("manage_media")
def manage_media(media_id):
	"""RESTful endpoint for managing individual media items.  Features: - Media item retrieval - Metadata updates - Media deletion - Thumbnail management - File system synchronization  Methods: GET: Retrieve media item details PUT: Update media metadata DELETE: Remove media item  URL Parameters:..."""
	if request.method == "GET":
		media = db_manager.get_media_by_id(media_id)
		if media:
			media["groups"] = db_manager.get_groups_for_media(media_id)
			return jsonify(media)
		return jsonify({"error": "Media not found"}), 404

	elif request.method == "PUT":
		data = request.json
		success = db_manager.update_media(media_id, data)
		if success:
			return jsonify({"success": True})
		return jsonify({"success": False, "error": "Update failed"}), 500

	elif request.method == "DELETE":
		success = db_manager.delete_media(media_id)
		if success:
			return jsonify({"success": True})
		return jsonify({"success": False, "error": "Delete failed"}), 500


# ---- image groups ---------------------------------------------------------------------------------------------
# A group is a named collection (e.g. an event). Removing images from a group never deletes the images;
# deleting a group only removes its memberships.

def _json_or_form():
	return request.get_json(silent=True) or request.form


def _media_ids_from_request():
	"""Parse media_ids from a JSON body or form; returns a list of ints, or None if malformed."""
	data = _json_or_form()
	raw = data.get("media_ids") if hasattr(data, "get") else None
	if isinstance(raw, str):
		raw = raw.split(",")
	if not isinstance(raw, (list, tuple)) or not raw:
		return None
	try:
		return sorted({int(i) for i in raw})
	except (TypeError, ValueError):
		return None


@bp.route("/admin/media/groups", methods=["GET"])
@capability_required("upload_media")
def mg_list():
	"""All image groups with their image counts (used by the library and the editor picker)."""
	return jsonify({"groups": db_manager.list_media_groups()})


@bp.route("/admin/media/groups", methods=["POST"])
@capability_required("upload_media")
def mg_create():
	data = _json_or_form()
	try:
		group = db_manager.create_media_group(data.get("name", ""), data.get("description", ""))
	except ValueError as e:
		return jsonify({"success": False, "error": str(e)}), 400
	return jsonify({"success": True, "group": group})


@bp.route("/admin/media/groups/<int:group_id>", methods=["PUT"])
@capability_required("manage_media")
def mg_update(group_id):
	data = _json_or_form()
	try:
		found = db_manager.update_media_group(group_id, data.get("name", ""), data.get("description"))
	except ValueError as e:
		return jsonify({"success": False, "error": str(e)}), 400
	if not found:
		return jsonify({"success": False, "error": "Group not found"}), 404
	return jsonify({"success": True})


@bp.route("/admin/media/groups/<int:group_id>", methods=["DELETE"])
@capability_required("manage_media")
def mg_delete(group_id):
	# Only the group is removed; its images stay in the library.
	if not db_manager.delete_media_group(group_id):
		return jsonify({"success": False, "error": "Group not found or could not be deleted"}), 404
	return jsonify({"success": True})


@bp.route("/admin/media/groups/<int:group_id>/add", methods=["POST"])
@capability_required("upload_media")
def mg_add(group_id):
	media_ids = _media_ids_from_request()
	if media_ids is None:
		return jsonify({"success": False, "error": "Choose at least one image."}), 400
	added = db_manager.add_media_to_group(group_id, media_ids)
	if added is None:
		return jsonify({"success": False, "error": "Group not found"}), 404
	return jsonify({"success": True, "added": added})


@bp.route("/admin/media/groups/<int:group_id>/remove", methods=["POST"])
@capability_required("manage_media")
def mg_remove(group_id):
	media_ids = _media_ids_from_request()
	if media_ids is None:
		return jsonify({"success": False, "error": "Choose at least one image."}), 400
	removed = db_manager.remove_media_from_group(group_id, media_ids)
	if removed is None:
		return jsonify({"success": False, "error": "Could not remove the images from the group."}), 500
	return jsonify({"success": True, "removed": removed})


@bp.route("/upload_image", methods=["POST"])
@capability_required("upload_media")
def upload_image():
	"""Handles image upload for the media library.  Features: - Validates CSRF token - Handles file upload securely - Creates multiple image thumbnails - Stores image metadata in database - Supports multiple file types (png, jpg, jpeg, gif, webp)  Returns: JSON: Response containing upload status and..."""

	try:
		token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
		if not token or token != session.get("csrf_token"):
			return jsonify({"error": "Invalid CSRF token"}), 403

		if "file" not in request.files:
			return jsonify({"error": "No file part"}), 400

		file = request.files["file"]
		if file.filename == "":
			return jsonify({"error": "No selected file"}), 400

		if file and allowed_file(file.filename):
			original_filename = secure_filename(file.filename)
			filename = None
			try:
				filename, mime_type, width, height = images.store_verified_image(file.stream, app.config["UPLOAD_FOLDER"], str(uuid.uuid4()))
				file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
				os.makedirs(os.path.join(app.config["UPLOAD_FOLDER"], "thumbnails"), exist_ok=True)
				thumbnails = create_image_thumbnails(file_path, filename)
				file_size = os.path.getsize(file_path)

				media_data = {
					"filename": filename,
					"original_filename": original_filename,
					"mime_type": mime_type,
					"file_size": file_size,
					"width": width,
					"height": height,
					"alt_text": os.path.splitext(original_filename)[0],
					"caption": "",
					"title": os.path.splitext(original_filename)[0],
					"description": "",
					"thumbnail": thumbnails.get("thumbnail"),
				}

				result = db_manager.add_media(media_data)
				if result:
					return jsonify(
						{"location": f"/uploads/{filename}", "success": True}
					)

				_discard_upload(filename)
				return jsonify({"error": "Failed to save to database"}), 500

			except images.UnsafeImage:
				_discard_upload(filename)
				return jsonify({"error": "The file is not a valid image"}), 400
			except Exception:
				_discard_upload(filename)
				raise

		return jsonify({"error": "Invalid file type"}), 400

	except Exception as e:
		app.logger.exception("Upload error")
		return jsonify({"error": "Upload failed"}), 500


@bp.route("/uploads/<path:filename>")
def uploaded_file(filename):
	"""Serves uploaded media files securely.  Features: - Secure file serving - Path traversal prevention - Proper MIME type handling - 404 handling for missing files - Optional access control  Security Measures: - Validates file paths - Prevents directory traversal - Confines to upload directory -..."""

	upload_folder = os.path.abspath(app.config["UPLOAD_FOLDER"])
	requested_path = os.path.abspath(os.path.join(upload_folder, filename))

	if not requested_path.startswith(upload_folder + os.sep):
		abort(403, "Forbidden: Path traversal detected.")

	if not os.path.isfile(requested_path):
		abort(404)

	# Uploads get a random name when stored, so they can be cached for a long time.
	SAFE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf", "text/plain")
	guessed = (mimetypes.guess_type(requested_path)[0] or "").lower()
	safe = guessed in SAFE_TYPES
	# Anything else (html, svg, xml, js...) is never served with its own type: octet-stream plus a forced download.
	response = send_file(requested_path, mimetype=guessed if safe else "application/octet-stream", conditional=True, max_age=60 * 60 * 24 * 30)
	# Defence in depth: even if a non-image slipped in, it can never run script on the site origin.
	response.headers["X-Content-Type-Options"] = "nosniff"
	if guessed != "application/pdf":  # a sandboxing CSP stops browsers' built-in PDF viewers
		response.headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'none'; sandbox"
	if not safe or guessed == "text/plain":
		response.headers["Content-Disposition"] = "attachment; filename=" + secure_filename(os.path.basename(requested_path))
	return response
