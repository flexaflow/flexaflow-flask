"""{{ cookiecutter.name }}: see docs/EXTENSIONS.md in FlexaFlow for everything register(api) can do."""


def register(api):
	@api.route("/{{ cookiecutter.slug }}/")
	def home():
		return api.render("home.html", page={"title": "{{ cookiecutter.name }}"})

	@api.admin_route("/", capability="manage_settings")
	def admin_home():
		return api.render("admin.html")

	api.add_admin_menu("{{ cookiecutter.name }}", [{"label": "Overview", "url": "/admin/ext/{{ cookiecutter.slug }}/"}],
	                   capability="manage_settings")
