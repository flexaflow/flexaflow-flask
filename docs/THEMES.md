# FlexaFlow Theme Developer Guide

A FlexaFlow theme controls the public-facing templates, custom page fields, static assets, and optional Jinja helper functions of the site.

---

## 1. Directory Structure

Themes live in `themes/<slug>/`. Any folder containing a `templates/` directory is recognized as a theme.

```text
themes/my-theme/
├── theme.json                          # Required metadata for packaging & compatibility
├── templates/                          # Required: Jinja2 HTML templates
│   ├── main/
│   │   ├── master.html                 # Base layout & default homepage/page template
│   │   ├── pager.html                  # Optional pagination partial
│   │   ├── header.html                 # Optional partial
│   │   └── footer.html                 # Optional partial
│   ├── page_templates/                 # HTML templates for custom page schemas
│   │   └── landing.html
│   ├── post.html                       # Single post view (including comments)
│   ├── category.html                   # Standard category archive
│   ├── tag.html                        # Tag archive
│   ├── search.html                     # Search results page
│   ├── section_index.html              # Custom section archive (/notices/)
│   ├── section_detail.html             # Custom section post (/notices/<slug>)
│   ├── directory_index.html            # Extension directory listing (/faculty/)
│   ├── directory_detail.html           # Extension directory profile (/faculty/<slug>)
│   ├── contact.html                    # Contact form page (optional; core has fallback)
│   ├── single-page.html                # Optional single-page template
│   └── 404.html                        # 404 and 500 error page
├── page_templates/                     # Optional: JSON custom-field schemas
│   └── landing.json
├── static/                             # Optional: CSS, JS, images -> served at /static/<slug>/
│   └── style.css
└── functions/                          # Optional: Python helpers exposed to Jinja
    └── helpers.py
```

> **Fallback behavior:** `ChoiceLoader` places `themes/default/templates/` right after your active theme, so any template you omit falls back to `default`.

---

## 2. `theme.json`

Include a `theme.json` file at the root of your theme:

```json
{
  "name": "My Theme",
  "slug": "my-theme",
  "version": "1.0.0",
  "author": "Your Name",
  "description": "A clean, responsive theme for FlexaFlow.",
  "requires_core": "1.1.0"
}
```

### Rules & Validation (`theme_manager.py`)
* `slug`: Must match `^[a-zA-Z0-9][a-zA-Z0-9_-]{1,40}$`.
* `default`: Reserved for the built-in theme and cannot be replaced or deleted.
* `requires_core`: Checked against `CORE_VERSION` (`1.1.0`). FlexaFlow refuses to install or activate a theme that requires a newer core.

---

## 3. Global Template Context & Filters

Every template receives these globals and filters automatically:

### Global Variables
| Variable | Description |
|---|---|
| `settings` | Dict of site settings: `site_title`, `site_description`, `favicon`, `logo`, `custom_analytics`, `site_domain`, `homepage`, `privacy_page`, `terms_page`, `social_links` (`facebook`, `twitter`, `instagram`), `theme`, `menu_items`. |
| `year` | Current 4-digit year (`int`). |
| `nav_sections` | List of active Section dicts (`id`, `name`, `slug`, `section_path`, `singular_name`, `description`, `date_label`, `show_time`, `fields_schema`, `post_count`). |
| `ENTRY_KINDS` | Dict of directories registered by active extensions. |
| `page_templates` | Dict of page templates loaded for the active theme. |
| `current_user` | Dict of the signed-in user, or `None`. |
| `can(capability)` | Returns `True` if the current user holds the given capability. |
| `csrf_token` | Lazy CSRF token proxy. **Important:** Only output `{{ csrf_token }}` inside forms that need it (such as comments or contact), because rendering it sets a session cookie and disables anonymous page caching for that route. |

### Helper Functions
* **`{{ seo_head() }}`**: Place inside `<head>`. Outputs `<title>`, `<meta name="description">`, `<link rel="canonical">`, Open Graph (`og:*`), Twitter Card, Atom feed link (`/feed.xml`), and JSON-LD (`BlogPosting`, `WebSite`, or `WebPage`). Reads `post`, `page`, and `settings` from the context.
* **`{{ latest_posts(category_slug=None, limit=5) }}`**: Returns recent published posts (optionally filtered by category slug) with `slug`, `title`, `excerpt`, `published_at`, `category`, `cover_image`, `meta`, and `url`.
* **`{{ post_url(post) }}`**: Returns `/<section_path>/<slug>` if the post belongs to a section, otherwise `/post/<slug>`.
* **`{{ section_href(cat_slug) }}`**: Returns `/<section_path>/` or `/category/<cat_slug>`.
* **`{{ directory_entries(kind, groups=None, include_drafts=False) }}`**: Queries extension directory entries (e.g., `directory_entries('faculty')`).
* **`{{ hook(name, *args, **kwargs) }}`**: Fires an extension action hook. Standard hooks in themes are `{{ hook("site_head") }}` (before `</head>`) and `{{ hook("site_footer") }}` (before `</body>`).
* **`{{ apply_filters(name, value, *args) }}`**: Passes a value through active extension filters.

### Filters
* **`{{ html_content | responsive }}`**: Rewrites `<img src="/uploads/...">` tags in post/page HTML to add `srcset`, `sizes`, `loading="lazy"`, `decoding="async"`, and wraps them in `<picture>` with WebP/AVIF `<source>` elements when generated thumbnails exist.
* **`{{ dt | pretty_date(with_time=False) }}`**: Formats a `datetime` or ISO date string as e.g. `20 September 2026` (or `20 September 2026, 10:00 AM`).

---

## 4. Template Reference (Route Contexts)

### `main/master.html`
Used as the base layout and directly rendered for:
1. **Home page (`/`)** when `settings.homepage` is empty or unpublished:
   * `page`: `{"title": "Home", "description": ...}`
   * `posts`: Up to 10 published posts per page, newest first.
   * `pagination`: `{"current_page", "total_pages", "pages", "prev_page", "next_page"}`
   * `categories`, `tags`, `max_tag_count`, `error`
2. **Default plain pages (`/<slug>`)**:
   * `page`: `Page.to_dict()` (`id`, `slug`, `title`, `content`, `description`, `status`, `template`, `fields`, `seo`, `created_at`, `updated_at`).

### Navigation Menu (`settings.menu_items`)
Menu items can be nested up to `MENU_MAX_DEPTH = 4` levels. Each item has:
* `label` (string)
* `url` (string, optional on parent dropdowns)
* `children` (optional list of nested items with the same shape)

```jinja
{% for item in settings.menu_items %}
  {% if item.children %}
    <div class="dropdown">
      <a href="{{ item.url or '#' }}">{{ item.label }}</a>
      <div class="dropdown-menu">
        {% for child in item.children %}
          <a href="{{ child.url }}">{{ child.label }}</a>
        {% endfor %}
      </div>
    </div>
  {% else %}
    <a href="{{ item.url }}">{{ item.label }}</a>
  {% endif %}
{% endfor %}
```

### `post.html`
Rendered for `/post/<slug>` (posts without a `section_path`; section posts redirect 301 to `/<section_path>/<slug>`).
* `post`: Dict (`id`, `slug`, `title`, `content`, `excerpt`, `category`, `tags`, `cover_image`, `meta`, `seo`, `created_at`, `updated_at`, `published_at`).
* `related_posts`: Up to 3 published posts sharing tags.
* `categories`, `tags`
* `comments_enabled` (`bool`), `comments`: list of `{author_name, body, created_at}`.

To support comments in your theme:
```jinja
{% if comments_enabled %}
<section id="comments">
  {% for c in comments %}
    <div><strong>{{ c.author_name }}</strong>: {{ c.body }}</div>
  {% endfor %}
  <form method="post" action="/post/{{ post.slug }}/comment">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <div style="position:absolute;left:-10000px" aria-hidden="true">
      <input name="website" tabindex="-1" autocomplete="off">
    </div>
    <input name="author_name" required maxlength="150">
    <input type="email" name="author_email" maxlength="255">
    <textarea name="body" required maxlength="3000"></textarea>
    <button type="submit">Post comment</button>
  </form>
</section>
{% endif %}
```

### `category.html`, `tag.html`, `search.html`
All three receive a `pager` object (`current_page`, `total_pages`, `pages`, `prev_page`, `next_page`, `query`) with 10 items per page:
* `category.html`: `category` (Category dict with `count`), `posts`, `pager`.
* `tag.html`: `tag` (`{"name", "count"}`), `posts`, `pager`.
* `search.html`: `query` (str), `results` (list of post dicts), `total_results` (int), `pager`.

### `section_index.html` & `section_detail.html`
Used when a Category has a `section_path` (e.g., `/notices/`):
* `section_index.html`: `section` (Category dict), `pagination` (`records`, `page`, `total_pages`, `total_items`, `has_prev`, `has_next`, `prev_page`, `next_page`), `active_section` (`section_path`).
* `section_detail.html`: `section`, `item` (Post dict plus `published_at` datetime, `created_at_dt`, `url`, `meta`), `active_section`.

### `directory_index.html` & `directory_detail.html`
Used by extensions calling `api.register_directory(...)`:
* `directory_index.html`: `kind`, `cfg`, `title`, `grouped` (list of `(group_label, members)`), `active_section`, `total`.
* `directory_detail.html`: `kind`, `cfg`, `record` (profile dict), `active_section`.

### `contact.html`
Rendered for `/contact` (falls back to core `templates/contact_fallback.html` if omitted).
* Context: `page`, `form` (`name`, `email`, `subject`, `message`), `errors` (list), `sent` (bool).
* Include core's form partial directly inside your theme layout:
  ```jinja
  {% extends "main/master.html" %}
  {% block content %}
  <div class="container py-4">{% include "_contact_form.html" %}</div>
  {% endblock %}
  ```

### `404.html`
Rendered for 404 (and 500) errors with `page = {"title": "404 Not Found"}` (or `"Error"`).

---

## 5. Page Templates & Custom Fields

A theme can define structured page templates using a JSON schema and a matching Jinja template:

* Schema: `themes/<theme>/page_templates/<key>.json`
* Template: `themes/<theme>/templates/page_templates/<key>.html`

`<key>` must match `^[a-z][a-z0-9_]{0,63}$` and cannot be `default`.

### Example Schema (`themes/my-theme/page_templates/landing.json`)
```json
{
  "label": "Department Landing",
  "description": "Hero banner and repeatable program cards.",
  "fields": [
    {"name": "hero_title", "label": "Hero title", "type": "text", "default": "Welcome"},
    {"name": "hero_image", "label": "Hero image", "type": "image"},
    {"name": "intro", "label": "Introduction", "type": "richtext"},
    {"name": "track", "label": "Track", "type": "select", "options": [
      {"value": "ug", "label": "Undergraduate"},
      {"value": "pg", "label": "Postgraduate"}
    ]},
    {
      "name": "programs",
      "label": "Programs",
      "type": "repeater",
      "fields": [
        {"name": "title", "label": "Program name", "type": "text"},
        {"name": "brochure", "label": "Brochure (PDF)", "type": "file"},
        {"name": "url", "label": "Details link", "type": "url"}
      ]
    }
  ]
}
```

### Supported Field Types (`page_templates.py`)
| Type | Max Length / Validation | Notes |
|---|---|---|
| `text` | 500 chars | Single-line input. |
| `textarea` | 5,000 chars | Multi-line plain text. |
| `richtext` | 200,000 chars | TinyMCE editor; sanitized via `sanitize_html()` on save. |
| `image` | 1,000 chars | Media library picker; must start with `/uploads/`, `/static/`, or `http(s)://`. |
| `file` | 1,000 chars | Document uploader (`/admin/upload_file`); same URL prefix rules. |
| `url` | 1,000 chars | Must match `^(https?://|mailto:|tel:|/|#)`. |
| `email` | 254 chars | Validated against email regex. |
| `number` | 32 chars | Integer or decimal (`^-?\d+(\.\d+)?$`). |
| `date` | 10 chars | `YYYY-MM-DD`. |
| `datetime` | 16 chars | `YYYY-MM-DDTHH:MM`. |
| `select` | 100 chars | Requires `"options": [{"value": "...", "label": "..."}]`. |
| `repeater` | Max 200 rows | Contains `"fields"` list of simple types (repeaters cannot nest). |

### Rendering Custom Fields (`themes/my-theme/templates/page_templates/landing.html`)
Your template receives `page` and `f` (the resolved field dict with defaults filled in):
```jinja
{% extends "main/master.html" %}
{% block content %}
  <h1>{{ f.hero_title }}</h1>
  {% if f.hero_image %}<img src="{{ f.hero_image }}" alt="">{% endif %}
  <div>{{ f.intro | responsive }}</div>

  <div class="row">
    {% for p in f.programs %}
      <div class="col-md-4">
        <h3>{{ p.title }}</h3>
        {% if p.brochure %}<a href="{{ p.brochure }}">Download PDF</a>{% endif %}
        {% if p.url %}<a href="{{ p.url }}">Learn more</a>{% endif %}
      </div>
    {% endfor %}
  </div>
{% endblock %}
```
> **Security note:** `richtext` values are sanitized on save, so render them with `{{ f.intro | responsive }}` or `| safe`. Render `text`/`textarea` fields normally (`{{ f.hero_title }}`) so Jinja auto-escapes them.

---

## 6. Static Assets & Theme Python Functions

### Static Files (`static/`)
On startup and when activating a theme via **Site > Themes**, `copy_theme_static_files()` copies everything from `themes/<slug>/static/` into `static/<slug>/`. Reference assets as:
```html
<link rel="stylesheet" href="/static/my-theme/style.css">
```

### Theme Functions (`functions/*.py`)
Any `.py` file inside `themes/<slug>/functions/` (except filenames starting with `__`) is imported on boot and theme activation. All public callables (not starting with `_`) are exposed globally in Jinja2 templates:

```python
# themes/my-theme/functions/custom.py
def reading_time(html_text):
    words = len((html_text or "").split())
    return max(1, round(words / 200))
```
Usage in Jinja: `{{ reading_time(post.content) }} min read`.

> **Security Warning:** `functions/*.py` runs with full server permissions. Browser theme uploads are disabled unless `FLEXAFLOW_ALLOW_EXTENSION_UPLOAD=1`, and the Themes UI shows a SHA-256 checksum and confirmation prompt before activation.

---

## 7. Packaging & Distributing a Theme

1. Ensure `theme.json` and `templates/` are present (at the `.zip` root or inside a single top-level folder `<slug>/`).
2. Do not name your theme `default`.
3. Install via **Site > Themes** (when `FLEXAFLOW_ALLOW_EXTENSION_UPLOAD=1`), add to `themes/` and run `python tools/build_packages.py` to publish to **Site > Packages**, or bundle inside an extension's `themes/<name>/` directory.