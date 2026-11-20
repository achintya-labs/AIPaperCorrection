import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.main import app
from app.models import Role, Student, Submission, SubmissionStatus, User
from app.storage import LocalStorage, get_storage
from tests.test_auth import TestSession, client, login  # noqa: F401  (fixture reuse)
from tests.test_jobs import run, seed


@pytest.fixture()
def env(client, tmp_path):
    """A graded submission (2 questions, 3 pages) assigned to ta@x.io and ta2@x.io."""
    storage = LocalStorage(tmp_path)
    app.dependency_overrides[get_storage] = lambda: storage
    exam_id, (sid,) = seed(storage)
    run(storage)
    with TestSession() as s:
        ta1 = s.query(User).filter_by(email="ta@x.io").one()
        ta2 = User(email="ta2@x.io", password_hash=hash_password("pw"), role=Role.ta)
        outsider = User(email="out@x.io", password_hash=hash_password("pw"), role=Role.ta)
        s.add_all([ta2, outsider])
        s.get(Submission, sid).tas = [ta1, ta2]
        s.commit()

    def as_user(email, password="pw"):
        c = TestClient(app)
        assert login(c, email, password).status_code == 200
        return c

    login(client, "admin@x.io")
    return type("Env", (), {"sid": sid, "exam_id": exam_id, "as_user": staticmethod(as_user), "admin": client})


def detail(c, sid):
    return c.get(f"/submissions/{sid}/detail").json()


def test_detail_pages_grades_and_scoping(env):
    ta = env.as_user("ta@x.io")
    d = detail(ta, env.sid)
    assert [p["index"] for p in d["pages"]] == [1, 2, 3]
    assert [g["question_number"] for g in d["grades"]] == ["1", "2"]
    g2 = d["grades"][1]
    assert g2["max_points"] == 6 and g2["criteria"] and g2["ai_score"] == 3 and g2["final_score"] is None
    assert env.as_user("out@x.io").get(f"/submissions/{env.sid}/detail").status_code == 404


def test_page_image(env):
    r = env.as_user("ta@x.io").get(f"/submissions/{env.sid}/pages/2/image")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
    assert env.as_user("ta@x.io").get(f"/submissions/{env.sid}/pages/9/image").status_code == 404
    assert env.as_user("out@x.io").get(f"/submissions/{env.sid}/pages/1/image").status_code == 404


def test_approve_then_override(env):
    ta = env.as_user("ta@x.io")
    g = detail(ta, env.sid)["grades"][0]
    ok = ta.patch(
        f"/grades/{g['id']}",
        json={"version": g["version"], "final_score": g["ai_score"], "final_feedback": g["ai_feedback"]},
    ).json()
    assert ok["status"] == "reviewed" and ok["version"] == g["version"] + 1 and ok["final_score"] == g["ai_score"]

    changed = ta.patch(
        f"/grades/{g['id']}", json={"version": ok["version"], "final_score": 4, "final_feedback": "better"}
    ).json()
    assert changed["status"] == "overridden" and changed["final_feedback"] == "better"
    assert changed["ai_score"] == g["ai_score"] and changed["ai_feedback"] == g["ai_feedback"]  # AI kept
    assert changed["reviewed_by"] is not None


def test_score_bounds(env):
    ta = env.as_user("ta@x.io")
    g = detail(ta, env.sid)["grades"][0]  # max 4
    body = {"version": g["version"], "final_feedback": "x"}
    assert ta.patch(f"/grades/{g['id']}", json={**body, "final_score": 4.5}).status_code == 422
    assert ta.patch(f"/grades/{g['id']}", json={**body, "final_score": -1}).status_code == 422
    assert ta.patch(f"/grades/{g['id']}", json={**body, "final_score": 4}).status_code == 200


def test_concurrent_tas_stale_write_gets_409(env):
    a, b = env.as_user("ta@x.io"), env.as_user("ta2@x.io")
    g = detail(a, env.sid)["grades"][0]
    v = g["version"]
    assert a.patch(f"/grades/{g['id']}", json={"version": v, "final_score": 1, "final_feedback": "A"}).status_code == 200
    r = b.patch(f"/grades/{g['id']}", json={"version": v, "final_score": 2, "final_feedback": "B"})
    assert r.status_code == 409
    cur = r.json()["detail"]["current"]
    assert cur["final_feedback"] == "A" and cur["version"] == v + 1  # B is shown A's edit, not overwritten
    assert detail(b, env.sid)["grades"][0]["final_feedback"] == "A"


def test_grade_access_rules(env):
    g = detail(env.admin, env.sid)["grades"][0]
    body = {"version": g["version"], "final_score": 1, "final_feedback": "x"}
    assert env.as_user("out@x.io").patch(f"/grades/{g['id']}", json=body).status_code == 404
    assert env.as_user("ta@x.io").patch("/grades/9999", json=body).status_code == 404
    with TestSession() as s:
        s.get(Submission, env.sid).status = SubmissionStatus.queued
        s.commit()
    assert env.as_user("ta@x.io").patch(f"/grades/{g['id']}", json=body).status_code == 409


def test_complete_requires_all_reviewed(env):
    ta = env.as_user("ta@x.io")
    grades = detail(ta, env.sid)["grades"]
    assert ta.post(f"/submissions/{env.sid}/complete").status_code == 409
    for g in grades:
        ta.patch(
            f"/grades/{g['id']}",
            json={"version": g["version"], "final_score": g["ai_score"], "final_feedback": g["ai_feedback"]},
        )
    r = ta.post(f"/submissions/{env.sid}/complete")
    assert r.status_code == 200 and r.json()["status"] == "reviewed"
    assert env.as_user("out@x.io").post(f"/submissions/{env.sid}/complete").status_code == 404


def test_list_progress_fields(env):
    ta = env.as_user("ta@x.io")
    (s,) = ta.get("/submissions").json()
    assert (s["grades_total"], s["grades_reviewed"], s["min_confidence"]) == (2, 0, 0.5)
    g = detail(ta, env.sid)["grades"][0]
    ta.patch(f"/grades/{g['id']}", json={"version": 1, "final_score": 1, "final_feedback": "x"})
    assert ta.get("/submissions").json()[0]["grades_reviewed"] == 1


def test_export_csv(env):
    with TestSession() as s:
        s.get(Student, 1).external_id = "=cmd|calc"
        s.get(Student, 1).name = "Ann"
        s.commit()
    ta = env.as_user("ta@x.io")
    g = detail(ta, env.sid)["grades"][0]
    ta.patch(f"/grades/{g['id']}", json={"version": 1, "final_score": 4, "final_feedback": "x"})

    r = env.admin.get(f"/exams/{env.exam_id}/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == ["student_id", "name", "Q1", "Q2", "total", "status", "graders", "Q1 feedback", "Q2 feedback"]
    # Q1 = TA's 4 (final beats AI's 2), Q2 = AI's 3 (untouched); formula-looking id is neutralised.
    assert rows[1][:5] == ["'=cmd|calc", "Ann", "4", "3", "7"]
    assert rows[1][5] == "ready_for_review" and "ta@x.io" in rows[1][6]
    # Feedback follows the same rule as the score: the TA's text where they wrote some, Claude's otherwise.
    assert rows[1][7] == "x" and rows[1][8] == "Fake grader: half marks."
    assert env.as_user("ta@x.io").get(f"/exams/{env.exam_id}/export.csv").status_code == 403
    assert env.admin.get("/exams/999/export.csv").status_code == 404
