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

import json
import logging
import secrets
import time
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
	"moderate": {ROLE_ADMINISTRATOR, ROLE_EDITOR},  # review queue, comments, contact inbox, redirects
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
		if row and row["is_active"] and session.get("sv", 0) == row.get("session_version", 0):
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
	row = db_manager.get_user(int(user_id))
	session["sv"] = (row or {}).get("session_version", 0)
	db_manager.touch_last_login(int(user_id))


def logout_user() -> None:
	session.clear()


def login_required(view):
	@wraps(view)
	def wrapper(*args, **kwargs):
		if current_user() is None:
			session.clear()
			flash("Please log in to continue.", "warning")
			return redirect(url_for("auth.login", next=request.path))
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


# ---- security event log -------------------------------------------------
security_log = logging.getLogger("flexaflow.security")


def client_ip() -> str:
	return request.remote_addr or "unknown"


def log_event(event: str, **fields) -> None:
	"""Write one line to the security log: who, from where, what happened."""
	actor = session.get("user_id") if session else None
	extras = " ".join(f"{k}={v!r}" for k, v in fields.items())
	security_log.info("%s ip=%s actor=%s %s", event, client_ip(), actor, extras)


# ---- login throttling (stored in the database) ---------------------------
# Failures are counted per IP and per (username, IP), never per username alone,
# so a stranger cannot lock a real user out from somewhere else. After a free
# allowance each further failure doubles the wait, capped at MAX_DELAY.
WINDOW_SECONDS = 15 * 60
MAX_DELAY = 15 * 60
FREE_ATTEMPTS = {"user": 5, "ip": 15, "totp": 5, "global": 40}


def _delay_for(timestamps: list, free: int, cap: int = MAX_DELAY) -> int:
	"""Seconds still to wait, given the failure timestamps (UTC) in the window."""
	import datetime
	count = len(timestamps)
	if count < free:
		return 0
	delay = min(2 ** (count - free + 1), cap)
	remaining = delay - (datetime.datetime.utcnow() - timestamps[-1]).total_seconds()
	return max(0, int(remaining) + 1) if remaining > 0 else 0


def _keys(kind: str, who: str):
	ip = client_ip()
	if kind == "totp":
		return [("totp", f"totp:{who}", "totp", MAX_DELAY)]
	return [
		("login", f"ip:{ip}", "ip", MAX_DELAY),
		("login", f"user:{who.lower()}|{ip}", "user", MAX_DELAY),
		("login", f"name:{who.lower()}", "global", 60),  # soft cap so a distributed attack is still slowed
	]


def throttle_wait(kind: str, who: str) -> int:
	"""Seconds the caller must wait before another attempt (0 = go ahead)."""
	wait = 0
	for k, key, allowance, cap in _keys(kind, who):
		times = db_manager.recent_attempts(k, key, WINDOW_SECONDS)
		wait = max(wait, _delay_for(times, FREE_ATTEMPTS[allowance], cap))
	return wait


def record_failure(kind: str, who: str) -> None:
	for k, key, _a, _c in _keys(kind, who):
		db_manager.record_attempt(k, key)
	log_event(f"{kind}_failure", who=who)


def clear_failures(kind: str, who: str) -> None:
	# Clear this user's own counters; the per-IP counter is left alone on purpose.
	for k, key, allowance, _c in _keys(kind, who):
		if allowance != "ip":
			db_manager.clear_attempts(k, key)


def wait_message(seconds: int) -> str:
	mins, secs = divmod(seconds, 60)
	span = f"{mins} min {secs} s" if mins else f"{secs} s"
	return f"Too many failed attempts. Please wait {span} and try again."


# ---- 2FA recovery codes --------------------------------------------------
RECOVERY_CODE_COUNT = 8


def generate_recovery_codes():
	"""Return (plain_codes, json_of_hashes). Show the plain codes once; store only the hashes."""
	plain = [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(RECOVERY_CODE_COUNT)]
	return plain, json.dumps([generate_password_hash(c) for c in plain])


def consume_recovery_code(user: Dict[str, Any], entered: str) -> bool:
	"""If `entered` matches an unused recovery code, mark it used and return True."""
	entered = (entered or "").strip().lower()
	if len(entered) != 13 or "-" not in entered:
		return False
	try:
		hashes = json.loads(user.get("two_fa_recovery") or "[]")
	except ValueError:
		return False
	for i, h in enumerate(hashes):
		if check_password_hash(h, entered):
			del hashes[i]
			db_manager.update_user(user["id"], two_fa_recovery=json.dumps(hashes))
			return True
	return False


def invalidate_other_sessions(user_id: int) -> None:
	"""Bump the user's session version; every session started before this stops working."""
	row = db_manager.get_user(int(user_id))
	new = (row.get("session_version", 0) + 1) if row else 1
	db_manager.update_user(int(user_id), session_version=new)
	if session.get("user_id") == int(user_id):
		session["sv"] = new
	if "_current_user" in g:
		del g._current_user


def validate_role(role: str) -> Optional[str]:
	return None if role in ROLES else f"Unknown role '{role}'."
