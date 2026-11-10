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
| `api.create_tables(Base)` | Create tables for SQLAlchemy models (or Core `Table`s) you declared on `data_store.Base`. |
| `api.register_export(export_fn, import_fn)` | Put your own tables into **Site > Export**. `export_fn()` returns JSON-able data; `import_fn(data, overwrite)` stores it and returns a count. If the extension is not active when a package is imported, the data waits and is applied on activation. |

Capabilities (see `auth.py`): `manage_pages`, `manage_settings`, `create_posts`, `manage_people`, `manage_taxonomy`, `manage_users`, `manage_extensions`, ...

Extension code can import core modules such as `data_store` and `content_store` (for example `content_store.save_section`, `data_store.add_post`, `data_store.get_pages`).

## Rules and safety

- Extensions are Python code with full access to the site. Admins should only install extensions they trust. Only administrators can install, and browser uploads are off by default and enabled with `FLEXAFLOW_ALLOW_EXTENSION_UPLOAD=1`.
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
automatically. Extensions that create their own tables register `api.register_export(...)` (see `contrib/extensions/newsletter`).

## Bundled extensions and themes

`contrib/extensions/` holds first-party examples that are also useful: **newsletter** (signup form, subscriber list, CSV, export hook),
**backups** (create and download full-site packages) and **seo-audit** (a report of weak titles, missing descriptions and images without alt text).
`themes/minimal` is a second public theme.

## Packages index

`python tools/build_packages.py` zips every extension in `contrib/extensions/` and every theme except `default`, and writes `packages/index.json`
with each package's version, the core version it needs (`requires_core`) and the SHA-256 of its zip. **Site > Packages** lists them, shows whether
each is installed, up to date or incompatible, and installs one after checking the hash. Installing never activates; you still read the trust
warning on the Extensions/Themes screen. To publish your own packages, add the folder under `contrib/extensions/` (or `themes/`) and rebuild.

## Starting a new extension and testing it

```bash
pip install cookiecutter
cookiecutter project-templates/extension
cp -r my-extension contrib/extensions/
pytest contrib/extensions/my-extension/tests
```

The generated test installs the folder, activates it and checks its public and admin pages using the same fixtures as the core tests
(`tests/flexaflow_fixtures.py`: `app`, `client`, `login_as("administrator" | "editor" | "author" | "contributor")`).
