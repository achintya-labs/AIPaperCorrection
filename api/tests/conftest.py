import os

# Hashing at production strength dominates the run time (every test makes users and logs in), so the tests
# use bcrypt's minimum cost. This must be set before the app is imported, which conftest guarantees.
os.environ.setdefault("BCRYPT_ROUNDS", "4")
# The existing suite exercises password sign-in; SSO has its own tests (test_sso.py) that switch the mode on.
os.environ.setdefault("AUTH_MODE", "local")
