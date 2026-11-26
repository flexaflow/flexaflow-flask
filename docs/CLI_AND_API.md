# CLI & HTTP/JSON API Reference

---

## 1. Command-Line Interface (`manage.py`)

Run commands from the repository root (or inside the container via `docker compose exec flexaflow python manage.py ...`).

### User & Role Management
Accounts are created from the CLI so the initial administrator is never created over the web. Passwords are read interactively or via `--password-stdin`.

```bash
# Create an administrator (warns if an active admin already exists unless --force)
python manage.py createsuperuser [--username USER] [--email EMAIL] [--name "Display Name"] [--password-stdin] [--force]

# Create a user with a specific role: administrator | editor | author | contributor
python manage.py createuser --role editor [--username USER] [--email EMAIL] [--name "Display Name"] [--password-stdin]

# List all accounts (username, role, active status, 2FA status, email)
python manage.py listusers

# Change password, change role, deactivate, or reactivate
python manage.py changepassword <username> [--password-stdin]
python manage.py setrole <username> <administrator|editor|author|contributor>
python manage.py deactivate <username>
python manage.py activate <username>
```

> **Safety guards:** `setrole` and `deactivate` refuse to demote or deactivate the last active administrator.

### Database & Scheduling
```bash
# Show current Alembic schema revision
python manage.py db-current

# Upgrade database to latest revision (also runs automatically at app startup)
python manage.py db-upgrade

# Publish all scheduled posts/pages whose publish_at <= current UTC time (for cron)
python manage.py publish-due
```

### Extensions
```bash
# List installed extensions with version, active state, and load errors
python manage.py ext-list

# Install an extension .zip (and optionally activate it immediately)
python manage.py ext-install path/to/extension.zip [--activate]

# Activate / deactivate an already installed extension
python manage.py ext-activate <slug>
python manage.py ext-deactivate <slug>
```

### WordPress & Blogger Migration
CLI migration has no upload size or download count/time limit (unlike the web form, which caps uploads at 128 MB and image downloads at 150 files / 120 seconds).

```bash
# Import from WordPress WXR (.xml) or Blogger Atom (.xml / feed.atom)
python manage.py import-wordpress export.xml [--conflict skip|rename|update] [--author USERNAME] [--no-drafts] [--no-pages] [--download-media] [--settings] [--menu]
python manage.py import-blogger blog.xml      [--conflict skip|rename|update] [--author USERNAME] [--no-drafts] [--no-pages] [--download-media] [--settings]

# Export to WordPress WXR 1.2 or Blogger Atom
python manage.py export-wordpress out.xml --base-url https://example.com [--no-drafts] [--no-pages] [--no-media] [--no-menu]
python manage.py export-blogger   out.xml --base-url https://example.com [--no-drafts] [--no-pages]
```

---

## 2. Public Endpoints

| Method & Path | Response | Notes |
|---|---|---|
| `GET /healthz` | `200 {"status": "ok", "version": "1.1.0"}` or `503 {"status": "error"}` | Runs `SELECT 1` against the database; no auth. |
| `GET /sitemap.xml` | `application/xml` | Served from `sitemaps` table (generated on demand if empty). |
| `GET /feed.xml` | `application/atom+xml` | Atom feed of the 20 latest published posts. |
| `GET /category/<slug>/feed.xml` | `application/atom+xml` | Atom feed filtered by category slug. |
| `GET /robots.txt` | `text/plain` | Custom `robots_txt` setting or default rule disallowing `/admin` and `/login`. |
| `GET /uploads/<path:filename>` | File stream | Serves verified uploads with `X-Content-Type-Options: nosniff`, `Cache-Control` (30 days), and strict CSP. Non-safe types download as `application/octet-stream`. |
| `GET /ext/<slug>/static/<path:filename>` | File stream | Static assets for active extensions. |

---

## 3. Admin JSON & AJAX Endpoints

All write endpoints (`POST`, `PUT`, `DELETE`) require an active authenticated session with the listed capability and a valid CSRF token (`X-CSRF-Token` header or `csrf_token` form field).

### Settings, Menu & Slugs
* **`GET /api/settings`** (`manage_settings`) — Returns all site settings as JSON.
* **`POST /api/settings`** (`manage_settings`) — Updates site settings from JSON body; applies `theme` changes immediately.
* **`GET /api/menu`** (`manage_settings`) — Returns `settings.menu_items`.
* **`POST /api/menu`** (`manage_settings`) — Sanitizes (up to `MENU_MAX_DEPTH = 4`) and saves `menu_items` JSON array.
* **`GET /api/check_slug?slug=<slug>&type=post|page`** (`login_required`) — Returns `{"unique": bool, "suggested_slug": str}`.

### Media & Image Groups
* **`GET /admin/media?format=json&page=1&search=&group=`** (`upload_media`) — Paginated media library (`20` items/page). `group` accepts a group ID or `"none"` (ungrouped).
* **`POST /admin/media/upload`** (`upload_media`) — Multipart upload (`files[]`, optional `group_id`). Verifies and re-encodes images via Pillow, creates `thumbnail`/`medium`/`large` (+ WebP/AVIF alternates), returns `{"success": true, "files": [...]}`.
* **`POST /upload_image`** (`upload_media`) — Single-file multipart upload (`file`) used by TinyMCE and settings pickers. Returns `{"location": "/uploads/<filename>", "success": true}`.
* **`POST /admin/upload_file`** (`upload_media`) — Document/image upload for custom fields (`file`, allowed extensions: `pdf, doc, docx, xls, xlsx, ppt, pptx, csv, txt, zip, jpg, jpeg, png, webp`). Returns `{"success": true, "url": "/uploads/files/...", "name": "..."}`.
* **`GET / PUT / DELETE /admin/media/<int:media_id>`** (`manage_media`) — Read, update metadata (`alt_text`, `caption`, `title`, `description`), or delete a media item and its files.
* **`GET /admin/media/groups`** (`upload_media`) — Returns `{"groups": [{"id", "name", "description", "count"}]}`.
* **`POST /admin/media/groups`** (`upload_media`) — Create group (`{"name", "description"}`).
* **`PUT / DELETE /admin/media/groups/<int:group_id>`** (`manage_media`) — Rename or delete a group (keeps images).
* **`POST /admin/media/groups/<int:group_id>/add`** (`upload_media`) — Add `media_ids` (`[1, 2, ...]`) to a group.
* **`POST /admin/media/groups/<int:group_id>/remove`** (`manage_media`) — Remove `media_ids` from a group.

### Sections, People Directories & Autosave
* **`POST /admin/sections/reorder`** (`manage_settings`) — Body: `{"ids": [1, 2, 3]}`. Updates `Category.sort_order`.
* **`POST /admin/people/<kind>/save`** (`manage_people`) — Body: `{"original_slug": "...", "record": {...}}`. Creates or updates a directory `Entry`.
* **`POST /admin/people/<kind>/delete`** (`manage_people`) — Body: `{"slug": "..."}`.
* **`POST /admin/people/<kind>/reorder`** (`manage_people`) — Body: `{"slugs": [...]}`.
* **`GET /admin/people/<kind>/export`** (`manage_people`) — Downloads `faculty.json`-compatible JSON array.
* **`GET / POST /admin/autosave/<kind>/<int:obj_id>`** (`login_required` + edit permission) — `POST` stores JSON `{title, content, summary}` (sanitizing `content`) as the current user's autosave; `GET` returns it if newer than `updated_at`.