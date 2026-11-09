import os
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(db_path, code):
	env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_path}", FLEXAFLOW_2FA_KEY="mIXUOmAbnJGl3B_x0SDdqK9e_c6oR0b0Kj6yT9wP1pU=")
	return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


def test_fresh_database_reaches_head(tmp_path):
	db = tmp_path / "fresh.db"
	out = _run(db, "import data_store")
	assert out.returncode == 0, out.stderr
	con = sqlite3.connect(db)
	assert con.execute("select version_num from alembic_version").fetchone()[0] == "0004"
	tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
	assert {"users", "posts", "revisions", "redirects", "comments", "contact_messages", "login_attempts"} <= tables
	assert "admin_setup" not in tables


def test_old_database_is_upgraded_and_legacy_admin_moved(tmp_path):
	db = tmp_path / "old.db"
	assert _run(db, "import data_store").returncode == 0
	con = sqlite3.connect(db)
	# make it look like a pre-Alembic install: no version table, missing columns, one legacy admin
	con.execute("drop table alembic_version")
	con.execute("alter table users drop column session_version")
	con.execute("alter table users drop column two_fa_recovery")
	con.execute("alter table posts drop column seo")
	con.execute("delete from users")
	con.execute("create table admin_setup (id integer primary key, is_configured boolean, username varchar(255), password varchar(255), "
	            "email varchar(255), two_fa_secret varchar(255), two_fa_enabled boolean)")
	con.execute("insert into admin_setup values (1, 1, 'legacy', 'hash', 'legacy@example.com', 'JBSWY3DPEHPK3PXP', 1)")
	con.commit()
	con.close()
	out = _run(db, "import data_store; from data_store import db_manager; print(db_manager.get_user_by_username('legacy', include_secrets=True)['two_fa_secret'])")
	assert out.returncode == 0, out.stderr
	assert "JBSWY3DPEHPK3PXP" in out.stdout  # decrypts back to the original secret
	con = sqlite3.connect(db)
	cols = {r[1] for r in con.execute("pragma table_info(users)")}
	assert {"session_version", "two_fa_recovery"} <= cols
	assert "seo" in {r[1] for r in con.execute("pragma table_info(posts)")}
	assert con.execute("select two_fa_secret from users where username='legacy'").fetchone()[0].startswith("enc:")
	assert "admin_setup" not in {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
