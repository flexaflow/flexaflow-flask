# Shared fixtures live in fixtures.py so extensions under contrib/ can use them too (see /conftest.py).
from flexaflow_fixtures import *  # noqa: F401,F403
from flexaflow_fixtures import PASSWORD, ROLES, csrf, login, token_of  # noqa: F401
