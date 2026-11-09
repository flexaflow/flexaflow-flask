import os
import sys
import tempfile

import pytest

# Isolated database and instance data, set BEFORE the app is imported.
_TMP = tempfile.mkdtemp(prefix="flexaflow-test-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "test.db")
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["FLEXAFLOW_EXTENSIONS_DIR"] = os.path.join(_TMP, "extensions")
os.environ["FLEXAFLOW_UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["FLEXAFLOW_BACKUP_DIR"] = os.path.join(_TMP, "backups")
os.environ["CACHE_DIR"] = os.path.join(_TMP, "cache")
os.environ["FLEXAFLOW_2FA_KEY"] = "mIXUOmAbnJGl3B_x0SDdqK9e_c6oR0b0Kj6yT9wP1pU="
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app as flask_app  # noqa: E402
from auth import hash_password  # noqa: E402
from data_store import db_manager  # noqa: E402

PASSWORD = "correct horse battery"
ROLES = ["administrator", "editor", "author", "contributor"]


@pytest.fixture(scope="session", autouse=True)
def users():
	for role in ROLES:
		if not db_manager.get_user_by_username(role):
			db_manager.create_user(role, f"{role}@example.com", hash_password(PASSWORD), role=role)
	return ROLES


@pytest.fixture()
def app():
	flask_app.config.update(TESTING=True)
	return flask_app


@pytest.fixture()
def client(app):
	return app.test_client()


def csrf(client) -> str:
	"""Fetch a CSRF token for this client's session (the login page sets one)."""
	client.get("/login")
	with client.session_transaction() as sess:
		return sess["csrf_token"]


def login(client, username, password=PASSWORD, **extra):
	token = csrf(client)
	return client.post("/login", data={"username": username, "password": password, "csrf_token": token, **extra})


@pytest.fixture()
def login_as(app):
	def _login(role):
		c = app.test_client()
		resp = login(c, role)
		assert resp.status_code == 302, resp.data[:300]
		token_of(c)
		return c
	return _login


def token_of(client) -> str:
	"""Current CSRF token; renders a page first because login clears the session (and the token)."""
	with client.session_transaction() as sess:
		if "csrf_token" in sess:
			return sess["csrf_token"]
	client.get("/login")
	with client.session_transaction() as sess:
		return sess["csrf_token"]
