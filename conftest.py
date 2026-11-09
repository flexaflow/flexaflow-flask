"""Makes the shared test fixtures (isolated database, logged-in clients) available to every test in the
repo, including tests that ship inside extensions under contrib/extensions/*/tests."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests"))
pytest_plugins = ["flexaflow_fixtures"]
