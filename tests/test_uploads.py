"""Regression tests for the stored-XSS upload bug: no non-image may be stored in or served from /uploads."""
import io
import os

import pytest
from PIL import Image

from app import app as flask_app
from flexaflow_fixtures import token_of

ROLES_WITH_UPLOAD = ["contributor", "author", "editor"]
HTML = b"<html><script>alert(document.domain)</script></html>"


def _png(extra=b""):
	buf = io.BytesIO()
	Image.new("RGB", (8, 8), "red").save(buf, "PNG")
	return buf.getvalue() + extra


def _jpeg_with_gps():
	exif = Image.Exif()
	exif[0x8825] = {1: "N", 2: (23.0, 45.0, 0.0)}  # GPSInfo
	buf = io.BytesIO()
	Image.new("RGB", (8, 8), "blue").save(buf, "JPEG", exif=exif)
	return buf.getvalue()


def _files_on_disk():
	root = flask_app.config["UPLOAD_FOLDER"]
	return {os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs}


def _post(c, name, data, endpoint="/admin/media/upload"):
	field = "files[]" if endpoint == "/admin/media/upload" else "file"
	return c.post(endpoint, data={field: (io.BytesIO(data), name)}, headers={"X-CSRF-Token": token_of(c)}, content_type="multipart/form-data")


@pytest.mark.parametrize("role", ROLES_WITH_UPLOAD)
@pytest.mark.parametrize("endpoint", ["/admin/media/upload", "/upload_image"])
def test_html_upload_is_rejected_and_not_kept(login_as, role, endpoint):
	c = login_as(role)
	before = _files_on_disk()
	for name, data in (("evil.html", HTML), ("evil.png", HTML), ("evil.html", _png(HTML)), ("evil.svg", b"<svg onload=alert(1)/>")):
		_post(c, name, data, endpoint)
	assert _files_on_disk() == before


@pytest.mark.parametrize("role", ROLES_WITH_UPLOAD)
def test_png_renamed_html_is_refused(login_as, role):
	c = login_as(role)
	before = _files_on_disk()
	r = _post(c, "pic.html", _png())
	assert r.get_json()["files"][0]["success"] is False
	assert _files_on_disk() == before


def test_valid_image_stored_with_verified_extension_and_no_trailing_bytes(login_as):
	c = login_as("contributor")
	before = _files_on_disk()
	# A real PNG with an HTML payload appended, uploaded under a misleading .jpg name.
	r = _post(c, "photo.jpg", _png(HTML))
	assert r.get_json()["files"][0]["success"] is True
	new = [p for p in _files_on_disk() - before if os.sep + "thumbnails" + os.sep not in p]
	assert len(new) == 1 and new[0].endswith(".png")  # extension from decoded format, not the client name
	assert b"<script" not in open(new[0], "rb").read()


def test_exif_gps_is_stripped(login_as):
	c = login_as("author")
	before = _files_on_disk()
	_post(c, "geo.jpg", _jpeg_with_gps(), "/upload_image")
	new = [p for p in _files_on_disk() - before if p.endswith(".jpg")]
	assert new
	for path in new:
		with Image.open(path) as im:
			assert not im.getexif().get(0x8825)


def test_uploaded_files_are_never_served_as_html(login_as, client):
	c = login_as("editor")
	root = flask_app.config["UPLOAD_FOLDER"]
	os.makedirs(root, exist_ok=True)
	with open(os.path.join(root, "legacy.html"), "wb") as fh:  # e.g. left behind by the old bug
		fh.write(HTML)
	r = client.get("/uploads/legacy.html")
	assert "text/html" not in r.headers["Content-Type"]
	assert "attachment" in r.headers.get("Content-Disposition", "")
	assert r.headers["X-Content-Type-Options"] == "nosniff"
	assert "default-src 'none'" in r.headers["Content-Security-Policy"]
	os.remove(os.path.join(root, "legacy.html"))


def test_document_upload_verifies_images(login_as):
	c = login_as("contributor")
	r = _post(c, "fake.png", HTML, "/admin/upload_file")
	assert r.status_code == 400
	assert not [p for p in _files_on_disk() if "fake" in p]


def test_extension_upload_off_by_default(login_as, monkeypatch):
	monkeypatch.delenv("FLEXAFLOW_ALLOW_EXTENSION_UPLOAD", raising=False)
	c = login_as("administrator")
	r = c.get("/admin/extensions")
	assert b'type="file"' not in r.data
