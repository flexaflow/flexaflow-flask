# How FlexaFlow Works (Architecture & Internals)

FlexaFlow is a modular Flask CMS backed by SQLAlchemy and Alembic, with a Jinja2 theme engine and an in-process extension runtime. This document explains how requests, data, templates, caching, and security work internally.

---

## 1. High-Level Request Lifecycle

```text
HTTP Request
   │
   ▼
WSGI (Flask / Waitress / Gunicorn + optional ProxyFix)
   │
   ├── before_request hooks
   │     1. csrf_protect          ── validates X-CSRF-Token / form csrf_token on writes
   │     2. _publish_scheduled    ── promotes due "scheduled" posts/pages (<= 1x / 30s)
   │     3. _extensions_before    ── syncs active extensions across workers (<= 1x / 2s)
   │     4. _ext_dispatch         ── matches dynamic extension routes (Werkzeug Map)
   │     5. _page_cache_lookup    ── returns cached HTML + ETag for anonymous GETs
   │
   ├── Flask Blueprints & Core Views
   │     ├── auth        (/login, /logout, /admin/profile, /admin/users...)
   │     ├── admin       (/admin, /admin/settings, /admin/menu, /admin/tags-and-catagories...)
   │     ├── api         (/api/settings, /api/check_slug, /api/menu)
   │     ├── media       (/admin/media..., /upload_image, /uploads/<file>)
   │     ├── editor      (/page/add|edit|delete|preview, /post/add|edit|delete)
   │     ├── public      (/healthz, /, /post/<slug>, /category/..., /tag/..., /search, /<slug>, /sitemap.xml)
   │     ├── content     (/admin/sections..., /admin/people/..., /admin/posts..., /<section_path>/)
   │     ├── features    (/admin/history, /admin/autosave, /admin/review, /contact, /admin/comments, /admin/redirects, /feed.xml, /robots.txt)
   │     └── init() apps (extension_views, theme_views, package_views, migrate_views, package_index)
   │
   └── after_request hooks
         1. _security_headers     ── X-Content-Type-Options, X-Frame-Options, Referrer-Policy, CSP, HSTS
         2. _page_cache_store     ── caches anonymous 200 HTML responses; clears cache on writes
```

### Entry Points
* **`app.py`**: Creates the `Flask` app, loads `.env`, initializes logging, security headers, Jinja loaders, page cache, extension manager, and registers all blueprints.
* **`server.py`**: Runs `app` via Waitress (`HOST`, `PORT`) for development and simple deployments.
* **`Dockerfile`**: Runs `gunicorn app:app --bind 0.0.0.0:8000 --workers 3` with `FLEXAFLOW_BEHIND_PROXY=1`.
* **`manage.py`**: CLI tool for user management, database migrations, scheduled publishing, extension management, and WordPress/Blogger migration.

---

## 2. Blueprint & Module Wiring

Core routes under `blueprints/` (`auth.py`, `admin.py`, `api.py`, `editor.py`, `media.py`, `public.py`) are imported at the bottom of `app.py` via `blueprints.register(app)`.

* **Shared namespace**: Each `blueprints/*.py` module pulls shared helpers and objects from `sys.modules["app"]` at import time (`pyproject.toml` ignores Ruff `F821` for `blueprints/*.py` because of this).
* **Legacy endpoint aliases**: `blueprints/__init__.py` registers unprefixed endpoint aliases (e.g., `url_for("view_post")` alongside `url_for("public.view_post")`) so older themes and extensions keep working.
* **Standalone feature modules**: Newer subsystems (`content_views.py`, `features.py`, `extension_views.py`, `theme_views.py`, `package_views.py`, `migrate_views.py`, `package_index.py`) are self-contained blueprints or `init(app, ...)` registrars.

---

## 3. Data Layer & Migrations

Persistence is managed in `data_store.py` through SQLAlchemy (`SessionLocal`, `Base`, and the singleton `db_manager`).

* **Databases**: SQLite (`sqlite:///cms.db`) by default, or MySQL when `FLASK_ENV=production` and `DATABASE_URL` is configured.
* **Automatic migrations**: `DatabaseManager.__init__()` runs Alembic (`migrations/versions/0001` through `0005`) to `head` at startup, then encrypts any legacy plaintext 2FA secrets.

### Core Tables
| Model / Table | Purpose |
|---|---|
| `User` (`users`) | Accounts, password hashes, roles, encrypted `two_fa_secret`, `two_fa_recovery` hashes, `session_version`. |
| `LoginAttempt` (`login_attempts`) | DB-backed rate limiting / progressive throttling for login, 2FA, contact form, comments, and extensions. |
| `Page` (`pages`) | Static pages, `template` key, JSON `fields` (custom fields), `publish_at`, JSON `seo`. |
| `Post` (`posts`) | Blog/section posts, `category_id`, `cover_image`, JSON `meta` (section fields), `publish_at`, JSON `seo`. |
| `Category` (`categories`) | Categories and **Sections** (`section_path`, `singular_name`, `date_label`, `show_time`, `fields_schema`, `sort_order`). |
| `Tag` (`tags`) & `post_tags` | Many-to-many post tags. |
| `SiteSetting` (`site_settings`) | Key/JSON-value store for site title, menu (`menu_items`), active theme (`theme`), `active_extensions`, extension settings (`ext_<slug>`), SMTP, etc. |
| `Media` (`media`), `MediaGroup`, `MediaGroupItem` | Uploaded image metadata and named collections (groups). (`MediaLibrary` also exists for package compatibility.) |
| `Entry` (`entries`) | Generic directory records (e.g., Faculty, Staff) registered by extensions, stored with `kind`, `slug`, `group`, `status`, `sort_order`, and JSON `data`. |
| `Revision` (`revisions`) | Edit history (up to `MAX_REVISIONS = 50` per item) and per-user editor autosaves (`is_autosave=True`). |
| `Redirect` (`redirects`) | 301/302 path redirects (auto-created when a page slug changes, or added manually). |
| `ContactMessage` (`contact_messages`) | Submissions from `/contact`. |
| `Comment` (`comments`) | Reader comments (`pending`, `approved`, `spam`). |
| `Sitemap` (`sitemaps`) | Cached XML sitemap generated by `generate_and_store_sitemap()`. |

### Full-Text Search
`search_posts()` in `data_store.py` uses:
* **SQLite**: FTS5 virtual table `search_index` kept in sync via `INSERT`/`UPDATE`/`DELETE` triggers (migration `0003`).
* **MySQL / MariaDB**: `FULLTEXT` indexes on `posts(title, content)` and `pages(title, content)`.
* **Fallback**: `ILIKE` queries when neither full-text engine is available, plus tag-name matching.

---

## 4. Template Resolution Order

`app.py` configures a Jinja2 `ChoiceLoader` (`_theme_loader(theme_name)`) that searches directories in this exact order:

1. `templates/` — Core admin templates and shared partials (`_admin_layout.html`, `_contact_form.html`, etc.).
2. `_ExtensionTemplateLoader()` — Resolves `"<extension-slug>/<file>"` from `extensions/<slug>/templates/` while that extension is active.
3. `themes/<THEME_NAME>/templates/` — The currently active theme.
4. `themes/default/templates/` — Built-in fallback if the active theme omits a template.

> Because `templates/` is checked first, themes cannot override core admin templates. `contact.html` explicitly falls back to `contact_fallback.html` if a theme omits it.

---

## 5. Page Caching & ETag Flow

Public page caching uses `Flask-Caching` (`FileSystemCache` in `instance/cache` by default so multiple Gunicorn workers share the cache, or `RedisCache` across multiple machines).

* **When a page is cached**: `GET` requests, `CACHE_TYPE != "NullCache"`, no session cookie present, path not in `_NO_CACHE_PREFIXES`, `len(request.args) <= 2`, HTTP `200`, `text/html`, no `Set-Cookie` header, and `not session` / `not session.modified`.
* **Lazy CSRF (`_LazyCsrf`)**: `csrf_token` in templates is a lazy proxy. Rendering `{{ csrf_token }}` in a template (such as `/contact` or a post with comments enabled) writes `session["csrf_token"]`, which automatically prevents that response from being cached so every visitor gets their own token.
* **Cache invalidation**: Any successful (`< 400`) state-changing request (`POST`, `PUT`, `PATCH`, `DELETE`) to `_CLEARING_PREFIXES` (`/admin`, `/page/`, `/post/`, `/api`, `/upload_image`, `/category`, `/tag`) clears the page cache.
* **Conditional requests**: Cached responses include an `ETag` and support `304 Not Modified`.

---

## 6. Extension Synchronization Across Workers

When running multiple workers (e.g., Gunicorn `--workers 3`), activating or deactivating an extension in one worker updates `SiteSetting["active_extensions"]`.

* On every non-static request, `_extensions_before()` checks `_ext_sync_needed()` (at most once every **2 seconds** per worker).
* When `extensions.sync()` detects a change between `self.registrations` and `active_extensions` in the DB, it unloads deactivated extensions, loads newly activated ones, rebuilds the Werkzeug `Map` for `_ext_dispatch()`, updates `content_store.ENTRY_KINDS` and `EXTENSION_PRESETS`, refreshes theme static files, and clears the Jinja template cache.

---

## 7. Security & Content Pipeline

* **Authentication & RBAC (`auth.py`)**: Four roles (`administrator`, `editor`, `author`, `contributor`) mapped to granular capabilities in `CAPABILITIES`. Routes enforce `@login_required` and `@capability_required("...")`. `current_user()` re-reads the user row and compares `session["sv"]` to `user.session_version` on every request so role changes, deactivations, and password resets invalidate existing sessions immediately.
* **Encryption at rest (`crypto_utils.py`)**: `EncryptedString` encrypts `users.two_fa_secret` (and SMTP `mail_password`) with Fernet (`FLEXAFLOW_2FA_KEY` or `instance/twofa.key`).
* **CSRF protection**: `csrf_protect` checks `X-CSRF-Token` or `form["csrf_token"]` via `secrets.compare_digest` on all non-safe HTTP methods.
* **HTML sanitization (`migrate_utils.py`)**: `sanitize_html()` runs an allow-list HTML parser on post/page bodies, `richtext` custom fields, autosaves, and WordPress/Blogger imports. Scripts, `on*` event attributes, unsafe CSS, and `javascript:` URLs are stripped; `<iframe>` is restricted to `EMBED_HOSTS` (YouTube, Vimeo, Google Maps, Facebook, Spotify, SoundCloud).
* **Image verification (`images.py` & `blueprints/media.py`)**: `store_verified_image()` enforces `MAX_IMAGE_PIXELS = 50_000_000`, verifies via Pillow (`PNG`, `JPEG`, `GIF`, `WEBP` only—no SVG), re-encodes to strip EXIF/GPS and trailing bytes, derives file extension/MIME from the decoded format, and serves `/uploads/<file>` with `X-Content-Type-Options: nosniff` and a strict `Content-Security-Policy`.
* **Request size limits**: Global `MAX_CONTENT_LENGTH` is `16 MB`. `MigrationRequest` raises this to `128 MB` exclusively for signed-in users posting to `/admin/migrate/import`.