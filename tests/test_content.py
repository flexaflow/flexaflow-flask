import io

import pytest

from auth import CAPABILITIES, can
from flexaflow_fixtures import ROLES, token_of
from app import get_post_by_slug
from data_store import db_manager


@pytest.mark.parametrize("capability", sorted(CAPABILITIES))
@pytest.mark.parametrize("role", ROLES)
def test_capability_table(role, capability):
	assert can(capability, {"role": role}) == (role in CAPABILITIES[capability])


@pytest.mark.parametrize("role,expected", [("administrator", 200), ("editor", 403), ("author", 403), ("contributor", 403)])
def test_users_page_is_admin_only(login_as, role, expected):
	assert login_as(role).get("/admin/users").status_code == expected


def test_anonymous_is_sent_to_login(client):
	resp = client.get("/admin")
	assert resp.status_code == 302 and "/login" in resp.headers["Location"]


def _post(c, **extra):
	data = {"csrf_token": token_of(c), "title": "T", "content": "<p>hi</p>", "status": "published", **extra}
	return c.post("/post/add", data=data)


def test_post_content_is_sanitized_on_save(login_as):
	c = login_as("author")
	payload = '<p>ok</p><script>alert(1)</script><img src="/x.png" onerror="alert(2)">'
	assert _post(c, title="Sanitize me", slug="sanitize-me", content=payload).status_code == 302
	saved = get_post_by_slug("sanitize-me")["content"]
	assert "<script" not in saved and "onerror" not in saved and "<p>ok</p>" in saved


def test_contributor_cannot_publish(login_as):
	c = login_as("contributor")
	_post(c, title="Mine", slug="contrib-post")
	assert get_post_by_slug("contrib-post")["status"] == "draft"


def test_draft_preview_is_sanitized(login_as):
	c = login_as("contributor")
	resp = c.post("/page/preview_draft", data={
		"csrf_token": token_of(c), "title": "x", "description": "", "content": "<script>alert(1)</script><b>b</b>"})
	assert b"<script>alert(1)" not in resp.data


def test_upload_rejects_non_image(login_as):
	c = login_as("author")
	resp = c.post("/upload_image", data={"file": (io.BytesIO(b"<?php ?>"), "evil.php")},
	              headers={"X-CSRF-Token": token_of(c)}, content_type="multipart/form-data")
	assert resp.status_code == 400


def test_upload_requires_csrf(login_as):
	c = login_as("author")
	resp = c.post("/upload_image", data={"file": (io.BytesIO(b"x"), "a.png")}, content_type="multipart/form-data")
	assert resp.status_code == 403


def test_site_package_round_trip(app):
	import site_package as sp
	from data_store import Post
	data = sp.export_package("Test", [], "default", content=True, settings=False, extension_data=False)
	db = db_manager.get_session()
	try:
		before = db.query(Post).count()
		db.query(Post).filter(Post.slug == "sanitize-me").delete()
		db.commit()
	finally:
		db.close()
	sp.import_package(io.BytesIO(data), content=True, settings=False, extension_data=False,
	                  media=False, extensions=False, themes=False)
	db = db_manager.get_session()
	try:
		assert db.query(Post).count() == before
	finally:
		db.close()


WP_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:excerpt="http://wordpress.org/export/1.2/excerpt/"
 xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:wp="http://wordpress.org/export/1.2/">
<channel><title>Old site</title><link>https://old.example</link>
<item><title>Imported</title><link>https://old.example/imported/</link><dc:creator>x</dc:creator>
<content:encoded><![CDATA[<p>Hello</p><script>alert(1)</script>]]></content:encoded>
<excerpt:encoded><![CDATA[]]></excerpt:encoded>
<wp:post_id>10</wp:post_id><wp:post_date>2020-01-01 10:00:00</wp:post_date>
<wp:post_name>imported-from-wp</wp:post_name><wp:status>publish</wp:status><wp:post_type>post</wp:post_type>
</item></channel></rss>"""


def test_wordpress_import_sanitizes(app):
	import migrate as mg
	lines = mg.import_file(WP_XML, "wordpress", mg.ImportOptions())
	post = get_post_by_slug("imported-from-wp")
	assert post is not None and "<script" not in post["content"] and "Hello" in post["content"]


def test_draft_post_is_not_public(login_as, client):
	c = login_as("author")
	_post(c, title="Secret draft", slug="secret-draft", status="draft")
	assert client.get("/post/secret-draft").status_code == 404
	assert c.get("/post/secret-draft").status_code == 200


def test_revision_saved_and_slug_redirect(login_as):
	from app import update_post_by_slug
	from data_store import Revision
	c = login_as("author")
	_post(c, title="Rev", slug="rev-post", content="<p>one</p>")
	assert update_post_by_slug("rev-post", {"title": "Rev", "content": "<p>two</p>"})
	db = db_manager.get_session()
	try:
		pid = get_post_by_slug("rev-post")["id"]
		revs = db.query(Revision).filter(Revision.kind == "post", Revision.object_id == pid).all()
		assert len(revs) == 1 and "one" in revs[0].content
	finally:
		db.close()
