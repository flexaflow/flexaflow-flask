# FlexaFlow Development Guide

Everything you need to set up a local development environment, follow repository conventions, write Alembic migrations, run tests, and build packages.

---

## 1. Local Setup

```bash
git clone https://github.com/flexaflow/flexaflow-flask.git
cd flexaflow-flask
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt  # pytest, ruff, pip-audit
```

Copy `.env.example` to `.env` (optional) and create your first administrator:
```bash
python manage.py createsuperuser
```

### Running the Dev Server
Option A — Flask dev server (auto-reload & debugger):
```bash
export FLASK_APP=app.py
export FLASK_DEBUG=1
flask run --host=127.0.0.1 --port=5000
```

Option B — Direct Python / Waitress:
```bash
python app.py        # runs Flask debug server when FLASK_ENV=development
# or
python server.py     # Waitress WSGI server
```

---

## 2. Repository Map

| Path | Responsibility |
|---|---|
| `app.py` | Flask app creation, config, Jinja setup, request hooks, page cache, extension sync. |
| `auth.py` | Capabilities, roles, login/TOTP throttling, session versioning, recovery codes, security log. |
| `data_store.py` | SQLAlchemy engine, ORM models, `DatabaseManager`, FTS5/LIKE search, revisions/redirects helpers. |
| `blueprints/` | Core route groups (`admin`, `api`, `auth`, `editor`, `media`, `public`). |
| `content_store.py` & `content_views.py` | Custom sections, directory entries (`Entry`), admin post search & bulk actions, document uploads. |
| `features.py` | Revisions/diff/restore, autosave, review queue, contact form & inbox, comments, redirects, feeds, `robots.txt`, scheduled publishing. |
| `images.py` | Pillow verification/re-encoding (`store_verified_image`), WebP/AVIF thumbnail generation, `|responsive` filter. |
| `seo.py` | `seo_head()` Open Graph, canonical, Atom link, and JSON-LD builder. |
| `mailer.py` | Background/sync SMTP sender reading encrypted credentials from `SiteSetting`. |
| `crypto_utils.py` | Fernet encryption/decryption (`EncryptedString`) for 2FA secrets and SMTP passwords. |
| `extension_manager.py` & `extension_views.py` | Extension loader, `ExtensionAPI`, Werkzeug sub-router, admin UI. |
| `theme_manager.py` & `theme_views.py` | Theme installer, validator, and admin UI. |
| `utils/theme_loader.py` | Loads `themes/<name>/functions/*.py` and copies `themes/<name>/static/`. |
| `page_templates.py` | Parses and validates `page_templates/*.json` schemas and form submissions. |
| `site_package.py` & `package_views.py` | Full-site `.zip` export and import (`content.json`, `settings.json`, `extension_data.json`, `media.json`). |
| `migrate.py`, `migrate_utils.py`, `migrate_views.py` | WordPress WXR & Blogger Atom importer/exporter, `sanitize_html()`, SSRF-safe `safe_fetch()`. |
| `package_index.py` & `tools/build_packages.py` | Local package catalog (`packages/index.json`) with SHA-256 verification. |
| `migrations/` | Alembic environment (`env.py`), helpers (`helpers.py`), and schema revisions (`versions/`). |
| `manage.py` | CLI for accounts, DB migrations, scheduled publishing, extensions, and WP/Blogger migration. |
| `contrib/extensions/` | First-party bundled extensions (`backups`, `newsletter`, `seo-audit`). |
| `project-templates/extension/` | Cookiecutter starter for new extensions. |
| `tests/` | Pytest suite and shared `flexaflow_fixtures.py`. |

---

## 3. Coding Conventions & Security Checklist

Configuration lives in `pyproject.toml`:
* **Python**: Target `3.11+` (Docker uses Python `3.12-slim`).
* **Formatting**: **Tab indentation** (`indent-style = "tab"`), double quotes, max line length `140`.
* **Linting**:
  ```bash
  ruff check .
  ruff format --check <files>
  ```
* **Checklist for new routes & features**:
  1. Protect admin/author endpoints with `@login_required` or `@capability_required("...")` (`auth.py`).
  2. Never mutate state on `GET` requests. `csrf_protect` enforces CSRF on all `POST`/`PUT`/`PATCH`/`DELETE` requests—include `<input type="hidden" name="csrf_token" value="{{ csrf_token }}">` or `X-CSRF-Token`.
  3. Sanitize user-submitted rich HTML with `migrate_utils.sanitize_html()` before saving or rendering with `|safe`.
  4. Never trust client filenames or MIME types on uploads: route images through `images.store_verified_image()`.
  5. Log security-sensitive events (role changes, deletions, package installs, approvals) with `auth.log_event(...)`.
  6. Add an Alembic migration whenever changing `data_store.py` models.

---

## 4. Database Migrations (Alembic)

FlexaFlow runs `alembic upgrade head` automatically when `DatabaseManager` initializes.

### CLI Commands
```bash
python manage.py db-current
python manage.py db-upgrade
```

### Creating a New Migration
1. Update the SQLAlchemy model in `data_store.py`.
2. Add a sequential file in `migrations/versions/` (e.g., `0006_my_feature.py`) with `revision = "0006"` and `down_revision = "0005"`.
3. Use `migrations/helpers.py` (`has_table`, `has_column`, `add_column_if_missing`) and `op.batch_alter_table(...)` (`render_as_batch=True` is enabled in `migrations/env.py`) so migrations stay safe across SQLite and MySQL databases of any age:

```python
"""Add my_column to posts.

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from migrations.helpers import add_column_if_missing

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
	add_column_if_missing("posts", sa.Column("my_column", sa.String(100)))


def downgrade():
	pass
```

---

## 5. Testing & Auditing

`pytest` discovers tests in both `tests/` and `contrib/` (`pyproject.toml`):

```bash
pytest
ruff check .
pip-audit -r requirements.txt
```

### Test Isolation (`tests/flexaflow_fixtures.py`)
Before importing `app`, `flexaflow_fixtures.py` redirects `DATABASE_URL`, `FLEXAFLOW_EXTENSIONS_DIR`, `FLEXAFLOW_UPLOAD_DIR`, `FLEXAFLOW_BACKUP_DIR`, and `CACHE_DIR` to a temporary directory and seeds four users (`administrator`, `editor`, `author`, `contributor`) with password `PASSWORD`.

Available fixtures & helpers:
* `app` — Flask app in `TESTING=True` mode.
* `client` — Anonymous `FlaskClient`.
* `login_as(role)` — Returns an authenticated `FlaskClient` for `"administrator"`, `"editor"`, `"author"`, or `"contributor"`.
* `csrf(client)` / `token_of(client)` — Retrieves the session's `csrf_token`.
* `login(client, username, password=PASSWORD)` — Submits `/login` with CSRF.

```python
from flexaflow_fixtures import token_of

def test_editor_can_add_post(login_as):
	c = login_as("editor")
	resp = c.post("/post/add", data={
		"csrf_token": token_of(c),
		"title": "Hello",
		"content": "<p>World</p>",
		"status": "published",
	})
	assert resp.status_code == 302
```

---

## 6. Building Packages & Docker Verification

Whenever you update an extension in `contrib/extensions/` or a non-default theme in `themes/`:
```bash
python tools/build_packages.py
```
This builds `.zip` packages into `packages/` and updates `packages/index.json` with `version`, `requires_core`, and `sha256` checksums. Run `pytest` afterwards to verify `test_package_index_install_and_checksum`.

For container testing:
```bash
cp .env.example .env   # set SECRET_KEY
docker compose up -d --build
docker compose exec flexaflow python manage.py createsuperuser
curl -f http://127.0.0.1:8000/healthz
```