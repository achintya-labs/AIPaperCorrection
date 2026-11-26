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


def _dep_calls(dependant) -> set:
    calls = {dependant.call}
    for sub in dependant.dependencies:
        calls |= _dep_calls(sub)
    return calls


def test_every_route_has_expected_auth_dependency(client):
    from fastapi.routing import APIRoute
    from app.auth import current_user, require_admin

    public = {
        ("GET", "/health"),
        ("POST", "/auth/login"),
        ("POST", "/auth/logout"),
        ("GET", "/auth/providers"),
        ("GET", "/auth/google/login"),
        ("GET", "/auth/google/callback"),
    }
    admin_only = {
        ("POST", "/auth/users"),
        ("GET", "/auth/users"),
        ("PUT", "/auth/users/{user_id}/password"),
        ("PATCH", "/auth/users/{user_id}"),
        ("DELETE", "/auth/users/{user_id}"),
        ("POST", "/exams"),
        ("PATCH", "/exams/{exam_id}"),
        ("POST", "/exams/{exam_id}/rubrics"),
        ("POST", "/exams/{exam_id}/rubric.pdf"),
        ("DELETE", "/exams/{exam_id}"),
        ("POST", "/students"),
        ("GET", "/students"),
        ("PUT", "/submissions/{submission_id}/assignment"),
        ("DELETE", "/submissions/{submission_id}"),
        ("POST", "/exams/{exam_id}/imports"),
        ("POST", "/exams/{exam_id}/imports/auto-split"),
        ("GET", "/exams/{exam_id}/imports/auto-split"),
        ("GET", "/exams/{exam_id}/imports/auto-split/{split_token}"),
        ("DELETE", "/exams/{exam_id}/imports/auto-split/{split_token}"),
        ("POST", "/exams/{exam_id}/imports/auto-split/{split_token}/retry"),
        ("GET", "/exams/{exam_id}/imports/auto-split/{split_token}/pages/{index}/image"),
        ("GET", "/exams/{exam_id}/imports/auto-split/{split_token}/pages/{index}/thumb"),
        ("POST", "/exams/{exam_id}/imports/auto-split/{split_token}/commit"),
        ("POST", "/exams/{exam_id}/queue"),
        ("POST", "/submissions/{submission_id}/regrade"),
        ("POST", "/submissions/bulk/grade"),
        ("PUT", "/submissions/bulk/assignment"),
        ("POST", "/submissions/bulk/delete"),
        ("PATCH", "/students/{student_id}"),
        ("GET", "/grader/status"),
        ("POST", "/grader/usage"),
        ("GET", "/exams/{exam_id}/export.csv"),
        ("GET", "/exams/{exam_id}/export.zip"),
    }

    seen = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            key = (method, route.path)
            seen.add(key)
            deps = _dep_calls(route.dependant)
            if key in public:
                continue
            assert current_user in deps or require_admin in deps, f"{key} is missing authentication"
            if key in admin_only:
                assert require_admin in deps, f"{key} must require_admin"
            else:
                assert require_admin not in deps, f"{key} unexpectedly requires admin"

    assert admin_only <= seen
