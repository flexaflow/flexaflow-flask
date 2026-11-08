# Writing a FlexaFlow extension

An extension is a folder (shipped as a `.zip`) that adds features to the CMS without changing the core.
Admins install it from **Site > Extensions**, click **Activate**, and it works immediately. Deactivating it removes
everything it added from the running site; content it created stays in the database.

## Layout

```
my-extension/
  extension.json     required: metadata and optional settings
  extension.py       def register(api): ...   (optional but usual)
  templates/         used as render_template("my-extension/page.html")
  static/            served at /ext/my-extension/static/<file>
  themes/<name>/     copied into themes/ while active (templates/, static/, page_templates/, functions/)
  ...                any other files or Python modules (import them relative to the extension folder)
```

The zip may have these at its top level or inside a single folder. See `hello-world-extension.zip`.

## extension.json

```json
{
  "name": "My Extension",
  "slug": "my-extension",
  "version": "1.0.0",
  "description": "What it does.",
  "author": "You",
  "requires_core": "1.1.0",
  "settings": [
    {"name": "api_key", "label": "API key", "type": "text", "default": "", "help": "Shown under the field."}
  ]
}
```

`slug`: lowercase letters, digits, hyphens. Setting types: text, textarea, url, email, number, checkbox, select (with `"options": [...]`).
Declaring `settings` adds a **Settings** screen automatically; read values with `api.settings.get("api_key")`.

## What `register(api)` can do

| Call | Purpose |
|---|---|
| `@api.route("/path/")` | Public page. Cannot start with a reserved word (`admin`, `static`, `post`, ...). |
| `@api.admin_route("/report", capability="manage_pages")` | Admin page at `/admin/ext/<slug>/report`, protected by a capability. |
| `api.add_admin_menu("Label", items, capability=None)` | Top-level admin menu. `items` is a list of `{"label", "url"}` (or `{"divider": True}`) or a function returning one. |
| `api.add_dashboard_widget(fn)` | HTML block on the admin dashboard. |
| `api.register_directory(key, label=, singular=, base_path=, groups=[(key, label)], public_groups=[...])` | A profile directory (like staff or team) with an admin editor and public pages at `/<base_path>/`. |
| `api.register_section_preset(key, {...})` | A starting point offered on **Site > Sections**. |
| `api.add_action(name, fn)` | Output hooks `site_head`, `site_footer`, `admin_dashboard`: return HTML to insert. Your own themes can fire others with `{{ hook('name') }}`. |
| `api.add_filter(name, fn)` | Change a value. Themes call `{{ apply_filters('name', value) }}`. |
| `api.add_template_global(name, value)` | A function or value available in every template. |
| `api.on_activate(fn)` / `api.on_deactivate(fn)` | Run when the admin switches it on or off (`fn(api)`). |
| `api.activate_theme("name")` | Make a theme from your `themes/` folder the active theme. |
| `api.register_demo_content(fn, label=, description=)` | Adds an **Install sample content** button. `fn(api, overwrite=False)` returns a message. |
| `api.render("file.html", **ctx)`, `api.static_url("x.css")` | Render your template / link your static file. |
| `api.settings.get/set/all/replace` | Per-extension settings stored in the site settings. |
| `api.create_tables(Base)` | Create tables for SQLAlchemy models you declared on `data_store.Base`. |

Capabilities (see `auth.py`): `manage_pages`, `manage_settings`, `create_posts`, `manage_people`, `manage_taxonomy`, `manage_users`, `manage_extensions`, ...

Extension code can import core modules such as `data_store` and `content_store` (for example `content_store.save_section`, `data_store.add_post`, `data_store.get_pages`).

## Rules and safety

- Extensions are Python code with full access to the site. Admins should only install extensions they trust. Only administrators can install, and uploads can be disabled with `FLEXAFLOW_ALLOW_EXTENSION_UPLOAD=0`.
- The installer rejects archives with unsafe paths or links and limits archive size.
- A theme name that already exists (and was not installed by the same extension) is never overwritten.
- If `register()` raises, the extension is not activated and the error is shown on the Extensions screen.
- Changing an extension's files: upload the new zip (same slug); an active extension reloads at once.

## Command line

```
python manage.py ext-install my-extension.zip --activate
python manage.py ext-list
python manage.py ext-activate my-extension
python manage.py ext-deactivate my-extension
```

## Themes

A theme is a separate zip: a folder with `templates/`, an optional `static/`, `page_templates/`, `functions/`, and a `theme.json`
(`name`, `slug`, `version`, `author`, `description`, `requires_core`). Upload it under **Site > Themes**.
An extension may also ship themes in its own `themes/` folder; they are installed while the extension is active.
Extensions that register a directory use the theme's `directory_index.html` / `directory_detail.html` (the default theme has fallbacks).

## Export / import

Records an extension keeps in the shared `entries` table (directories) and its settings are included in **Export > Extension data**
automatically. Extensions that create their own tables should register their own export through a hook in a later release.
