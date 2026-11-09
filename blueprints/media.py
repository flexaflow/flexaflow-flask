"""Media library, image upload and serving of uploaded files."""
import sys as _sys

from flask import Blueprint

bp = Blueprint("media", __name__)

# Names defined in app.py (helpers, stores, settings) are shared with this module.
# This file is imported at the end of app.py, once all of them exist.
_core = _sys.modules["app"]
globals().update({k: v for k, v in vars(_core).items() if not k.startswith("__") and k not in ("bp",)})


@bp.route("/admin/media")
@capability_required("upload_media")
def media_library():
	"""Displays and manages the media library.  Features: - Paged display of media items - Search functionality - JSON API support for AJAX requests - Displays image metadata - Shows thumbnails and image sizes  Query Parameters: page (int): Page number for pagination (default: 1) search (str): Optional..."""
	page = max(1, int(request.args.get("page", 1)))
	search = request.args.get("search")
	media = db_manager.get_media_library(page=page, search=search) or {
		"items": [], "total": 0, "page": 1, "total_pages": 1, "per_page": 10,
	}

	if request.args.get("format") == "json":
		return jsonify(media)

	return render_template("media-library.html", media=media)


@bp.route("/admin/media/upload", methods=["POST"])
@capability_required("upload_media")
def upload_media():
	"""Handles bulk media file uploads for the media library.  Features: - Multiple file upload support - Automatic thumbnail generation - Metadata extraction - Database integration - Error handling per file  Request: files[]: Array of file uploads  Processing: 1. Validates each file 2. Generates..."""
	if "files[]" not in request.files:
		return jsonify({"success": False, "error": "No file part"})

	files = request.files.getlist("files[]")
	results = []

	for file in files:
		if file.filename == "":
			continue

		if file:
			try:
				original_filename = secure_filename(file.filename)
				filename = str(uuid.uuid4()) + os.path.splitext(original_filename)[1]
				file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)

				file.save(file_path)

				thumbnails = create_image_thumbnails(file_path, filename)

				with Image.open(file_path) as img:
					width, height = img.size

				file_size = os.path.getsize(file_path)

				media_data = {
					"filename": filename,
					"original_filename": original_filename,
					"mime_type": file.content_type,
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
					result["thumbnails"] = thumbnails
					results.append({"success": True, "file": result})
				else:
					results.append(
						{
							"success": False,
							"error": f"Failed to save {original_filename} to database",
						}
					)

			except Exception as e:
				results.append(
					{
						"success": False,
						"error": f"Error processing {file.filename}: {str(e)}",
					}
				)

	return jsonify({"success": True, "files": results})


@bp.route("/admin/media/<int:media_id>", methods=["GET", "PUT", "DELETE"])
@capability_required("manage_media")
def manage_media(media_id):
	"""RESTful endpoint for managing individual media items.  Features: - Media item retrieval - Metadata updates - Media deletion - Thumbnail management - File system synchronization  Methods: GET: Retrieve media item details PUT: Update media metadata DELETE: Remove media item  URL Parameters:..."""
	if request.method == "GET":
		media = db_manager.get_media_by_id(media_id)
		if media:
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
			filename = str(uuid.uuid4()) + os.path.splitext(original_filename)[1]
			file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)

			os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

			file.save(file_path)

			try:
				thumbnails = create_image_thumbnails(file_path, filename)

				with Image.open(file_path) as img:
					width, height = img.size

				file_size = os.path.getsize(file_path)

				media_data = {
					"filename": filename,
					"original_filename": original_filename,
					"mime_type": file.content_type,
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

				return jsonify({"error": "Failed to save to database"}), 500

			except Exception as e:
				try:
					os.remove(file_path)
				except:
					pass
				raise e

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
	return send_file(requested_path, conditional=True, max_age=60 * 60 * 24 * 30)
