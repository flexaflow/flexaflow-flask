import datetime
import io
import os

import pytest
from PIL import Image

from app import app as flask_app, get_post_by_slug, get_page_by_slug
from flexaflow_fixtures import login, token_of
from data_store import Comment, ContactMessage, Post, Redirect, db_manager, update_site_settings


def _add_post(c, **extra):
	data = {"csrf_token": token_of(c), "title": "T", "content": "<p>hi there</p>", "status": "published", **extra}
	return c.post("/post/add", data=data)


def _utc_local(delta):
	return (datetime.datetime.utcnow() + delta).strftime("%Y-%m-%dT%H:%M")


# ---- scheduling and review ---------------------------------------------------
def test_scheduled_post_is_hidden_then_published(login_as, client):
	import features
	c = login_as("editor")
	_add_post(c, title="Later", slug="later-post", publish_at=_utc_local(datetime.timedelta(hours=2)), tz_offset="0")
	assert get_post_by_slug("later-post")["status"] == "scheduled"
	assert client.get("/post/later-post").status_code == 404
	db = db_manager.get_session()
	try:
		db.query(Post).filter(Post.slug == "later-post").update({"publish_at": datetime.datetime.utcnow() - datetime.timedelta(minutes=1)})
		db.commit()
	finally:
		db.close()
	assert features.publish_due(force=True) == 1
	assert get_post_by_slug("later-post")["status"] == "published"
	assert client.get("/post/later-post").status_code == 200


def test_timezone_offset_converted_to_utc():
	import features
	# 10:00 in UTC+6 (JS offset -360) is 04:00 UTC
	assert features.parse_publish_at("2030-01-01T10:00", "-360") == datetime.datetime(2030, 1, 1, 4, 0)


def test_review_workflow(login_as):
	c = login_as("contributor")
	_add_post(c, title="Needs review", slug="needs-review", status="pending")
	assert get_post_by_slug("needs-review")["status"] == "pending"
	assert c.get("/admin/review").status_code == 403
	e = login_as("editor")
	assert b"Needs review" in e.get("/admin/review").data
	e.post("/admin/review/needs-review/approve", data={"csrf_token": token_of(e)})
	assert get_post_by_slug("needs-review")["status"] == "published"


def test_review_reject_returns_draft(login_as):
	c = login_as("contributor")
	_add_post(c, title="Nope", slug="nope-post", status="pending")
	e = login_as("editor")
	e.post("/admin/review/nope-post/reject", data={"csrf_token": token_of(e), "note": "fix it"})
	assert get_post_by_slug("nope-post")["status"] == "draft"


# ---- revisions, autosave, redirects ----------------------------------------------------
def test_edit_creates_revision_and_restore(login_as):
	c = login_as("editor")
	_add_post(c, title="Hist", slug="hist-post", content="<p>version one</p>")
	c.post("/post/edit/hist-post", data={"csrf_token": token_of(c), "title": "Hist", "content": "<p>version two</p>", "status": "published"})
	pid = get_post_by_slug("hist-post")["id"]
	page = c.get(f"/admin/history/post/{pid}")
	assert page.status_code == 200 and b"Compare" in page.data
	from data_store import Revision
	db = db_manager.get_session()
	try:
		rev = db.query(Revision).filter(Revision.kind == "post", Revision.object_id == pid).first()
		rid = rev.id
	finally:
		db.close()
	assert b"version one" in c.get(f"/admin/history/revision/{rid}").data
	c.post(f"/admin/history/revision/{rid}/restore", data={"csrf_token": token_of(c)})
	assert "version one" in get_post_by_slug("hist-post")["content"]


def test_autosave_round_trip_and_ownership(login_as):
	c = login_as("author")
	_add_post(c, title="Auto", slug="auto-post")
	pid = get_post_by_slug("auto-post")["id"]
	r = c.post(f"/admin/autosave/post/{pid}", json={"title": "Auto", "content": "<p>draft text</p><script>x</script>"},
	           headers={"X-CSRF-Token": token_of(c)})
	assert r.status_code == 200
	got = c.get(f"/admin/autosave/post/{pid}").get_json()
	assert got and "draft text" in got["content"] and "<script" not in got["content"]
	other = login_as("contributor")  # not their post
	assert other.get(f"/admin/autosave/post/{pid}").status_code == 403


def test_page_slug_change_creates_redirect(login_as, client):
	c = login_as("editor")
	c.post("/page/add", data={"csrf_token": token_of(c), "title": "Old", "slug": "old-page", "content": "<p>x</p>", "status": "published"})
	assert get_page_by_slug("old-page")
	c.post("/page/edit/old-page", data={"csrf_token": token_of(c), "title": "Old", "slug": "new-page", "content": "<p>x</p>", "status": "published"})
	resp = client.get("/old-page")
	assert resp.status_code == 301 and resp.headers["Location"].endswith("/new-page")


def test_manual_redirect_rejects_open_redirect(login_as):
	c = login_as("editor")
	c.post("/admin/redirects", data={"csrf_token": token_of(c), "source": "/a", "target": "//evil.example"})
	db = db_manager.get_session()
	try:
		assert db.query(Redirect).filter(Redirect.source == "/a").count() == 0
	finally:
		db.close()


# ---- contact form -------------------------------------------------------------------------
def _contact(client, **over):
	client.get("/contact")
	with client.session_transaction() as s:
		s["contact_started"] = 0  # pretend the form was open for a while
		tok = s["csrf_token"]
	data = {"csrf_token": tok, "name": "Ann", "email": "ann@example.com", "subject": "Hi", "message": "Hello there", **over}
	return client.post("/contact", data=data)


def test_contact_form_stores_message(app):
	c = app.test_client()
	assert _contact(c, message="Stored message text").status_code == 302
	db = db_manager.get_session()
	try:
		assert db.query(ContactMessage).filter(ContactMessage.message == "Stored message text").count() == 1
	finally:
		db.close()


def test_contact_honeypot_and_validation(app):
	c = app.test_client()
	_contact(c, website="http://spam", message="spam by bot")
	bad = _contact(app.test_client(), email="not-an-email")
	assert bad.status_code == 200 and b"valid email" in bad.data
	db = db_manager.get_session()
	try:
		assert db.query(ContactMessage).filter(ContactMessage.message == "spam by bot").count() == 0
	finally:
		db.close()


def test_contact_rate_limit(app):
	ok = 0
	for i in range(8):
		r = _contact(app.test_client(), message=f"flood {i}")
		ok += r.status_code == 302
	assert ok <= 5


def test_contact_page_not_cached_and_inbox_is_staff_only(login_as, client):
	assert client.get("/contact").headers["Cache-Control"] == "no-store"
	assert login_as("author").get("/admin/messages").status_code == 403
	assert login_as("editor").get("/admin/messages").status_code == 200


# ---- comments ---------------------------------------------------------------------------------
def test_comment_moderation(login_as, app):
	update_site_settings({"comments_enabled": "1"})
	try:
		a = login_as("author")
		_add_post(a, title="Talk", slug="talk-post")
		anon = app.test_client()
		anon.get("/post/talk-post")
		with anon.session_transaction() as s:
			tok = s["csrf_token"]
		anon.post("/post/talk-post/comment", data={"csrf_token": tok, "author_name": "Bob", "body": "Nice <b>post</b>"})
		assert b"Nice" not in app.test_client().get("/post/talk-post").data  # pending: not shown
		e = login_as("editor")
		assert b"Bob" in e.get("/admin/comments").data
		db = db_manager.get_session()
		try:
			cid = db.query(Comment).filter(Comment.author_name == "Bob").first().id
		finally:
			db.close()
		e.post(f"/admin/comments/{cid}/approve", data={"csrf_token": token_of(e)})
		html = app.test_client().get("/post/talk-post").data
		assert b"Nice" in html and b"<b>post</b>" not in html  # escaped
	finally:
		update_site_settings({"comments_enabled": "0"})


# ---- public extras --------------------------------------------------------------------------------
def test_feed_and_robots(client, login_as):
	_add_post(login_as("editor"), title="In feed", slug="in-feed")
	feed = client.get("/feed.xml")
	assert feed.status_code == 200 and b"<feed" in feed.data and b"In feed" in feed.data
	robots = client.get("/robots.txt")
	assert b"Disallow: /admin" in robots.data and b"sitemap.xml" in robots.data


def test_robots_is_editable(login_as, client):
	c = login_as("administrator")
	c.post("/admin/settings/more", data={"csrf_token": token_of(c), "robots_txt": "User-agent: *\nDisallow: /", "mail_security": "starttls"})
	assert client.get("/robots.txt").data.startswith(b"User-agent: *\nDisallow: /")
	c.post("/admin/settings/more", data={"csrf_token": token_of(c), "robots_txt": "", "mail_security": "starttls"})


def test_seo_head_and_json_ld(login_as, client):
	c = login_as("editor")
	_add_post(c, title="Seo post", slug="seo-post", seo_title="Custom SEO title", seo_description="Custom desc",
	          seo_canonical="https://example.com/canon")
	html = client.get("/post/seo-post").data.decode()
	assert "Custom SEO title" in html and 'rel="canonical" href="https://example.com/canon"' in html
	assert "application/ld+json" in html and "BlogPosting" in html and "</script><script" not in html.split("ld+json")[1][:400]


def test_settings_pages_render(login_as):
	a = login_as("administrator")
	for path in ["/admin", "/admin/extensions", "/admin/themes", "/admin/settings", "/admin/settings/more",
	             "/admin/review", "/admin/comments", "/admin/messages", "/admin/redirects", "/post/add", "/page/add"]:
		assert a.get(path).status_code == 200, path


def test_healthz(client):
	assert client.get("/healthz").get_json()["status"] == "ok"


# ---- full-text search --------------------------------------------------------------------------------
def test_search_matches_prefix_and_ignores_drafts(login_as, client):
	c = login_as("editor")
	_add_post(c, title="Zanzibar travel diary", slug="zanzibar", content="<p>spices and beaches</p>")
	_add_post(c, title="Zanzibar secret", slug="zanzibar-draft", status="draft")
	html = client.get("/search?q=zanzi").data
	assert b"Zanzibar travel diary" in html and b"Zanzibar secret" not in html
	assert b"Zanzibar travel diary" in client.get("/search?q=beaches").data
	assert client.get('/search?q="%27 OR 1=1 --').status_code == 200


# ---- cache -------------------------------------------------------------------------------------------------
def test_page_cache_hit_and_clear_on_edit(login_as, app):
	c = login_as("editor")
	_add_post(c, title="Cache me", slug="cache-me", content="<p>first body</p>")
	anon = app.test_client()
	assert anon.get("/post/cache-me").headers.get("X-Cache") == "MISS"
	hit = anon.get("/post/cache-me")
	assert hit.headers.get("X-Cache") == "HIT" and b"first body" in hit.data
	c.post("/post/edit/cache-me", data={"csrf_token": token_of(c), "title": "Cache me", "content": "<p>second body</p>", "status": "published"})
	fresh = anon.get("/post/cache-me")
	assert b"second body" in fresh.data and fresh.headers.get("X-Cache") == "MISS"
	etag = fresh.headers["ETag"]
	assert anon.get("/post/cache-me", headers={"If-None-Match": etag}).status_code == 304


def test_logged_in_users_never_get_cached_pages(login_as):
	c = login_as("editor")
	c.get("/")
	assert c.get("/").headers.get("X-Cache") is None


# ---- images -----------------------------------------------------------------------------------------------------
def test_responsive_images(tmp_path):
	import images
	up = tmp_path
	(up / "thumbnails").mkdir()
	Image.new("RGB", (2000, 1000), "red").save(up / "abc.jpg")
	Image.new("RGB", (1024, 512), "red").save(up / "thumbnails" / "abc-large.jpg")
	Image.new("RGB", (300, 150), "red").save(up / "thumbnails" / "abc-medium.jpg")
	Image.new("RGB", (1024, 512), "red").save(up / "thumbnails" / "abc-large.webp")
	out = images.make_responsive('<p><img src="/uploads/abc.jpg" alt="x"></p>', str(up))
	assert "srcset=" in out and "2000w" in out and "1024w" in out
	assert 'loading="lazy"' in out and "<picture>" in out and "image/webp" in out
	assert images.make_responsive('<img src="https://elsewhere/x.jpg">', str(up)) == '<img src="https://elsewhere/x.jpg">'


# ---- headers / uploads --------------------------------------------------------------------------------------------
def test_uploads_are_cacheable(login_as, client):
	c = login_as("author")
	buf = io.BytesIO()
	Image.new("RGB", (40, 20), "blue").save(buf, "PNG")
	buf.seek(0)
	r = c.post("/upload_image", data={"file": (buf, "pic.png")}, headers={"X-CSRF-Token": token_of(c)}, content_type="multipart/form-data")
	assert r.status_code == 200, r.data
	loc = r.get_json()["location"]
	got = client.get(loc)
	assert "max-age" in got.headers["Cache-Control"] and got.headers.get("ETag")
	assert client.get(loc, headers={"If-None-Match": got.headers["ETag"]}).status_code == 304


def test_pages_with_csrf_token_are_never_cached(app, login_as):
	update_site_settings({"comments_enabled": "1"})
	try:
		_add_post(login_as("editor"), title="Form page", slug="form-page")
		a, b = app.test_client(), app.test_client()
		ra = a.get("/post/form-page")
		rb = b.get("/post/form-page")
		assert ra.headers.get("X-Cache") is None and rb.headers.get("X-Cache") is None
		import re
		ta = re.search(rb'name="csrf_token" value="(\w+)"', ra.data).group(1)
		tb = re.search(rb'name="csrf_token" value="(\w+)"', rb.data).group(1)
		assert ta != tb  # each visitor gets their own token
	finally:
		update_site_settings({"comments_enabled": "0"})
