from migrate_utils import sanitize_html


def test_script_removed():
	assert "<script" not in sanitize_html("<p>hi</p><script>alert(1)</script>")


def test_event_handler_removed():
	out = sanitize_html('<img src="/a.png" onerror="alert(1)">')
	assert "onerror" not in out and "<img" in out


def test_javascript_url_removed():
	assert "javascript" not in sanitize_html('<a href="javascript:alert(1)">x</a>')


def test_iframe_host_allow_list():
	assert "evil.example" not in sanitize_html('<iframe src="https://evil.example/x"></iframe>')
	assert "youtube.com" in sanitize_html('<iframe src="https://www.youtube.com/embed/abcdefghijk"></iframe>')


def test_app_boots_and_headers(client):
	resp = client.get("/")
	assert resp.status_code < 500
	assert resp.headers["X-Content-Type-Options"] == "nosniff"
	assert "frame-ancestors" in resp.headers["Content-Security-Policy"]
