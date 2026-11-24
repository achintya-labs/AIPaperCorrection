import pytest
from fastapi import HTTPException

from app.models import Grade, GradeStatus, Role, Submission, User
from app.routes.auth import _guard_last_admin
from tests.test_auth import TestSession, client, login  # noqa: F401  (fixture reuse)
from tests.test_review import env  # noqa: F401  (a graded paper assigned to ta@x.io and ta2@x.io)


def uid(email):
    with TestSession() as s:
        return s.query(User).filter_by(email=email).one().id


def users(c):
    return {u["email"]: u for u in c.get("/auth/users").json()}


def approve_first_grade(c, sid):
    g = c.get(f"/submissions/{sid}/detail").json()["grades"][0]
    r = c.patch(
        f"/grades/{g['id']}",
        json={"version": g["version"], "final_score": g["ai_score"], "final_feedback": g["ai_feedback"]},
    )
    assert r.status_code == 200
    return g["id"]


def test_list_includes_activity_counts(env):
    ta = env.as_user("ta@x.io")
    approve_first_grade(ta, env.sid)
    u = users(env.admin)
    assert u["ta@x.io"]["assigned_papers"] == 1 and u["ta@x.io"]["reviewed_grades"] == 1
    assert u["ta2@x.io"]["assigned_papers"] == 1 and u["ta2@x.io"]["reviewed_grades"] == 0
    assert u["out@x.io"]["assigned_papers"] == 0 and u["out@x.io"]["active"] is True
    assert set(u["admin@x.io"]) == {"id", "email", "name", "role", "active", "has_password", "assigned_papers", "reviewed_grades"}


def test_role_change_takes_effect_immediately_on_the_same_session(env):
    out = env.as_user("out@x.io")  # a TA with no papers
    assert out.get("/submissions").json() == []
    assert out.get("/auth/users").status_code == 403

    r = env.admin.patch(f"/auth/users/{uid('out@x.io')}", json={"role": "admin"})
    assert r.status_code == 200 and r.json()["role"] == "admin"
    assert len(out.get("/submissions").json()) == 1  # same cookie, now sees everything
    assert out.get("/auth/users").status_code == 200

    env.admin.patch(f"/auth/users/{uid('out@x.io')}", json={"role": "ta"})
    assert out.get("/submissions").json() == []
    assert out.get("/auth/users").status_code == 403


def test_deactivate_blocks_existing_session_and_login_then_reactivate(env):
    out = env.as_user("out@x.io")
    assert out.get("/auth/me").status_code == 200
    r = env.admin.patch(f"/auth/users/{uid('out@x.io')}", json={"active": False})
    assert r.status_code == 200 and r.json()["active"] is False
    assert out.get("/auth/me").status_code == 401
    assert login(out, "out@x.io").status_code == 401
    env.admin.patch(f"/auth/users/{uid('out@x.io')}", json={"active": True})
    assert login(out, "out@x.io").status_code == 200


def test_patch_validation_and_permissions(env):
    t = uid("ta@x.io")
    assert env.admin.patch(f"/auth/users/{t}", json={}).status_code == 422
    assert env.admin.patch(f"/auth/users/{t}", json={"role": "superuser"}).status_code == 422
    assert env.admin.patch("/auth/users/9999", json={"role": "ta"}).status_code == 404
    ta = env.as_user("ta@x.io")
    assert ta.patch(f"/auth/users/{t}", json={"role": "admin"}).status_code == 403
    assert ta.delete(f"/auth/users/{uid('out@x.io')}").status_code == 403


def test_admin_cannot_demote_or_deactivate_themselves(env):
    me = uid("admin@x.io")
    for body in ({"role": "ta"}, {"active": False}, {"role": "ta", "active": False}):
        r = env.admin.patch(f"/auth/users/{me}", json=body)
        assert r.status_code == 409 and "yourself" in r.json()["detail"]
    assert env.admin.patch(f"/auth/users/{me}", json={"role": "admin"}).status_code == 200  # no-op is fine
    assert users(env.admin)["admin@x.io"]["role"] == "admin"


def test_one_admin_can_demote_another(env):
    env.admin.post("/auth/users", json={"email": "boss@x.io", "password": "boss-password-1", "role": "admin"})
    boss = env.as_user("boss@x.io", "boss-password-1")
    assert boss.patch(f"/auth/users/{uid('admin@x.io')}", json={"role": "ta"}).status_code == 200
    assert env.admin.get("/auth/users").status_code == 403  # the first admin is now a TA


def test_last_active_admin_guard():
    # The guard is what stops two admins demoting each other at the same instant, so test it directly.
    from tests.test_auth import engine
    from app.db import Base
    from app.auth import hash_password

    Base.metadata.create_all(engine)
    try:
        with TestSession() as s:
            only = User(email="solo@x.io", password_hash=hash_password("pw"), role=Role.admin)
            s.add(only)
            s.commit()
            with pytest.raises(HTTPException) as e:
                _guard_last_admin(s, only)
            assert e.value.status_code == 409 and "active admin" in e.value.detail
            s.add(User(email="two@x.io", password_hash=hash_password("pw"), role=Role.admin))
            s.commit()
            _guard_last_admin(s, only)  # a second active admin exists: allowed
    finally:
        Base.metadata.drop_all(engine)


def test_delete_removes_assignments_and_invalidates_session(env):
    victim = env.as_user("ta2@x.io")
    assert len(victim.get("/submissions").json()) == 1
    r = env.admin.delete(f"/auth/users/{uid('ta2@x.io')}")
    assert r.status_code == 204
    assert victim.get("/auth/me").status_code == 401
    assert "ta2@x.io" not in users(env.admin)
    sub = env.admin.get(f"/submissions/{env.sid}").json()
    assert sub["ta_ids"] == [uid("ta@x.io")]  # the other TA is untouched
    assert login(victim, "ta2@x.io").status_code == 401


def test_delete_refused_when_user_reviewed_grades_but_force_keeps_the_grades(env):
    ta = env.as_user("ta@x.io")
    gid = approve_first_grade(ta, env.sid)
    t = uid("ta@x.io")

    r = env.admin.delete(f"/auth/users/{t}")
    assert r.status_code == 409
    assert "reviewed 1 grade" in r.json()["detail"] and "Deactivate" in r.json()["detail"]
    assert "ta@x.io" in users(env.admin)  # still there

    assert env.admin.delete(f"/auth/users/{t}?force=true").status_code == 204
    with TestSession() as s:
        g = s.get(Grade, gid)
        assert g.reviewed_by is None and g.status == GradeStatus.reviewed and g.final_score is not None
    assert env.admin.get(f"/submissions/{env.sid}").json()["ta_ids"] == [uid("ta2@x.io")]


def test_delete_guards(env):
    assert env.admin.delete(f"/auth/users/{uid('admin@x.io')}").status_code == 409  # yourself
    assert env.admin.delete("/auth/users/9999").status_code == 404
    env.admin.post("/auth/users", json={"email": "boss@x.io", "password": "boss-password-1", "role": "admin"})
    assert env.admin.delete(f"/auth/users/{uid('boss@x.io')}").status_code == 204  # another admin is fine
    with TestSession() as s:
        assert s.query(Submission).count() == 1  # papers are never deleted with a user
