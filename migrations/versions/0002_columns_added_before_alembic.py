"""Add columns that older databases may lack (replaces the old hand-written ALTER TABLE code).

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa

from migrations.helpers import add_column_if_missing as add

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
	add("pages", sa.Column("template", sa.String(100), nullable=False, server_default="default"))
	add("pages", sa.Column("fields", sa.Text()))
	add("pages", sa.Column("author_id", sa.Integer()))
	add("posts", sa.Column("author_id", sa.Integer()))
	add("posts", sa.Column("cover_image", sa.String(1000)))
	add("posts", sa.Column("meta", sa.Text()))
	add("categories", sa.Column("section_path", sa.String(100)))
	add("categories", sa.Column("singular_name", sa.String(100)))
	add("categories", sa.Column("fields_schema", sa.Text()))
	add("categories", sa.Column("sort_order", sa.Integer(), server_default="0"))
	add("categories", sa.Column("date_label", sa.String(60)))
	add("categories", sa.Column("show_time", sa.Boolean(), server_default=sa.false()))
	add("users", sa.Column("two_fa_recovery", sa.Text()))
	add("users", sa.Column("session_version", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
	pass
