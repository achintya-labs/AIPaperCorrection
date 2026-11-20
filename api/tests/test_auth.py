import os

os.environ["DATABASE_URL"] = "sqlite://"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as app_db
from app.auth import hash_password
from app.main import app
from app.models import Role, User

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestSession = sessionmaker(bind=engine, expire_on_commit=False)


def override_get_db():
    with TestSession() as s:
        yield s


@pytest.fixture()
def client():
    app_db.Base.metadata.create_all(engine)
    with TestSession() as s:
        s.add(User(email="admin@x.io", password_hash=hash_password("pw"), role=Role.admin))
        s.add(User(email="ta@x.io", password_hash=hash_password("pw"), role=Role.ta))
        s.commit()
    app.dependency_overrides[app_db.get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
    app_db.Base.metadata.drop_all(engine)


def login(client, email, password="pw"):
    return client.post("/auth/login", json={"email": email, "password": password})


def test_login_and_me(client):
    assert login(client, "ta@x.io").status_code == 200
    assert client.get("/auth/me").json()["email"] == "ta@x.io"


def test_bad_password(client):
    assert login(client, "ta@x.io", "nope").status_code == 401


def test_unauthenticated(client):
    assert client.get("/auth/me").status_code == 401


def test_ta_cannot_create_users(client):
    login(client, "ta@x.io")
    r = client.post("/auth/users", json={"email": "n@x.io", "password": "long-enough-pw"})
    assert r.status_code == 403


def test_admin_creates_ta(client):
    login(client, "admin@x.io")
    r = client.post("/auth/users", json={"email": "n@x.io", "password": "long-enough-pw"})
    assert r.status_code == 201 and r.json()["role"] == "ta"
