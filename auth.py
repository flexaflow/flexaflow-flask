"""
Accounts, roles and access control for FlexaFlow.

Roles (modelled on WordPress):

    administrator  everything, including users and site settings
    editor         manage all pages, posts, categories/tags and media
    author         write and publish their own posts
    contributor    write their own posts, but drafts only (cannot publish)

Access is decided by capability names. A route declares the capability it
needs with ``@capability_required("...")``; ``can()`` maps a role to the
capabilities it grants. Keep this table as the single source of truth.
"""

import time
from collections import defaultdict
from functools import wraps
from typing import Any, Dict, Optional

from flask import abort, flash, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from data_store import (
	ROLE_ADMINISTRATOR,
	ROLE_AUTHOR,
	ROLE_CONTRIBUTOR,
	ROLE_EDITOR,
	ROLES,
	db_manager,
)

ROLE_LABELS = {
	ROLE_ADMINISTRATOR: "Administrator",
	ROLE_EDITOR: "Editor",
	ROLE_AUTHOR: "Author",
	ROLE_CONTRIBUTOR: "Contributor",
}

ROLE_DESCRIPTIONS = {
	ROLE_ADMINISTRATOR: "Full access, including users and site settings.",
	ROLE_EDITOR: "Manage all pages, posts, categories, tags and media.",
	ROLE_AUTHOR: "Write and publish their own posts.",
	ROLE_CONTRIBUTOR: "Write their own posts; submissions stay as drafts.",
}

CAPABILITIES = {
	"manage_users": {ROLE_ADMINISTRATOR},
	"manage_settings": {ROLE_ADMINISTRATOR},
	"manage_pages": {ROLE_ADMINISTRATOR, ROLE_EDITOR},
	"manage_taxonomy": {ROLE_ADMINISTRATOR, ROLE_EDITOR},
	"manage_people": {ROLE_ADMINISTRATOR, ROLE_EDITOR},
	"manage_extensions": {ROLE_ADMINISTRATOR},
	"edit_others_content": {ROLE_ADMINISTRATOR, ROLE_EDITOR},
	"publish_posts": {ROLE_ADMINISTRATOR, ROLE_EDITOR, ROLE_AUTHOR},
	"create_posts": {ROLE_ADMINISTRATOR, ROLE_EDITOR, ROLE_AUTHOR, ROLE_CONTRIBUTOR},
	"upload_media": {ROLE_ADMINISTRATOR, ROLE_EDITOR, ROLE_AUTHOR, ROLE_CONTRIBUTOR},
	"manage_media": {ROLE_ADMINISTRATOR, ROLE_EDITOR, ROLE_AUTHOR},
}

MIN_PASSWORD_LENGTH = 10


def current_user() -> Optional[Dict[str, Any]]:
	"""
	The logged-in, active user for this request, or None.

	The user row is re-read on each request, so deactivating an account or
	changing its role takes effect immediately.
	"""
	if "_current_user" in g:
		return g._current_user
	user = None
	user_id = session.get("user_id")
	if user_id:
		row = db_manager.get_user(int(user_id))
		if row and row["is_active"]:
			user = row
	g._current_user = user
	return user


def can(capability: str, user: Optional[Dict[str, Any]] = None) -> bool:
	"""True if the user (default: the current user) holds the capability."""
	user = user if user is not None else current_user()
	if not user:
		return False
	return user["role"] in CAPABILITIES.get(capability, set())


def login_user(user_id: int) -> None:
	"""Start a fresh session for a user. Clears any previous state (prevents fixation)."""
	session.clear()
	session.permanent = True
	session["user_id"] = int(user_id)
	db_manager.touch_last_login(int(user_id))


def logout_user() -> None:
	session.clear()


def login_required(view):
	@wraps(view)
	def wrapper(*args, **kwargs):
		if current_user() is None:
			session.clear()
			flash("Please log in to continue.", "warning")
			return redirect(url_for("login", next=request.path))
		return view(*args, **kwargs)
	return wrapper


def capability_required(capability: str):
	"""Require login AND the given capability. Responds 403 otherwise."""
	def decorator(view):
		@wraps(view)
		def checked(*args, **kwargs):
			if not can(capability):
				abort(403)
			return view(*args, **kwargs)
		return login_required(checked)
	return decorator


def validate_password(password: str, username: str = "") -> Optional[str]:
	"""Return an error message, or None if the password is acceptable."""
	if not password or len(password) < MIN_PASSWORD_LENGTH:
		return f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
	if username and password.lower() == username.lower():
		return "Password must not be the same as the username."
	return None


def hash_password(password: str) -> str:
	return generate_password_hash(password)


def verify_password(password_hash: str, password: str) -> bool:
	try:
		return check_password_hash(password_hash, password)
	except (ValueError, TypeError):
		return False


MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60
_failures: Dict[str, list] = defaultdict(list)


def _recent_failures(username: str) -> list:
	now = time.time()
	_failures[username] = [t for t in _failures[username] if now - t < WINDOW_SECONDS]
	return _failures[username]


def is_locked_out(username: str) -> bool:
	return len(_recent_failures(username)) >= MAX_FAILURES


def record_failure(username: str) -> None:
	_recent_failures(username).append(time.time())


def clear_failures(username: str) -> None:
	_failures.pop(username, None)


def validate_role(role: str) -> Optional[str]:
	return None if role in ROLES else f"Unknown role '{role}'."
