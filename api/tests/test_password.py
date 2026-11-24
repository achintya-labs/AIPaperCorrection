import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.auth import ALGORITHM, verify_password
from app.config import get_settings
from app.main import app
from app.models import User
from tests.test_auth import TestSession, client, login  # noqa: F401  (fixture reuse)

NEW = "a-brand-new-password"


def uid(email):
    with TestSession() as s:
        return s.query(User).filter_by(email=email).one().id


def signed_in(email, password="pw"):
    c = TestClient(app)
    assert login(c, email, password).status_code == 200
    return c


def change(c, current="pw", new=NEW):
    return c.put("/auth/me/password", json={"current_password": current, "new_password": new})


# ---- changing your own password ----


def test_change_own_password(client):
    ta = signed_in("ta@x.io")
    assert change(ta).status_code == 204
    assert ta.get("/auth/me").status_code == 200  # this browser stays signed in
    assert login(TestClient(app), "ta@x.io", "pw").status_code == 401  # old password is dead
    assert login(TestClient(app), "ta@x.io", NEW).status_code == 200
    with TestSession() as s:
        h = s.query(User).filter_by(email="ta@x.io").one().password_hash
        assert h != NEW and verify_password(NEW, h)  # stored hashed, never plaintext


def test_wrong_current_password_is_400_not_401(client):
    ta = signed_in("ta@x.io")
    r = change(ta, current="not-my-password")
    assert r.status_code == 400 and r.json()["detail"] == "Current password is incorrect"
    assert ta.get("/auth/me").status_code == 200  # still signed in: 401 would have kicked the UI to /login
    assert login(TestClient(app), "ta@x.io", "pw").status_code == 200  # unchanged


def test_new_password_must_differ_and_follow_policy(client):
    ta = signed_in("ta@x.io")
    assert change(ta, "pw", "pw").status_code == 422  # too short anyway
    with TestSession() as s:  # make the current password policy-compliant to test "must differ"
        from app.auth import hash_password
        s.query(User).filter_by(email="ta@x.io").one().password_hash = hash_password("same-password-1")
        s.commit()
    assert change(ta, "same-password-1", "same-password-1").status_code == 422
    for bad in ("short", "x" * 73, "é" * 37):  # 37 two-byte chars = 74 bytes
        r = change(ta, "same-password-1", bad)
        assert r.status_code == 422, bad
        assert r.json()["detail"][0]["msg"].startswith("Password must be at")


def test_change_requires_login(client):
    assert change(TestClient(app)).status_code == 401


def test_changing_password_signs_out_other_sessions_only(client):
    laptop, phone = signed_in("ta@x.io"), signed_in("ta@x.io")
    assert change(laptop).status_code == 204
    assert laptop.get("/auth/me").status_code == 200  # the session that changed it continues
    assert phone.get("/auth/me").status_code == 401  # every other session is ended
    assert phone.get("/submissions").status_code == 401


def test_tokens_issued_before_the_feature_still_work(client):
    """Existing sessions at upgrade time carry no "v" claim and must not be logged out."""
    s = get_settings()
    old = jwt.encode({"sub": str(uid("ta@x.io")), "exp": 4102444800}, s.jwt_secret, algorithm=ALGORITHM)
    c = TestClient(app)
    c.cookies.set("session", old)
    assert c.get("/auth/me").status_code == 200
    ta = signed_in("ta@x.io")
    change(ta)
    c2 = TestClient(app)
    c2.cookies.set("session", old)
    assert c2.get("/auth/me").status_code == 401  # but a password change does end it


# ---- admin resetting someone else's ----


def test_admin_resets_a_users_password(client):
    victim = signed_in("ta@x.io")
    login(client, "admin@x.io")
    r = client.put(f"/auth/users/{uid('ta@x.io')}/password", json={"password": NEW})
    assert r.status_code == 204
    assert victim.get("/auth/me").status_code == 401  # their sessions are ended
    assert login(TestClient(app), "ta@x.io", "pw").status_code == 401
    assert login(TestClient(app), "ta@x.io", NEW).status_code == 200
    assert client.get("/auth/me").status_code == 200  # the admin's own session is unaffected


def test_reset_works_for_inactive_users_and_keeps_them_inactive(client):
    login(client, "admin@x.io")
    t = uid("ta@x.io")
    client.patch(f"/auth/users/{t}", json={"active": False})
    assert client.put(f"/auth/users/{t}/password", json={"password": NEW}).status_code == 204
    assert login(TestClient(app), "ta@x.io", NEW).status_code == 401  # still inactive
    client.patch(f"/auth/users/{t}", json={"active": True})
    assert login(TestClient(app), "ta@x.io", NEW).status_code == 200


def test_reset_rules(client):
    ta = signed_in("ta@x.io")
    assert ta.put(f"/auth/users/{uid('admin@x.io')}/password", json={"password": NEW}).status_code == 403
    login(client, "admin@x.io")
    assert client.put("/auth/users/9999/password", json={"password": NEW}).status_code == 404
    assert client.put(f"/auth/users/{uid('ta@x.io')}/password", json={"password": "short"}).status_code == 422
    assert login(TestClient(app), "ta@x.io", "pw").status_code == 200  # a rejected reset changes nothing


def test_admin_resets_own_password_directly(client):
    other_admin = signed_in("admin@x.io")
    login(client, "admin@x.io")
    me = uid("admin@x.io")
    assert client.put(f"/auth/users/{me}/password", json={"password": NEW}).status_code == 204
    assert client.get("/auth/users").status_code == 200  # current session stays signed in
    assert other_admin.get("/auth/me").status_code == 401  # other sessions are signed out
    assert login(TestClient(app), "admin@x.io", NEW).status_code == 200


def test_admin_can_change_their_own_password_the_normal_way(client):
    admin = signed_in("admin@x.io")
    assert change(admin).status_code == 204
    assert admin.get("/auth/users").status_code == 200  # still an admin, still signed in
    assert login(TestClient(app), "admin@x.io", NEW).status_code == 200


# ---- policy on account creation ----


def test_new_accounts_must_meet_the_same_policy(client):
    login(client, "admin@x.io")
    assert client.post("/auth/users", json={"email": "n@x.io", "password": "short"}).status_code == 422
    assert client.post("/auth/users", json={"email": "n@x.io", "password": "x" * 73}).status_code == 422
    assert client.post("/auth/users", json={"email": "n@x.io", "password": "long-enough-pw"}).status_code == 201


# ---- blank password = Google-only account ----


def test_admin_can_create_a_user_without_a_password(client):
    login(client, "admin@x.io")
    r = client.post("/auth/users", json={"email": "g@x.io", "password": ""})
    assert r.status_code == 201
    row = next(u for u in client.get("/auth/users").json() if u["email"] == "g@x.io")
    assert row["has_password"] is False
    assert next(u for u in client.get("/auth/users").json() if u["email"] == "ta@x.io")["has_password"] is True


def test_blank_password_never_signs_in(client):
    login(client, "admin@x.io")
    client.post("/auth/users", json={"email": "g@x.io", "password": ""})
    c = TestClient(app)
    assert login(c, "g@x.io", "").status_code == 401  # the passwordless account
    assert login(c, "ta@x.io", "").status_code == 401  # an account that does have a password
    assert c.get("/auth/me").status_code == 401


def test_admin_can_blank_another_users_password_and_it_signs_them_out(client):
    ta = signed_in("ta@x.io")
    login(client, "admin@x.io")
    assert client.put(f"/auth/users/{uid('ta@x.io')}/password", json={"password": ""}).status_code == 204
    assert ta.get("/auth/me").status_code == 401
    assert login(TestClient(app), "ta@x.io", "pw").status_code == 401
    assert login(TestClient(app), "ta@x.io", "").status_code == 401


def test_nobody_can_blank_their_own_password(client):
    login(client, "admin@x.io")
    me = uid("admin@x.io")
    assert client.put(f"/auth/users/{me}/password", json={"password": ""}).status_code == 422  # direct reset
    assert change(client, new="").status_code == 422  # normal change
    assert login(TestClient(app), "admin@x.io", "pw").status_code == 200  # unchanged


def test_non_blank_passwords_still_follow_the_policy(client):
    login(client, "admin@x.io")
    assert client.post("/auth/users", json={"email": "s@x.io", "password": "short"}).status_code == 422
    assert client.put(f"/auth/users/{uid('ta@x.io')}/password", json={"password": "short"}).status_code == 422
