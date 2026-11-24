from fastapi.testclient import TestClient

from app.auth import hash_password
from app.main import app
from app.models import Role, Student, Submission, User
from tests.test_auth import TestSession, client, login  # noqa: F401  (fixture reuse)

RUBRIC = {
    "questions": [
        {
            "number": "1",
            "max_points": 5,
            "criteria": [{"description": "correct setup", "points": 2}, {"description": "answer", "points": 3}],
        },
        {"number": "2a", "max_points": 3},
    ]
}


def make_exam(c):
    login(c, "admin@x.io")
    return c.post("/exams", json={"name": "Midterm"}).json()["id"]


def test_rubric_versioning(client):
    exam_id = make_exam(client)
    r1 = client.post(f"/exams/{exam_id}/rubrics", json=RUBRIC)
    r2 = client.post(f"/exams/{exam_id}/rubrics", json=RUBRIC)
    assert (r1.json()["version"], r2.json()["version"]) == (1, 2)
    latest = client.get(f"/exams/{exam_id}/rubric").json()
    assert latest["version"] == 2 and [q["number"] for q in latest["questions"]] == ["1", "2a"]


def test_rubric_validation(client):
    exam_id = make_exam(client)
    dup = {"questions": [{"number": "1", "max_points": 1}, {"number": "1", "max_points": 1}]}
    assert client.post(f"/exams/{exam_id}/rubrics", json=dup).status_code == 422
    over = {"questions": [{"number": "1", "max_points": 1, "criteria": [{"description": "x", "points": 2}]}]}
    assert client.post(f"/exams/{exam_id}/rubrics", json=over).status_code == 422


def test_ta_cannot_edit_exams_or_students(client):
    login(client, "ta@x.io")
    assert client.post("/exams", json={"name": "x"}).status_code == 403
    assert client.post("/students", json=[{"external_id": "s1"}]).status_code == 403


def test_ta_scoping(client):
    exam_id = make_exam(client)
    with TestSession() as s:
        ta1 = s.query(User).filter_by(email="ta@x.io").one()
        ta2 = User(email="ta2@x.io", password_hash=hash_password("pw"), role=Role.ta)
        s1, s2 = Student(external_id="s1"), Student(external_id="s2")
        s.add_all([ta2, s1, s2])
        s.flush()
        a = Submission(exam_id=exam_id, student_id=s1.id, pdf_path="a.pdf", tas=[ta1])
        b = Submission(exam_id=exam_id, student_id=s2.id, pdf_path="b.pdf", tas=[ta2])
        s.add_all([a, b])
        s.commit()
        a_id, b_id = a.id, b.id

    admin_count = len(client.get("/submissions").json())
    assert admin_count == 2

    other = TestClient(app)
    login(other, "ta@x.io")
    assert [x["id"] for x in other.get("/submissions").json()] == [a_id]
    assert other.get(f"/submissions/{a_id}").status_code == 200
    assert other.get(f"/submissions/{b_id}").status_code == 404
    assert other.put(f"/submissions/{a_id}/assignment", json={"ta_ids": []}).status_code == 403


def test_multiple_tas_share_a_submission(client):
    exam_id = make_exam(client)
    with TestSession() as s:
        ta1 = s.query(User).filter_by(email="ta@x.io").one()
        ta2 = User(email="ta2@x.io", password_hash=hash_password("pw"), role=Role.ta)
        st = Student(external_id="s1")
        s.add_all([ta2, st])
        s.flush()
        sub = Submission(exam_id=exam_id, student_id=st.id, pdf_path="a.pdf", tas=[ta1, ta2])
        s.add(sub)
        s.commit()
        sid = sub.id
    assert client.get(f"/submissions/{sid}").json()["ta_ids"] == sorted([ta1.id, ta2.id])
    for email in ("ta@x.io", "ta2@x.io"):
        c = TestClient(app)
        login(c, email)
        assert c.get(f"/submissions/{sid}").status_code == 200


def test_admin_reassigns(client):
    exam_id = make_exam(client)
    with TestSession() as s:
        st = Student(external_id="s1")
        s.add(st)
        s.flush()
        sub = Submission(exam_id=exam_id, student_id=st.id, pdf_path="a.pdf")
        s.add(sub)
        s.commit()
        sid = sub.id
        ta_id = s.query(User).filter_by(email="ta@x.io").one().id
    r = client.put(f"/submissions/{sid}/assignment", json={"ta_ids": [ta_id]})
    assert r.status_code == 200 and r.json()["ta_ids"] == [ta_id]
    assert client.put(f"/submissions/{sid}/assignment", json={"ta_ids": [ta_id, 9999]}).status_code == 422
    r = client.put(f"/submissions/{sid}/assignment", json={"ta_ids": []})
    assert r.json()["ta_ids"] == []


def _seed_two_exams_two_tas(client):
    """Exam A: a paper for ta@x.io. Exam B: a paper for ta2@x.io. Exam C: no papers at all."""
    login(client, "admin@x.io")
    ids = {n: client.post("/exams", json={"name": f"Exam {n}"}).json()["id"] for n in "ABC"}
    for exam_id in ids.values():
        client.post(f"/exams/{exam_id}/rubrics", json={"questions": [{"number": "1", "max_points": 1, "expected_answer": "secret"}]})
    with TestSession() as s:
        ta1 = s.query(User).filter_by(email="ta@x.io").one()
        ta2 = User(email="ta2@x.io", password_hash=hash_password("pw"), role=Role.ta)
        st1, st2 = Student(external_id="s1"), Student(external_id="s2")
        s.add_all([ta2, st1, st2])
        s.flush()
        s.add_all([
            Submission(exam_id=ids["A"], student_id=st1.id, pdf_path="a.pdf", tas=[ta1]),
            Submission(exam_id=ids["B"], student_id=st2.id, pdf_path="b.pdf", tas=[ta2]),
        ])
        s.commit()
    return ids


def test_ta_only_sees_exams_they_are_assigned_to(client):
    ids = _seed_two_exams_two_tas(client)
    assert [e["name"] for e in client.get("/exams").json()] == ["Exam A", "Exam B", "Exam C"]  # admin: everything

    ta1, ta2 = TestClient(app), TestClient(app)
    login(ta1, "ta@x.io"); login(ta2, "ta2@x.io")
    assert [e["name"] for e in ta1.get("/exams").json()] == ["Exam A"]
    assert [e["name"] for e in ta2.get("/exams").json()] == ["Exam B"]  # and never the unassigned exam C


def test_ta_with_no_assignments_sees_no_exams(client):
    _seed_two_exams_two_tas(client)
    with TestSession() as s:
        s.add(User(email="idle@x.io", password_hash=hash_password("pw"), role=Role.ta))
        s.commit()
    idle = TestClient(app); login(idle, "idle@x.io")
    assert idle.get("/exams").json() == []


def test_rubric_is_only_readable_for_your_own_exams(client):
    ids = _seed_two_exams_two_tas(client)
    ta1 = TestClient(app); login(ta1, "ta@x.io")
    assert ta1.get(f"/exams/{ids['A']}/rubric").status_code == 200
    # Another exam's rubric (with its model answers) and an exam nobody is assigned to look identical to "missing".
    assert ta1.get(f"/exams/{ids['B']}/rubric").status_code == 404
    assert ta1.get(f"/exams/{ids['C']}/rubric").status_code == 404
    assert ta1.get("/exams/9999/rubric").status_code == 404
    assert client.get(f"/exams/{ids['B']}/rubric").status_code == 200  # admin unaffected


def test_exam_list_follows_assignment_changes(client):
    ids = _seed_two_exams_two_tas(client)
    ta1 = TestClient(app); login(ta1, "ta@x.io")
    with TestSession() as s:
        sub_b = s.query(Submission).filter_by(exam_id=ids["B"]).one().id
    client.put(f"/submissions/{sub_b}/assignment", json={"ta_ids": [uid_of("ta@x.io"), uid_of("ta2@x.io")]})
    assert [e["name"] for e in ta1.get("/exams").json()] == ["Exam A", "Exam B"]  # added to a paper in B
    client.put(f"/submissions/{sub_b}/assignment", json={"ta_ids": [uid_of("ta2@x.io")]})
    assert [e["name"] for e in ta1.get("/exams").json()] == ["Exam A"]  # and removed again


def uid_of(email):
    with TestSession() as s:
        return s.query(User).filter_by(email=email).one().id


def test_exam_rename_keeps_everything_else(client):
    exam_id = make_exam(client)
    client.post(f"/exams/{exam_id}/rubrics", json=RUBRIC)
    r = client.patch(f"/exams/{exam_id}", json={"name": "  Final exam  ", "model_override": "m1"})
    assert r.status_code == 200 and r.json()["name"] == "Final exam" and r.json()["id"] == exam_id
    # Same id, same rubric, same exam: the name is only a label.
    assert client.get(f"/exams/{exam_id}/rubric").json()["version"] == 1
    assert [e["name"] for e in client.get("/exams").json()] == ["Final exam"]
    # A second exam may share the name: it is not a key.
    assert client.post("/exams", json={"name": "Final exam"}).status_code == 201


def test_exam_name_cannot_be_blank(client):
    exam_id = make_exam(client)
    assert client.patch(f"/exams/{exam_id}", json={"name": "   "}).status_code == 422
    assert client.post("/exams", json={"name": ""}).status_code == 422
    assert client.get("/exams").json()[0]["name"] == "Midterm"
