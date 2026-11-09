# FlexaFlow CMS

A small, hackable content management system for Flask. Pages, posts, sections, menus, media, multi-user roles,
themes and extensions, with a WordPress-style admin.

- **Content**: pages and posts with a TinyMCE editor, drafts, scheduled publishing, revisions with restore, autosave, an approval queue, comments with moderation, per-page SEO fields, RSS/Atom feeds, redirects.
- **People**: four roles (administrator, editor, author, contributor), per-user two-factor authentication with recovery codes.
- **Site**: themes, page templates with custom fields, extensions, WordPress/Blogger import and export, full-site export/import, contact form with inbox.
- **Operations**: Alembic migrations, `/healthz`, response caching, Docker image, tests and CI.

> Status: version 1.1.x. It has a test suite, but review the [security notes](docs/SECURITY.md) and test your own setup before exposing it to the internet.

## Quick start

```bash
git clone https://github.com/flexaflow/flexaflow-flask.git
cd flexaflow-flask
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                    # then edit it
python manage.py createsuperuser                        # the first administrator is created here, never on the web
python server.py                                        # http://127.0.0.1:5000  (admin at /login)
```

With Docker:

```bash
cp .env.example .env        # set SECRET_KEY
docker compose up -d --build
docker compose exec flexaflow python manage.py createsuperuser
```

For a real deployment (gunicorn, nginx, HTTPS, backups, upgrades) read [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Configuration

Set these in `.env` or the environment.

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | generated into `instance/secret_key` | Signs sessions. Set it explicitly in production. |
| `DATABASE_URL` | `sqlite:///cms.db` | SQLAlchemy URL. MySQL is supported with `FLASK_ENV=production`. |
| `SESSION_COOKIE_SECURE` | `0` | Set `1` behind HTTPS (secure cookies and HSTS). |
| `FLEXAFLOW_BEHIND_PROXY` | `0` | Set `1` behind nginx or a load balancer to trust `X-Forwarded-*`. |
| `FLEXAFLOW_2FA_KEY` | generated into `instance/twofa.key` | Fernet key that encrypts 2FA secrets. Keep it apart from database backups. |
| `FLEXAFLOW_ALLOW_EXTENSION_UPLOAD` | `1` | `0` turns off extension and theme zip uploads in the browser. |
| `CACHE_TYPE` | `FileSystemCache` | `SimpleCache`, `FileSystemCache`, `RedisCache` or `NullCache` (see docs/DEPLOYMENT.md). |
| `CACHE_REDIS_URL` | empty | Used with `CACHE_TYPE=RedisCache`. |
| `CACHE_TIMEOUT` | `300` | Seconds a cached public page may be reused. |
| `FLEXAFLOW_IMAGE_FORMATS` | `webp` | Extra formats generated for uploaded images (`webp`, `avif`; skipped if Pillow lacks support). |
| `STATIC_MAX_AGE` | `86400` | Cache lifetime for static files, in seconds. Uploads are cached for 30 days. |
| `FLEXAFLOW_UPLOAD_DIR` | `uploads` | Where uploaded files are stored. |
| `LOG_LEVEL` | `INFO` | Python log level. |
| `PORT`, `HOST` | `5000`, `127.0.0.1` | Used by `server.py`. |

## Screenshots

Add yours to `docs/screenshots/` and link them here.

## Scheduled publishing

Scheduled posts are published the first time any request arrives after their time (checked at most every 30 seconds per process). On a quiet site, or to be exact, run `python manage.py publish-due` from cron every minute.

## Notes

- Public pages are cached only for anonymous visitors. A page that prints a CSRF token (the contact form, and post pages when comments are on) is never cached.
- Comments are off by default; turn them on under Site > Email, SEO & comments. They wait for approval.
- Add `{{ seo_head() }}` to the `<head>` of your own theme to get titles, canonical links, Open Graph tags, the feed link and JSON-LD. Wrap post or page body output in `|responsive` for srcset, lazy loading and WebP.

## Backup and upgrade

Back up three things: the database (`DATABASE_URL`; for SQLite the `.db` file), the `uploads/` folder, and `instance/` (secret key and 2FA key). **Site > Export** also produces a content-only package.

To upgrade: back up, pull the new code, `pip install -r requirements.txt`, restart. Database migrations run automatically at startup; `python manage.py db-upgrade` runs them by hand and `db-current` shows the revision.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
ruff check .
```

## Accounts (multi-user, role based)

Roles, from most to least access (WordPress-style):

| Role | Can |
|---|---|
| Administrator | Everything, including users and site settings |
| Editor | Manage all pages, posts, categories/tags and media |
| Author | Write and publish their own posts |
| Contributor | Write their own posts; submissions are saved as drafts |

Accounts are created from the command line, so the first administrator is never
created over the web:

```bash
python manage.py createsuperuser                    # first administrator (prompts for password)
python manage.py createuser --role editor --username ayesha --email a@example.edu
python manage.py listusers
python manage.py setrole ayesha author
python manage.py changepassword ayesha
python manage.py deactivate ayesha
```

Administrators can add and manage other users under **Users** in the admin.
Passwords are never taken as command-line arguments; use `--password-stdin` for scripts.

## Page templates and custom fields

A theme can define page templates: a JSON field schema in
`themes/<theme>/page_templates/<key>.json` and a Jinja template in
`themes/<theme>/templates/page_templates/<key>.html`. Editors pick a template on the
page form and fill its fields. Field types: text, textarea, richtext, image, file, url,
email, number, date, datetime, select and repeater. Templates read values as `f`, for example
`{{ f.hero_title }}` or `{% for p in f.programs %}`.

## Extensions (like WordPress plugins)

The core is a general CMS: pages, posts, sections, menus, media, users, search and themes.
Anything specific to one organisation lives in an **extension**.

1. **Site > Extensions**, choose the extension `.zip`, **Upload & install**.
2. Click **Activate**. No restart is needed. Its menus, pages, directories and theme appear immediately.
3. Some extensions offer **Settings** and **Install sample content**.
4. **Deactivate** switches it off (your content is kept). **Delete** removes its files from the server.

From the command line: `python manage.py ext-install file.zip --activate`, `ext-list`, `ext-activate <slug>`, `ext-deactivate <slug>`.

Included with this download (in separate zips, like WordPress plugins and themes):

- `briu-cse-extension.zip` – Faculty and Officer & Staff directories, the *Academics* admin menu and the Notices/News/Events sections. No sample data.
- `briu-cse-theme.zip` – the department theme (upload under **Site > Themes**, then Activate).
- `hello-world-extension.zip` – a tiny example to copy when writing your own.

Both extensions and themes declare their own `version` and `requires_core`; FlexaFlow refuses to install or activate one that needs a newer core.

## Themes

**Site > Themes** lists installed themes with version and compatibility. Upload a theme `.zip` (a folder with `templates/` and a `theme.json`), click **Activate**, or delete unused ones. Themes may include `page_templates/` (custom page fields), `static/` and `functions/`.

## Export and import

**Site > Export** builds one `.zip` and you choose what goes in: content (pages, posts, sections, tags), site settings, **extension data** (for example faculty and staff profiles and extension settings), media files, and optionally the installed extension and theme packages.

**Site > Import** takes that zip (old XML exports still work). You choose which parts to import. Extensions and themes found in the file are installed but left **inactive**, so an import never conflicts with the running site; tick "switch on the same extensions and theme" to restore them as they were. Existing items with the same address are kept unless you tick Overwrite. Users and passwords are never exported.

Writing one is covered in [docs/EXTENSIONS.md](docs/EXTENSIONS.md). Extensions are Python code that
runs with full access to the site, so install only ones you trust. Set `FLEXAFLOW_ALLOW_EXTENSION_UPLOAD=0`
to disable browser uploads (administrators only can use them anyway).

### Sections: choose where content is published

A *section* is a category with its own web address. In **Site > Sections & addresses** the admin picks:

- the address (`notices` gives `/notices/` and `/notices/<post>`; `events` gives `/events/`),
- the name used on the button ("New notice"),
- the label of the date ("Notice date", "Event starts") and whether a time is asked for,
- extra fields for every post in it: short text, long text, **file (PDF upload)**, image, link, date, date & time.

Each section adds a "New ..." entry to the **Publish** menu and a card on the dashboard. Removing a
section keeps its posts; they stay reachable at `/post/<slug>`. Posts in a section that are opened at
`/post/<slug>` are redirected to their section address.

### Finding and maintaining posts

**Publish > All posts & search** (also the search box in the top bar) lists every post, 20 per page, with search
(title, address, text), filters (section/category, status, author, date range) and sorting. Tick posts to
publish, move to draft, change section/category or delete many at once. Contributors only see and change their own posts.
The Pages tab on the dashboard and the People editors have their own quick search and filters.

### People directories

An extension can register profile directories (the BRIU CSE one adds **Faculty** at `/faculty/` and
**Officer & Staff** at `/officer-and-staff/`). They appear under **People** in the admin menu. Each card is saved on its own;
cards can be reordered, hidden as drafts, deleted, imported from and exported to JSON.

### Page templates

The active theme can provide page templates with custom fields (the BRIU CSE theme adds *Department landing*,
*Academic program* and *Documents* pages). They are listed under **Pages > + New ...** in the admin menu.

## Security

See [docs/SECURITY.md](docs/SECURITY.md) for the full list. In short: CSRF on every state-changing request, editor HTML sanitized on save, per-IP and per-username login throttling stored in the database, encrypted 2FA secrets with recovery codes, security headers, and a security event log.

### Switching theme

Admin > Settings > Theme lists every folder in `themes/` that has a `templates/` directory.
Saving applies the theme immediately, so there is no restart needed. Themes shipped by an extension
are installed when it is activated (and the site returns to `default` if it is deactivated).


## WordPress and Blogger

**Site > WordPress & Blogger** moves a site in either direction.

**Import**

- WordPress: *Tools > Export > All content* gives a `.xml` file (WXR). Blogger: *Settings > Manage blog > Back up content* gives a `.xml` file (a Google Takeout `feed.atom` also works). The source is detected automatically; uploading such a file on the normal Import page sends you here.
- Posts, pages, categories, tags, dates, drafts and featured images are brought over. WordPress block-editor markers, `[caption]` and `[gallery]` shortcodes and plain-text paragraphs are converted to clean HTML; YouTube links on their own line become embeds. A post gets its first WordPress category, extra categories and all Blogger labels become tags; WordPress child pages become ordinary pages.
- Optional: copy the images from the old site into the media library (only from the old site's own address, never to private or internal addresses), use the old title and description, replace the navigation menu with the WordPress menu.
- If an address already exists you choose: keep yours, keep both (the new one gets `-2`), or replace yours.
- All imported HTML is cleaned with an allow-list: scripts, event handlers and `javascript:` links are removed; iframes are kept only for YouTube, Vimeo, Google Maps, Spotify and SoundCloud.
- Not imported: comments, users and passwords, custom post types, theme and plugin data.

**Export**

- WordPress: a WXR 1.2 file for *Tools > Import > WordPress* (posts, pages, categories, tags, drafts, featured images, media library, navigation menu).
- Blogger: an Atom backup for *Settings > Manage blog > Back up content > Restore* (posts, pages, labels, drafts).
- Image and link addresses such as `/uploads/a.jpg` are written as full addresses using the site address you enter, so WordPress and Blogger can fetch them. The site must be reachable from the internet while they import.
- FlexaFlow-only data (page templates, extension records, section fields) is not part of those formats; use *Site > Export* for a complete backup.

**Command line** (no size or time limit, recommended for big sites; the web form accepts files up to 128 MB and copies the first 150 images):

```bash
python manage.py import-wordpress export.xml --download-media --author admin
python manage.py import-blogger blog-backup.xml --conflict rename
python manage.py export-wordpress wordpress.xml --base-url https://example.com
python manage.py export-blogger blogger.xml --base-url https://example.com
```

Options: `--conflict skip|rename|update`, `--no-drafts`, `--no-pages`, `--settings`, `--menu`, `--download-media`.

## License

FlexaFlow is released under the GNU General Public License v2.0. See `LICENSE`.

## Acknowledgments

- [Mashiur Rahman](https://github.com/01one)
- Built with [Flask](https://flask.palletsprojects.com/), [SQLAlchemy](https://www.sqlalchemy.org/), [Alembic](https://alembic.sqlalchemy.org/), [TinyMCE](https://www.tiny.cloud/) and [Bootstrap](https://getbootstrap.com/).

