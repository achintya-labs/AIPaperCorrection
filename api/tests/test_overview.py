import csv
import io

import pytest
from fastapi.testclient import TestClient

from app import stats
from app.main import app
from app.models import Grade, GradeStatus, Question, Student, Submission, SubmissionStatus, User
from app.permissions import Permission, permissions_for
from app.models import Role
from tests.test_auth import TestSession, client, login  # noqa: F401  (fixture reuse)

RUBRIC = {"questions": [{"number": "1", "max_points": 6}, {"number": "2", "max_points": 4}]}


def seed(client):
    """One exam (10 marks), four papers: A graded, B and C reviewed, D not graded. The TA is assigned to A only."""
    login(client, "admin@x.io")
    exam_id = client.post("/exams", json={"name": "Midterm"}).json()["id"]
    client.post(f"/exams/{exam_id}/rubrics", json=RUBRIC)
    with TestSession() as s:
        ta = s.query(User).filter_by(email="ta@x.io").one()
        q1, q2 = s.query(Question).order_by(Question.id).all()
        spec = [  # status, ai scores, final scores
            (SubmissionStatus.ready_for_review, (5, 3), (None, None)),
            (SubmissionStatus.reviewed, (3, 3), (4, None)),
            (SubmissionStatus.reviewed, (2, 2), (None, None)),
            (SubmissionStatus.uploaded, None, None),
        ]
        for i, (status, ai, final) in enumerate(spec):
            st = Student(external_id=f"s{i}")
            s.add(st)
            s.flush()
            sub = Submission(exam_id=exam_id, student_id=st.id, pdf_path=f"{i}.pdf", status=status, tas=[ta] if i == 0 else [])
            s.add(sub)
            s.flush()
            for q, a, f in zip((q1, q2), ai or (), final or ()):
                s.add(Grade(submission_id=sub.id, question_id=q.id, ai_score=a, final_score=f, status=GradeStatus.pending))
        s.commit()
    return exam_id


def test_overview_numbers(client):
    exam_id = seed(client)
    o = client.get(f"/exams/{exam_id}/overview").json()
    assert (o["total_papers"], o["max_score"]) == (4, 10.0)
    assert o["graded"]["count"] == 3 and o["graded"]["mean"] == pytest.approx(6.0)  # Claude's 8, 6, 4
    assert o["graded"]["std_dev"] == pytest.approx((8 / 3) ** 0.5)
    assert o["reviewed"]["count"] == 2 and o["reviewed"]["mean"] == pytest.approx(5.5)  # final 7 and 4
    assert o["reviewed"]["std_dev"] == pytest.approx(1.5)
    assert sum(b["graded"] for b in o["bins"]) == 3 and sum(b["reviewed"] for b in o["bins"]) == 2
    assert o["bins"][0]["start"] == 0 and o["bins"][-1]["end"] >= 10
    assert o["bin_width"] == o["auto_bin_width"]


def test_overview_bin_width_is_selectable(client):
    exam_id = seed(client)
    o = client.get(f"/exams/{exam_id}/overview", params={"bin_width": 5}).json()
    assert [(b["start"], b["end"], b["graded"], b["reviewed"]) for b in o["bins"]] == [(0, 5, 1, 1), (5, 10, 2, 1)]
    assert client.get(f"/exams/{exam_id}/overview", params={"bin_width": 0}).status_code == 422
    assert client.get(f"/exams/{exam_id}/overview", params={"bin_width": 0.001}).status_code == 422  # too many bars


def test_overview_empty_exam(client):
    login(client, "admin@x.io")
    exam_id = client.post("/exams", json={"name": "Empty"}).json()["id"]
    o = client.get(f"/exams/{exam_id}/overview").json()
    assert o["graded"] == {"count": 0, "mean": None, "std_dev": None} and o["total_papers"] == 0
    assert len(o["bins"]) >= 1


def test_overview_csv(client):
    exam_id = seed(client)
    r = client.get(f"/exams/{exam_id}/overview.csv", params={"bin_width": 5})
    assert r.status_code == 200 and "exam-%d-overview.csv" % exam_id in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert ["papers", "3", "2"] in rows and ["mean", "6", "5.5"] in rows
    assert ["0", "5", "1", "1"] in rows and ["5", "10", "2", "1"] in rows


def test_ta_sees_only_the_overview_of_their_exam(client):
    exam_id = seed(client)
    other = client.post("/exams", json={"name": "Not mine"}).json()["id"]
    ta = TestClient(app)
    login(ta, "ta@x.io")
    assert ta.get(f"/exams/{exam_id}/overview").status_code == 200
    assert ta.get(f"/exams/{exam_id}/overview.csv").status_code == 200
    assert ta.get(f"/exams/{other}/overview").status_code == 404  # not assigned: same answer as a missing exam
    assert [e["id"] for e in ta.get("/exams").json()] == [exam_id]


def test_ta_can_read_papers_and_rubric_but_not_change_anything(client):
    exam_id = seed(client)
    ta = TestClient(app)
    login(ta, "ta@x.io")
    assert ta.get(f"/exams/{exam_id}/rubric").status_code == 200
    assert ta.get("/submissions", params={"exam_id": exam_id}).status_code == 200
    people = ta.get("/auth/people")  # names the assigned TAs on the Papers tab
    assert people.status_code == 200 and set(people.json()[0]) == {"id", "email", "name", "role", "permissions"}
    assert ta.get("/auth/users").status_code == 403  # the full user list stays admin-only
    assert ta.post(f"/exams/{exam_id}/rubrics", json=RUBRIC).status_code == 403
    assert ta.post(f"/exams/{exam_id}/rubric.pdf", json=RUBRIC).status_code == 403
    assert ta.put("/submissions/1/assignment", json={"ta_ids": []}).status_code == 403
    assert ta.delete("/submissions/1").status_code == 403
    assert ta.patch("/students/1", json={"name": "x"}).status_code == 403
    assert ta.post("/submissions/bulk/grade", json={"submission_ids": [1]}).status_code == 403


def test_ta_sees_every_paper_of_their_exam_but_reviews_only_their_own(client):
    exam_id = seed(client)
    other = client.post("/exams", json={"name": "Not mine"}).json()["id"]
    ta = TestClient(app)
    login(ta, "ta@x.io")
    mine = ta.get("/submissions").json()  # the review queue: only the assigned paper
    assert len(mine) == 1
    everyone = ta.get("/submissions", params={"exam_id": exam_id, "scope": "exam"}).json()
    assert len(everyone) == 4 and {"score_total", "ta_ids"} <= set(everyone[0])
    assert ta.get("/submissions", params={"exam_id": exam_id}).json() == mine  # scope is opt-in
    assert ta.get("/submissions", params={"exam_id": other, "scope": "exam"}).status_code == 404
    assert ta.get("/submissions", params={"scope": "exam"}).status_code == 422
    not_mine = next(s["id"] for s in everyone if s["id"] != mine[0]["id"])
    assert ta.get(f"/submissions/{not_mine}").status_code == 404
    assert ta.get(f"/submissions/{not_mine}/detail").status_code == 404
    assert ta.get(f"/submissions/{not_mine}/export.pdf").status_code == 404
    assert ta.post(f"/submissions/{not_mine}/complete").status_code == 404


def test_ta_is_refused_everything_else_on_an_exam(client):
    exam_id = seed(client)
    ta = TestClient(app)
    login(ta, "ta@x.io")
    assert ta.post("/exams", json={"name": "x"}).status_code == 403
    assert ta.patch(f"/exams/{exam_id}", json={"name": "x"}).status_code == 403
    assert ta.delete(f"/exams/{exam_id}").status_code == 403
    assert ta.post(f"/exams/{exam_id}/rubrics", json=RUBRIC).status_code == 403
    assert ta.post(f"/exams/{exam_id}/rubric.pdf", json=RUBRIC).status_code == 403
    assert ta.post(f"/exams/{exam_id}/imports", files={"files": ("1.pdf", b"x", "application/pdf")}).status_code == 403
    assert ta.post(f"/exams/{exam_id}/queue").status_code == 403
    assert ta.post("/submissions/bulk/grade", json={"submission_ids": [1]}).status_code == 403
    assert ta.get(f"/exams/{exam_id}/export.csv").status_code == 403
    assert ta.get(f"/exams/{exam_id}/export.zip").status_code == 403
    assert ta.get("/students").status_code == 403


def test_permissions_in_me_and_role_table(client):
    login(client, "ta@x.io")
    assert client.get("/auth/me").json()["permissions"] == ["exams.view", "overview.export", "tab.overview", "tab.papers", "tab.rubric"]
    login(client, "admin@x.io")
    assert set(client.get("/auth/me").json()["permissions"]) == {p.value for p in Permission}
    assert permissions_for(Role.ta) < permissions_for(Role.admin)


def test_overview_needs_its_permission(client, monkeypatch):
    """Taking a tab away from a role closes the API behind it too, with no other change."""
    exam_id = seed(client)
    ta = TestClient(app)
    login(ta, "ta@x.io")
    from app import permissions
    monkeypatch.setitem(permissions.ROLE_PERMISSIONS, Role.ta, frozenset({Permission.exams_view}))
    assert ta.get(f"/exams/{exam_id}/overview").status_code == 403
    monkeypatch.setitem(permissions.ROLE_PERMISSIONS, Role.ta, frozenset({Permission.tab_overview}))
    assert ta.get(f"/exams/{exam_id}/overview").status_code == 200
    assert ta.get(f"/exams/{exam_id}/overview.csv").status_code == 403  # export is its own permission


def test_stats_helpers():
    assert stats.mean([]) is None and stats.std_dev([]) is None and stats.std_dev([3.0]) == 0.0
    assert stats.auto_bin_width([], 0) > 0
    assert stats.auto_bin_width([40, 55, 60, 62, 70, 71, 88], 100) in (5, 10)  # round, between span/30 and span/5
    assert stats.auto_bin_width([5.0] * 50, 10) == 2  # zero IQR falls back to Sturges, capped at span/5
    start, n = stats.bin_edges([-3.0, 4.0], 10, 5)
    assert (start, n) == (-5, 3) and stats.histogram([-3.0, 4.0, 10.0], start, 5, n) == [1, 1, 1]  # top edge joins the last bin
