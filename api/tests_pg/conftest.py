"""Tests that need a REAL Postgres: SQLite doesn't enforce foreign keys and ignores FOR UPDATE / SKIP LOCKED.

Skipped unless PG_TEST_URL is set (CI sets it; see .github/workflows/ci.yml). Locally:

    podman run -d --rm --name pgtest -e POSTGRES_PASSWORD=x -e POSTGRES_DB=t -p 55432:5432 --tmpfs /var/lib/postgresql/data postgres:16
    PG_TEST_URL=postgresql+psycopg://postgres:x@127.0.0.1:55432/t python -m pytest tests_pg

The database is wiped between tests, so point it at a throwaway database.
"""

import os
import tempfile

import pytest

URL = os.environ.get("PG_TEST_URL")
if not URL:
    collect_ignore_glob = ["test_*.py"]
else:
    # Must happen before the app is imported anywhere.
    os.environ["DATABASE_URL"] = URL
    os.environ.setdefault("BCRYPT_ROUNDS", "4")
    os.environ.setdefault("STORAGE_DIR", tempfile.mkdtemp(prefix="pgtest-storage-"))
    os.environ.setdefault("JWT_SECRET", "pg-test")


@pytest.fixture(scope="session", autouse=True)
def migrated():
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config("alembic.ini"), "head")  # run pytest from api/, where alembic.ini lives


@pytest.fixture(autouse=True)
def clean_database():
    from sqlalchemy import inspect, text

    from app.db import engine

    tables = [t for t in inspect(engine).get_table_names() if t != "alembic_version"]
    with engine.begin() as c:
        c.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    yield
