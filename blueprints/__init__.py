"""Route groups of the CMS, one Blueprint per area."""


def register(app):
	"""Register every blueprint. Called at the end of app.py, after all shared helpers exist."""
	from . import admin, api, auth, editor, media, public
	for module in (auth, admin, api, media, editor, public):
		app.register_blueprint(module.bp)

	# Keep the old endpoint names (url_for("view_post"), url_for("login")...) working for third-party
	# themes and extensions written before the split.
	for rule in list(app.url_map.iter_rules()):
		if "." in rule.endpoint and rule.endpoint.split(".")[0] in {"auth", "admin", "api", "media", "editor", "public"}:
			legacy = rule.endpoint.split(".", 1)[1]
			if legacy not in app.view_functions:
				app.add_url_rule(rule.rule, endpoint=legacy, view_func=app.view_functions[rule.endpoint],
				                 methods=sorted(rule.methods - {"HEAD", "OPTIONS"}), defaults=rule.defaults)
