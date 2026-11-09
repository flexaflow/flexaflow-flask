"""Baseline: create any table that does not exist yet.

FlexaFlow shipped before it used Alembic, so this revision builds the schema with
create_all(checkfirst) and is safe on both new and existing databases. Every later
change must be an explicit migration in this folder (op.add_column, op.drop_column, ...).

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
	from data_store import Base
	Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade():
	pass  # the baseline is never undone
