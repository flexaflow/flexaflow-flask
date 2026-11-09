import json

import pyotp

from flexaflow_fixtures import PASSWORD, csrf, login, token_of
from data_store import db_manager
from auth import generate_recovery_codes


def test_login_success_and_wrong_password(app):
	good = app.test_client()
	assert login(good, "editor").status_code == 302
	bad = app.test_client()
	resp = login(bad, "editor", "wrong password")
	assert resp.status_code == 200 and b"Invalid credentials" in resp.data


def test_post_without_csrf_is_rejected(client):
	assert client.post("/login", data={"username": "editor", "password": PASSWORD}).status_code == 403


def test_throttle_is_progressive_and_does_not_lock_other_ips(app):
	c = app.test_client()
	for _ in range(5):
		login(c, "author", "nope nope nope")
	blocked = login(c, "author", PASSWORD)  # even the right password waits
	assert blocked.status_code == 429
	# the real user, coming from a different address, is not locked out
	other = app.test_client()
	resp = other.post("/login", data={"username": "author", "password": PASSWORD,
	                                  "csrf_token": csrf(other)},
	                  environ_overrides={"REMOTE_ADDR": "203.0.113.9"})
	assert resp.status_code == 302
	# leave no throttle state behind for the other tests (they all log in from 127.0.0.1)
	db_manager.clear_attempts("login", "ip:127.0.0.1")
	db_manager.clear_attempts("login", "user:author|127.0.0.1")
	db_manager.clear_attempts("login", "name:author")


def test_two_factor_secret_encrypted_at_rest(app):
	secret = pyotp.random_base32()
	user = db_manager.get_user_by_username("contributor")
	db_manager.update_user(user["id"], two_fa_secret=secret, two_fa_enabled=True)
	with db_manager.engine.connect() as conn:
		from sqlalchemy import text
		raw = conn.execute(text("SELECT two_fa_secret FROM users WHERE id=:i"), {"i": user["id"]}).scalar()
	assert raw.startswith("enc:") and secret not in raw
	assert db_manager.get_user(user["id"], include_secrets=True)["two_fa_secret"] == secret


def test_2fa_login_totp_and_recovery_code(app):
	user = db_manager.get_user_by_username("contributor", include_secrets=True)
	secret = user["two_fa_secret"]
	plain, hashed = generate_recovery_codes()
	db_manager.update_user(user["id"], two_fa_recovery=hashed)

	c = app.test_client()
	login(c, "contributor")
	resp = c.post("/login", data={"2fa_code": pyotp.TOTP(secret).now(), "csrf_token": token_of(c)})
	assert resp.status_code == 302

	c2 = app.test_client()
	login(c2, "contributor")
	resp = c2.post("/login", data={"2fa_code": plain[0], "csrf_token": token_of(c2)})
	assert resp.status_code == 302
	# a recovery code works once only
	c3 = app.test_client()
	login(c3, "contributor")
	resp = c3.post("/login", data={"2fa_code": plain[0], "csrf_token": token_of(c3)})
	assert resp.status_code == 200
	db_manager.update_user(user["id"], two_fa_enabled=False, two_fa_secret=None, two_fa_recovery=None)


def test_password_change_invalidates_other_sessions(app):
	a, b = app.test_client(), app.test_client()
	login(a, "editor")
	login(b, "editor")
	assert b.get("/admin").status_code == 200
	resp = a.post("/admin/profile", data={
		"csrf_token": token_of(a), "display_name": "Editor", "email": "editor@example.com",
		"current_password": PASSWORD, "new_password": PASSWORD, "confirm_password": PASSWORD})
	assert resp.status_code == 302
	assert a.get("/admin").status_code == 200  # the session that changed it stays signed in
	assert b.get("/admin").status_code == 302  # the other one is sent to /login
