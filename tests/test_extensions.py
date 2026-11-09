import json
import os
import shutil

import pytest

import extension_manager as em
from app import extensions as manager
from flexaflow_fixtures import token_of
from data_store import db_manager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRIB = os.path.join(ROOT, "contrib", "extensions")


def install(slug):
	em.install_dir(os.path.join(CONTRIB, slug))
	manager.activate(slug)


@pytest.fixture()
def newsletter():
	install("newsletter")
	yield
	manager.deactivate("newsletter")


def test_manifests_are_valid():
	for slug in os.listdir(CONTRIB):
		manifest = em.read_manifest(os.path.join(CONTRIB, slug))
		assert manifest["slug"] == slug and manifest["version"]


def test_newsletter_signup_and_admin(app, login_as, newsletter):
	anon = app.test_client()
	anon.get("/newsletter/")
	with anon.session_transaction() as s:
		tok = s["csrf_token"]
	r = anon.post("/newsletter/", data={"csrf_token": tok, "email": "Reader@Example.com"})
	assert r.status_code == 302
	anon.post("/newsletter/", data={"csrf_token": tok, "email": "reader@example.com"})  # duplicate is ignored
	editor = login_as("editor")
	page = editor.get("/admin/ext/newsletter/")
	assert page.status_code == 200 and page.data.count(b"reader@example.com") == 1
	csv_data = editor.get("/admin/ext/newsletter/export.csv").data
	assert b"reader@example.com" in csv_data
	assert login_as("author").get("/admin/ext/newsletter/").status_code == 403


def test_newsletter_rejects_bad_email_and_honeypot(app, newsletter):
	c = app.test_client()
	c.get("/newsletter/")
	with c.session_transaction() as s:
		tok = s["csrf_token"]
	bad = c.post("/newsletter/", data={"csrf_token": tok, "email": "nope"})
	assert bad.status_code == 200 and b"valid email" in bad.data
	c.post("/newsletter/", data={"csrf_token": tok, "email": "bot@example.com", "website": "x"})
	editor_rows = manager.export_extension_data()["newsletter"]
	assert all(r["email"] != "bot@example.com" for r in editor_rows)


def test_extension_data_round_trip_through_site_package(app, newsletter):
	import site_package as sp
	from sqlalchemy import text
	with db_manager.engine.begin() as conn:
		conn.execute(text("INSERT OR IGNORE INTO ext_newsletter_subscribers (email) VALUES ('round@example.com')"))
	exported = manager.export_extension_data()
	assert any(r["email"] == "round@example.com" for r in exported["newsletter"])
	data = sp.export_package("t", ["newsletter"], "default", content=False, settings=False, extension_data=True,
	                         extension_exports=exported)
	with db_manager.engine.begin() as conn:
		conn.execute(text("DELETE FROM ext_newsletter_subscribers WHERE email='round@example.com'"))
	import io
	lines = sp.import_package(io.BytesIO(data), content=False, settings=False, extension_data=True, media=False,
	                          extensions=False, themes=False, extension_importer=manager.import_extension_data)
	assert any("newsletter" in line and "imported" in line for line in lines), lines
	assert any(r["email"] == "round@example.com" for r in manager.export_extension_data()["newsletter"])


def test_data_waits_for_inactive_extension(app):
	manager.import_extension_data("newsletter", [{"email": "later@example.com"}])
	install("newsletter")  # activation applies the pending data
	try:
		assert any(r["email"] == "later@example.com" for r in manager.export_extension_data()["newsletter"])
	finally:
		manager.deactivate("newsletter")


def test_seo_audit_flags_problems(app, login_as):
	c = login_as("editor")
	c.post("/post/add", data={"csrf_token": token_of(c), "title": "Hi", "slug": "audit-me", "status": "published",
	                          "content": '<p>text</p><img src="/uploads/a.png">'})
	install("seo-audit")
	try:
		html = c.get("/admin/ext/seo-audit/").data
		assert b"audit-me" in html or b"Hi" in html
		assert b"without alt text" in html and b"No meta description" in html or b"short" in html
	finally:
		manager.deactivate("seo-audit")


def test_backups_create_and_download(app, login_as, tmp_path):
	install("backups")
	try:
		a = login_as("administrator")
		r = a.post("/admin/ext/backups/create", data={"csrf_token": token_of(a)})
		assert r.status_code == 302
		page = a.get("/admin/ext/backups/").data.decode()
		import re
		name = re.search(r"backup-\d{8}-\d{6}\.zip", page).group(0)
		got = a.get(f"/admin/ext/backups/download/{name}")
		assert got.status_code == 200 and got.data[:2] == b"PK"
		assert a.get("/admin/ext/backups/download/../../etc/passwd").status_code == 404
		assert login_as("editor").get("/admin/ext/backups/").status_code == 403
	finally:
		manager.deactivate("backups")


def test_package_index_install_and_checksum(app, login_as, tmp_path, monkeypatch):
	import hashlib, subprocess, sys
	import package_index
	subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_packages.py")], check=True, capture_output=True)
	index = package_index.load_index()
	assert {p["slug"] for p in index} >= {"newsletter", "backups", "seo-audit", "minimal"}
	a = login_as("administrator")
	assert a.get("/admin/packages").status_code == 200
	shutil.rmtree(os.path.join(em.EXTENSIONS_DIR, "seo-audit"), ignore_errors=True)
	r = a.post("/admin/packages/install", data={"csrf_token": token_of(a), "slug": "seo-audit", "kind": "extension"})
	assert r.status_code == 302 and os.path.isdir(os.path.join(em.EXTENSIONS_DIR, "seo-audit"))
	assert not manager.is_active("seo-audit")  # installing never activates
	# a tampered index hash is refused
	entry = next(p for p in index if p["slug"] == "backups")
	bad = [dict(p, sha256="0" * 64) if p["slug"] == "backups" else p for p in index]
	monkeypatch.setattr(package_index, "load_index", lambda: bad)
	shutil.rmtree(os.path.join(em.EXTENSIONS_DIR, "backups"), ignore_errors=True)
	a.post("/admin/packages/install", data={"csrf_token": token_of(a), "slug": "backups", "kind": "extension"})
	assert not os.path.isdir(os.path.join(em.EXTENSIONS_DIR, "backups"))
	assert login_as("editor").get("/admin/packages").status_code == 403


def test_minimal_theme_renders_in_isolation(tmp_path):
	import subprocess, sys
	code = (
		"import app\n"
		"from app import app as a, _switch_theme\n"
		"from auth import hash_password\n"
		"from data_store import db_manager\n"
		"db_manager.create_user('admin', 'a@example.com', hash_password('pw-pw-pw-pw'), role='administrator')\n"
		"assert _switch_theme('minimal')\n"
		"c = a.test_client()\n"
		"r = c.get('/')\n"
		"assert r.status_code == 200, r.status_code\n"
		"html = r.data.decode()\n"
		"assert 'Source Serif 4' in html and 'rel=\"canonical\"' in html\n"
		"assert c.get('/search?q=x').status_code == 200\n"
		"assert c.get('/contact').status_code == 200\n"
		"print('ok')\n"
	)
	env = dict(os.environ, DATABASE_URL=f"sqlite:///{tmp_path}/m.db")
	out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
	assert "ok" in out.stdout, out.stdout[-500:] + out.stderr[-1500:]
