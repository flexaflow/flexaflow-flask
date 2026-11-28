"""Admin screens: page search and bulk actions, cache controls, and the shared admin layout."""
from app import get_page_by_slug
from data_store import update_site_settings
from flexaflow_fixtures import token_of


def _add_page(c, title, slug, status="published", content="<p>body</p>"):
	data = {"csrf_token": token_of(c), "title": title, "slug": slug, "content": content, "status": status}
	return c.post("/page/add", data=data)


def test_pages_list_search_filters_and_renders(login_as):
	c = login_as("administrator")
	_add_page(c, "Zebra crossing guide", "zebra-crossing-guide", content="<p>stripes</p>")
	_add_page(c, "Zebra draft notes", "zebra-draft-notes", status="draft")

	by_title = c.get("/admin/pages?q=zebra").data
	assert b"Zebra crossing guide" in by_title and b"Zebra draft notes" in by_title
	assert b"Zebra draft notes" not in c.get("/admin/pages?q=zebra&status=published").data
	assert b"Zebra crossing guide" in c.get("/admin/pages?q=stripes").data  # searches page text too
	assert c.get("/admin/pages?q=%27%20OR%201%3D1%20--").status_code == 200


def test_pages_bulk_draft_publish_and_delete(login_as):
	c = login_as("administrator")
	_add_page(c, "Bulk one", "bulk-one")
	_add_page(c, "Bulk two", "bulk-two")
	ids = [get_page_by_slug("bulk-one")["id"], get_page_by_slug("bulk-two")["id"]]

	resp = c.post("/admin/pages/bulk", data={"csrf_token": token_of(c), "action": "draft", "ids": ids,
	                                         "back": "/admin/pages"})
	assert resp.status_code == 302 and resp.headers["Location"].endswith("/admin/pages")
	assert get_page_by_slug("bulk-one")["status"] == "draft" and get_page_by_slug("bulk-two")["status"] == "draft"

	c.post("/admin/pages/bulk", data={"csrf_token": token_of(c), "action": "publish", "ids": ids})
	assert get_page_by_slug("bulk-one")["status"] == "published"

	c.post("/admin/pages/bulk", data={"csrf_token": token_of(c), "action": "delete", "ids": ids})
	assert get_page_by_slug("bulk-one") is None and get_page_by_slug("bulk-two") is None


def test_bulk_ignores_unknown_action_and_external_redirect(login_as):
	c = login_as("administrator")
	resp = c.post("/admin/pages/bulk", data={"csrf_token": token_of(c), "action": "explode", "ids": ["1"],
	                                         "back": "https://evil.example/"})
	assert resp.status_code == 302 and resp.headers["Location"].endswith("/admin/pages")


def test_clear_cache_button_and_disabled_cache_setting(login_as, client):
	a = login_as("administrator")
	page = a.get("/admin/settings")
	assert page.status_code == 200 and b"Clear cache now" in page.data and b"page_cache_ttl" in page.data

	resp = a.post("/admin/settings/clear-cache", data={"csrf_token": token_of(a)})
	assert resp.status_code == 302 and resp.headers["Location"].endswith("/admin/settings#cache")

	update_site_settings({"page_cache_enabled": "0"})
	try:
		assert client.get("/").headers.get("X-Cache") is None
		assert client.get("/").headers.get("X-Cache") is None
	finally:
		update_site_settings({"page_cache_enabled": "1"})


def test_settings_and_tags_use_admin_layout(login_as):
	a = login_as("administrator")
	for path in ["/admin/settings", "/admin/tags-and-catagories", "/admin/pages"]:
		html = a.get(path).data
		assert b"FlexaFlow" in html and b'id="adminNav"' in html, path
		assert b"data-bs-toggle=\"dropdown\"" in html, path
