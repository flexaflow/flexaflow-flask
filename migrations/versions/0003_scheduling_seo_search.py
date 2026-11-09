"""publish_at and seo columns, plus full-text search indexes.

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

from migrations.helpers import add_column_if_missing as add, has_table

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
	for table in ("pages", "posts"):
		add(table, sa.Column("publish_at", sa.DateTime()))
		add(table, sa.Column("seo", sa.Text()))
	bind = op.get_bind()
	if bind.dialect.name == "sqlite":
		_sqlite_fts(bind)
	elif bind.dialect.name in ("mysql", "mariadb"):
		for table in ("posts", "pages"):
			try:
				op.execute(f"ALTER TABLE {table} ADD FULLTEXT INDEX ft_{table} (title, content)")
			except Exception:
				pass  # index already exists


def _sqlite_fts(bind):
	try:
		bind.exec_driver_sql("CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(kind UNINDEXED, ref_id UNINDEXED, title, body)")
	except Exception:
		return  # this SQLite build has no FTS5; search falls back to LIKE
	for kind, table, body in (("post", "posts", "coalesce(new.content,'') || ' ' || coalesce(new.excerpt,'')"),
	                          ("page", "pages", "coalesce(new.content,'') || ' ' || coalesce(new.description,'')")):
		if not has_table(table):
			continue
		bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS {table}_fts_ai")
		bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS {table}_fts_au")
		bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS {table}_fts_ad")
		bind.exec_driver_sql(f"""CREATE TRIGGER {table}_fts_ai AFTER INSERT ON {table} BEGIN
			INSERT INTO search_index(kind, ref_id, title, body) VALUES ('{kind}', new.id, new.title, {body}); END""")
		bind.exec_driver_sql(f"""CREATE TRIGGER {table}_fts_au AFTER UPDATE ON {table} BEGIN
			DELETE FROM search_index WHERE kind = '{kind}' AND ref_id = old.id;
			INSERT INTO search_index(kind, ref_id, title, body) VALUES ('{kind}', new.id, new.title, {body}); END""")
		bind.exec_driver_sql(f"""CREATE TRIGGER {table}_fts_ad AFTER DELETE ON {table} BEGIN
			DELETE FROM search_index WHERE kind = '{kind}' AND ref_id = old.id; END""")
		bind.exec_driver_sql(f"DELETE FROM search_index WHERE kind = '{kind}'")
		bind.exec_driver_sql(
			f"INSERT INTO search_index(kind, ref_id, title, body) "
			f"SELECT '{kind}', id, title, coalesce(content,'') || ' ' || coalesce({'excerpt' if kind == 'post' else 'description'},'') FROM {table}")


def downgrade():
	pass
