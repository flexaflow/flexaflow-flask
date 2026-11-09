"""Move the legacy single admin (admin_setup table) into users, then drop the table.

Revision ID: 0004
Revises: 0003
"""
import datetime

import sqlalchemy as sa
from alembic import op

from migrations.helpers import has_table

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
	if not has_table("admin_setup"):
		return
	bind = op.get_bind()
	users = bind.execute(sa.text("SELECT COUNT(*) FROM users")).scalar()
	legacy = bind.execute(sa.text(
		"SELECT username, password, email, two_fa_secret, two_fa_enabled FROM admin_setup "
		"WHERE is_configured = :t AND username IS NOT NULL LIMIT 1"), {"t": True}).fetchone()
	if not users and legacy:
		now = datetime.datetime.utcnow()
		bind.execute(sa.text(
			"INSERT INTO users (username, email, password_hash, role, is_active, two_fa_secret, two_fa_enabled, "
			"session_version, created_at, updated_at) VALUES (:u, :e, :p, 'administrator', :t, :s, :f, 0, :n, :n)"),
			{"u": legacy[0], "e": legacy[2] or f"{legacy[0]}@localhost", "p": legacy[1], "t": True,
			 "s": legacy[3] if legacy[4] else None, "f": bool(legacy[4] and legacy[3]), "n": now})
		admin_id = bind.execute(sa.text("SELECT id FROM users WHERE username = :u"), {"u": legacy[0]}).scalar()
		for table in ("pages", "posts"):
			bind.execute(sa.text(f"UPDATE {table} SET author_id = :a WHERE author_id IS NULL"), {"a": admin_id})
	op.drop_table("admin_setup")


def downgrade():
	pass  # the old table held a single admin; it is not recreated
