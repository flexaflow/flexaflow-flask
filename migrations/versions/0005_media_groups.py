"""Media groups (named collections of images) and their membership table.

Revision ID: 0005
Revises: 0004
"""
import datetime

import sqlalchemy as sa
from alembic import op

from migrations.helpers import has_table

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
	if not has_table("media_groups"):
		op.create_table(
			"media_groups",
			sa.Column("id", sa.Integer(), primary_key=True),
			sa.Column("name", sa.String(120), nullable=False, unique=True),
			sa.Column("description", sa.Text()),
			sa.Column("created_at", sa.DateTime(), default=datetime.datetime.utcnow),
			sa.Column("updated_at", sa.DateTime(), default=datetime.datetime.utcnow),
		)
	if not has_table("media_group_items"):
		op.create_table(
			"media_group_items",
			sa.Column("group_id", sa.Integer(), sa.ForeignKey("media_groups.id", ondelete="CASCADE"), primary_key=True),
			sa.Column("media_id", sa.Integer(), sa.ForeignKey("media.id", ondelete="CASCADE"), primary_key=True),
			sa.Column("added_at", sa.DateTime(), default=datetime.datetime.utcnow),
		)


def downgrade():
	op.drop_table("media_group_items")
	op.drop_table("media_groups")
