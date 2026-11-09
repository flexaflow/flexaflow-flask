#!/usr/bin/env python3
"""
FlexaFlow command-line management.

Accounts are created here, not in the web UI, so the first administrator and
every other account follow the same controlled path.

Examples:

    python manage.py createsuperuser                  # prompts for everything
    python manage.py createsuperuser --username admin --email admin@example.edu
    python manage.py createuser --role editor --username ayesha --email a@example.edu
    python manage.py changepassword admin
    python manage.py setrole ayesha author
    python manage.py deactivate ayesha
    python manage.py listusers

    python manage.py import-wordpress export.xml --download-media --author admin
    python manage.py import-blogger blog-backup.xml
    python manage.py export-wordpress out.xml --base-url https://example.com
    python manage.py export-blogger out.xml --base-url https://example.com

Passwords are read with a hidden prompt, or from stdin with --password-stdin
(useful in provisioning scripts), so they never appear in shell history.
"""

import argparse
import getpass
import re
import sys

from data_store import ROLE_ADMINISTRATOR, ROLE_AUTHOR, ROLE_CONTRIBUTOR, ROLE_EDITOR, ROLES, db_manager
from auth import ROLE_LABELS, hash_password, validate_password

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _die(message: str, code: int = 1):
	print(f"error: {message}", file=sys.stderr)
	sys.exit(code)


def _read_password(args) -> str:
	if getattr(args, "password_stdin", False):
		return sys.stdin.readline().rstrip("\n")
	while True:
		first = getpass.getpass("Password: ")
		second = getpass.getpass("Password (again): ")
		if first != second:
			print("Passwords do not match. Try again.")
			continue
		return first


def _prompt(label: str, value, validator=None, default=None):
	while True:
		if value is None:
			suffix = f" [{default}]" if default else ""
			value = input(f"{label}{suffix}: ").strip() or (default or "")
		error = validator(value) if validator else None
		if not error:
			return value
		print(f"  {error}")
		value = None


def _check_username(value: str):
	if not USERNAME_RE.match(value or ""):
		return "Username must be 3-64 characters: letters, digits, '.', '_' or '-'."
	if db_manager.get_user_by_username(value):
		return f"Username '{value}' already exists."
	return None


def _check_email(value: str):
	return None if EMAIL_RE.match(value or "") else "Enter a valid email address."


def _create(args, role: str):
	username = _prompt("Username", args.username, _check_username)
	email = _prompt("Email", args.email, _check_email)
	display = args.name or ""
	password = _read_password(args)
	problem = validate_password(password, username)
	if problem:
		_die(problem)
	try:
		user = db_manager.create_user(
			username=username,
			email=email,
			password_hash=hash_password(password),
			role=role,
			display_name=display or None,
		)
	except ValueError as e:
		_die(str(e))
	print(f"Created {ROLE_LABELS[role].lower()} '{user['username']}' <{user['email']}>.")


def cmd_createsuperuser(args):
	if db_manager.count_active_administrators() and not args.force:
		print("An active administrator already exists. Creating another one anyway.")
	_create(args, ROLE_ADMINISTRATOR)


def cmd_createuser(args):
	_create(args, args.role)


def _find(username: str):
	user = db_manager.get_user_by_username(username)
	if not user:
		_die(f"no user named '{username}'")
	return user


def cmd_changepassword(args):
	user = _find(args.username)
	password = _read_password(args)
	problem = validate_password(password, user["username"])
	if problem:
		_die(problem)
	db_manager.update_user(user["id"], password_hash=hash_password(password))
	print(f"Password updated for '{user['username']}'.")


def cmd_setrole(args):
	user = _find(args.username)
	if user["role"] == ROLE_ADMINISTRATOR and args.role != ROLE_ADMINISTRATOR:
		if db_manager.count_active_administrators() <= 1:
			_die("refusing to demote the last active administrator")
	db_manager.update_user(user["id"], role=args.role)
	print(f"'{user['username']}' is now {ROLE_LABELS[args.role].lower()}.")


def _set_active(args, active: bool):
	user = _find(args.username)
	if not active and user["role"] == ROLE_ADMINISTRATOR and db_manager.count_active_administrators() <= 1:
		_die("refusing to deactivate the last active administrator")
	db_manager.update_user(user["id"], is_active=active)
	print(f"'{user['username']}' is {'active' if active else 'deactivated'}.")


def cmd_listusers(args):
	users = db_manager.list_users()
	if not users:
		print("No users yet. Create one with: python manage.py createsuperuser")
		return
	print(f"{'username':<20} {'role':<14} {'active':<7} {'2fa':<4} email")
	for u in users:
		print(f"{u['username']:<20} {u['role']:<14} {str(u['is_active']):<7} "
		      f"{'yes' if u['two_fa_enabled'] else 'no':<4} {u['email']}")


def _ext():
	import app
	import extension_manager as em
	return em


def cmd_ext_list(args):
	em = _ext()
	for e in em.list_installed():
		state = "active" if em.manager.is_active(e["slug"]) else "inactive"
		print(f"{e['slug']:<24} {e['version']:<10} {state:<9} {e.get('error') or e['name']}")


def cmd_ext_install(args):
	em = _ext()
	with open(args.zipfile, "rb") as fh:
		manifest = em.install_zip(fh)
	print(f"Installed {manifest['name']} {manifest['version']}.")
	if args.activate:
		em.manager.activate(manifest["slug"])
		print("Activated.")


def cmd_ext_toggle(args, on):
	em = _ext()
	(em.manager.activate if on else em.manager.deactivate)(args.slug)
	print("Activated." if on else "Deactivated.")


def _migrate_import(args, source):
	import app as core
	import migrate as mg
	import migrate_views
	from migrate_utils import MigrationError

	author = db_manager.get_user_by_username(args.author) if args.author else None
	if args.author and not author:
		_die(f"no such user: {args.author}")
	with open(args.file, "rb") as fh:
		data = fh.read()
	opts = mg.ImportOptions(
		conflict=args.conflict, include_drafts=not args.no_drafts, import_pages=not args.no_pages,
		import_settings=args.settings, import_menu=args.menu, download_media=args.download_media,
		max_downloads=10 ** 6, time_budget=60 * 60 * 6, author_id=author["id"] if author else None,
		reserved_slugs=set(core.RESERVED_SLUGS),
	)
	try:
		for line in mg.import_file(data, source, opts, store_media=migrate_views.media_storer() if args.download_media else None):
			print(line)
	except MigrationError as e:
		_die(str(e))


def _migrate_export(args, target):
	import app as core
	import migrate as mg
	from migrate_utils import MigrationError

	try:
		if target == "wordpress":
			data = mg.export_wordpress(args.base_url, include_drafts=not args.no_drafts, include_pages=not args.no_pages,
				include_media=not args.no_media, include_menu=not args.no_menu)
		else:
			data = mg.export_blogger(args.base_url, include_drafts=not args.no_drafts, include_pages=not args.no_pages)
	except MigrationError as e:
		_die(str(e))
	with open(args.output, "wb") as fh:
		fh.write(data)
	print(f"Wrote {args.output} ({len(data) // 1024} KB).")


def _alembic_cfg():
	import os
	from alembic.config import Config
	cfg = Config()
	cfg.set_main_option("script_location", os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations"))
	return cfg


def cmd_publish_due(args):
	import features
	print(f"Published {features.publish_due(force=True)} item(s).")


def cmd_db_upgrade(args):
	from alembic import command
	command.upgrade(_alembic_cfg(), "head")
	print("Database is up to date.")


def cmd_db_current(args):
	from alembic import command
	command.current(_alembic_cfg(), verbose=True)


def build_parser():
	parser = argparse.ArgumentParser(description="FlexaFlow management commands")
	sub = parser.add_subparsers(dest="command", required=True)

	def add_account_args(p):
		p.add_argument("--username")
		p.add_argument("--email")
		p.add_argument("--name", help="Display name (optional)")
		p.add_argument("--password-stdin", action="store_true", help="Read the password from stdin")

	p = sub.add_parser("createsuperuser", help="Create an administrator account")
	add_account_args(p)
	p.add_argument("--force", action="store_true", help="Do not warn when administrators already exist")
	p.set_defaults(func=cmd_createsuperuser)

	p = sub.add_parser("createuser", help="Create a user with a given role")
	add_account_args(p)
	p.add_argument("--role", required=True, choices=ROLES)
	p.set_defaults(func=cmd_createuser)

	p = sub.add_parser("changepassword", help="Change a user's password")
	p.add_argument("username")
	p.add_argument("--password-stdin", action="store_true")
	p.set_defaults(func=cmd_changepassword)

	p = sub.add_parser("setrole", help="Change a user's role")
	p.add_argument("username")
	p.add_argument("role", choices=ROLES)
	p.set_defaults(func=cmd_setrole)

	p = sub.add_parser("deactivate", help="Block a user from logging in")
	p.add_argument("username")
	p.set_defaults(func=lambda a: _set_active(a, False))

	p = sub.add_parser("activate", help="Allow a deactivated user to log in again")
	p.add_argument("username")
	p.set_defaults(func=lambda a: _set_active(a, True))

	p = sub.add_parser("publish-due", help="Publish scheduled posts and pages whose time has come (for cron)")
	p.set_defaults(func=cmd_publish_due)

	p = sub.add_parser("db-upgrade", help="Apply database migrations (also done automatically at startup)")
	p.set_defaults(func=cmd_db_upgrade)

	p = sub.add_parser("db-current", help="Show the database schema revision")
	p.set_defaults(func=cmd_db_current)

	p = sub.add_parser("listusers", help="List accounts")
	p.set_defaults(func=cmd_listusers)

	p = sub.add_parser("ext-list", help="List installed extensions")
	p.set_defaults(func=cmd_ext_list)

	p = sub.add_parser("ext-install", help="Install an extension .zip")
	p.add_argument("zipfile")
	p.add_argument("--activate", action="store_true")
	p.set_defaults(func=cmd_ext_install)

	p = sub.add_parser("ext-activate", help="Activate an installed extension")
	p.add_argument("slug")
	p.set_defaults(func=lambda a: cmd_ext_toggle(a, True))

	p = sub.add_parser("ext-deactivate", help="Deactivate an extension")
	p.add_argument("slug")
	p.set_defaults(func=lambda a: cmd_ext_toggle(a, False))

	for source, label in (("wordpress", "WordPress (WXR .xml)"), ("blogger", "Blogger (backup .xml)")):
		p = sub.add_parser(f"import-{source}", help=f"Import a {label} export")
		p.add_argument("file")
		p.add_argument("--conflict", choices=["skip", "rename", "update"], default="skip")
		p.add_argument("--author", help="Username that becomes the author of imported items")
		p.add_argument("--no-drafts", action="store_true")
		p.add_argument("--no-pages", action="store_true")
		p.add_argument("--download-media", action="store_true", help="Copy images from the old site (no limit)")
		p.add_argument("--settings", action="store_true", help="Use the old site's title and description")
		p.add_argument("--menu", action="store_true", help="Replace the navigation menu (WordPress only)")
		p.set_defaults(func=lambda a, s=source: _migrate_import(a, s))

		p = sub.add_parser(f"export-{source}", help=f"Write a file you can import into {source.capitalize()}")
		p.add_argument("output")
		p.add_argument("--base-url", required=True, help="Public address of this site, e.g. https://example.com")
		p.add_argument("--no-drafts", action="store_true")
		p.add_argument("--no-pages", action="store_true")
		p.add_argument("--no-media", action="store_true")
		p.add_argument("--no-menu", action="store_true")
		p.set_defaults(func=lambda a, s=source: _migrate_export(a, s))

	return parser


if __name__ == "__main__":
	args = build_parser().parse_args()
	args.func(args)
