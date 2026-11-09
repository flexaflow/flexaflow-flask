"""Alembic environment. Uses the app's own engine and metadata."""
import os
import sys

from alembic import context

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_store import Base, engine  # noqa: E402

target_metadata = Base.metadata


def run_migrations_offline():
	context.configure(url=str(engine.url), target_metadata=target_metadata, literal_binds=True)
	with context.begin_transaction():
		context.run_migrations()


def run_migrations_online():
	# When the app runs migrations at startup it passes its own connection.
	connection = context.config.attributes.get("connection")
	if connection is not None:
		_run(connection)
		return
	with engine.connect() as conn:
		_run(conn)


def _run(connection):
	context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
	with context.begin_transaction():
		context.run_migrations()


if context.is_offline_mode():
	run_migrations_offline()
else:
	run_migrations_online()
