# Sections, Content Workflows & Migration Internals

This document covers FlexaFlow's content subsystems in `content_store.py`, `features.py`, `site_package.py`, and `migrate.py`.

---

## 1. Custom Sections (`content_store.py`)

A **Section** is a `Category` row with a non-empty `section_path` (e.g., `notices`, `news`, `events`).

* **Routing**:
  * `/<section_path>/` (and `/<section_path>` if no Page uses that slug) renders `section_index.html` (`POSTS_PER_PAGE = 8`).
  * `/<section_path>/<post_slug>` renders `section_detail.html`.
  * Opening a section post at `/post/<slug>` or `/category/<cat_slug>` issues a **301 permanent redirect** to the section URL.
* **Custom Fields**: Each section stores a `fields_schema` JSON list (validated via `page_templates._validate_schema`, excluding `repeater`). Post values are saved in `Post.meta` JSON and rendered via `section_detail.html`.
* **Reserved Paths**: `section_path` cannot collide with `RESERVED_PATHS`, existing pages, or active extension directory `base_path`s.
* **Presets**: Core provides `notice`, `news`, and `event` (`CORE_SECTION_PRESETS`). Active extensions can add more via `api.register_section_preset()`.

---

## 2. People Directories (`Entry` model)

Extensions can register structured profile directories via `api.register_directory(...)`.

* All directory items share the `entries` table keyed by `(kind, slug)` with `group`, `status` (`published`/`draft`), `sort_order`, and a JSON `data` blob.
* `content_store.normalize_entry()` sanitizes URLs (`SAFE_URL_RE`, `SAFE_FILE_RE`), caps list lengths (`emails` 10, `profiles` 30, `biography` 30, `education` 30, `research_interests` 50, `publications` 500), and forces safe defaults.
* Records can be reordered, filtered, exported to JSON, and imported from JSON (`faculty.json` format) at `/admin/people/<kind>`.

---

## 3. Editorial Workflows (`features.py`)

### Scheduled Publishing
* Posts and pages support a `scheduled` status with a UTC `publish_at` timestamp (converted in the browser from local time using `tz_offset`).
* `features.publish_due()` runs at most once every 30 seconds inside `before_request` (and immediately via `python manage.py publish-due`), switching due items to `published`, regenerating the sitemap, and clearing the page cache.

### Review Queue
* Users without `publish_posts` (Contributors) can submit posts with `status = "pending"`.
* `notify_review_requested()` emails active Administrators and Editors when SMTP is configured.
* Moderators (`moderate` capability) approve or return items as drafts at `/admin/review` (with an optional feedback note emailed to the author).

### Revisions & Autosave
* Every title/content/summary edit on a Page or Post writes a `Revision` row (`MAX_REVISIONS = 50` non-autosave revisions kept per item).
* Restoring a revision saves the replaced version first so nothing is lost.
* Every 20 seconds, the editor posts changes to `/admin/autosave/<kind>/<id>` (or `localStorage` for unsaved new items). Publishing or saving removes old autosaves for that item.

### Redirects, Contact Form & Comments
* **Redirects**: Changing a page slug in `update_page_by_slug()` automatically creates a `301` `Redirect` from `/<old>` to `/<new>` and cleans up loops. Stored redirects are checked inside the `404` error handler.
* **Contact Form (`/contact`)**: Protected by CSRF, a 3-second minimum submission time (`session["contact_started"]`), a hidden `website` honeypot field, and DB rate limiting (`5` submissions per IP per `600` seconds). Submissions are stored in `contact_messages` and optionally emailed via `mailer.py`.
* **Comments (`/post/<slug>/comment`)**: Off by default (`comments_enabled` in `site_settings`). Protected by CSRF, honeypot, and DB rate limiting (`5` per `600` seconds). Submissions by visitors enter as `pending` until approved at `/admin/comments`; comments from `moderate`-capable users are auto-approved.

---

## 4. Site Packages (`site_package.py`)

**Site > Export** creates a `flexaflow-package` `.zip` (`FORMAT_VERSION = 1`) containing:
* `manifest.json`: Core version, timestamp, included parts, `active_extensions`, `active_theme`.
* `content.json`: Categories/sections, pages, posts (including tags, `cover_image`, `meta`, `fields`).
* `settings.json`: Site settings (excluding `ext_*`, `active_extensions`, and `theme`).
* `extension_data.json`: `entries` rows, `ext_*` settings, and custom extension payloads from `api.register_export()`.
* `media.json` + `uploads/`: Media table rows and uploaded files.
* `packages/extensions/<slug>/` & `packages/themes/<slug>/`: Optional installed packages.

> **Security & Safety:** Users, password hashes, and 2FA secrets are **never** exported. Imported extensions and themes remain inactive unless **Also switch on the same extensions and theme** (`restore_state`) is checked.

---

## 5. WordPress & Blogger Migration (`migrate.py`, `migrate_utils.py`)

* **Format detection**: `detect_format()` distinguishes FlexaFlow XML, WordPress WXR, and Blogger Atom (`feed.atom` / backup `.xml`).
* **XML safety**: `parse_xml_bytes()` rejects files above `200 MB` and rejects any `<!ENTITY` declaration (preventing XXE / Billion Laughs).
* **Content conversion**: `wp_to_html()` strips Gutenberg `<!-- wp:... -->` markers, converts `[caption]` to `<figure><figcaption>`, strips `[gallery]` and unknown shortcodes, applies `_autop()` paragraphing if block tags are absent, embeds standalone YouTube URLs, and runs `sanitize_html()`.
* **Media downloading (`safe_fetch`)**: Only downloads from hosts belonging to the exported site (or known Google/Blogger image CDNs), blocks non-global/private IPs (SSRF protection) by pinning the socket connection directly to the validated IP, caps redirects at 3, restricts MIME types to `image/*` and `application/pdf` (`25 MB` max), and re-verifies downloaded images through `images.store_verified_image()`.