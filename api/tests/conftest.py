import os

# Hashing at production strength dominates the run time (every test makes users and logs in), so the tests
# use bcrypt's minimum cost. This must be set before the app is imported, which conftest guarantees.
os.environ.setdefault("BCRYPT_ROUNDS", "4")
