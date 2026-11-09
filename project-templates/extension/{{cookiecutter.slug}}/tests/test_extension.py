"""Run from the FlexaFlow checkout:  pytest path/to/{{ cookiecutter.slug }}/tests

Installs this folder as an extension, activates it and checks its pages. The FlexaFlow test fixtures
(tests/flexaflow_fixtures.py) provide the app and logged-in clients."""
import os

import extension_manager as em
from app import extensions as manager

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_public_page_and_admin_page(app, login_as):
	em.install_dir(HERE)
	manager.activate("{{ cookiecutter.slug }}")
	try:
		assert app.test_client().get("/{{ cookiecutter.slug }}/").status_code == 200
		assert login_as("administrator").get("/admin/ext/{{ cookiecutter.slug }}/").status_code == 200
		assert login_as("author").get("/admin/ext/{{ cookiecutter.slug }}/").status_code == 403
	finally:
		manager.deactivate("{{ cookiecutter.slug }}")
