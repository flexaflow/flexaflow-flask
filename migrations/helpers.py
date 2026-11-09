"""Small guards so a migration can run safely on databases of any age."""
import sqlalchemy as sa
from alembic import op


def has_table(name: str) -> bool:
	return name in sa.inspect(op.get_bind()).get_table_names()


def has_column(table: str, column: str) -> bool:
	if not has_table(table):
		return False
	return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def add_column_if_missing(table: str, column: sa.Column) -> None:
	if has_table(table) and not has_column(table, column.name):
		with op.batch_alter_table(table) as batch:
			batch.add_column(column)
