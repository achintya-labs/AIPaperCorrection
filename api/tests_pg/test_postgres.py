import threading

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select

from app.auth import hash_password
from app.db import SessionLocal, engine
from app.jobs import queue
from app.main import app
from app.models import (
    Exam, Grade, GradeStatus, GradingRun, Page, Question, Role, Rubric, Student, Submission,
    SubmissionStatus, User, submission_tas,
)
from app.storage import LocalStorage, get_storage

pytestmark = pytest.mark.postgres


def make_user(email, role=Role.ta):
    with SessionLocal() as s:
        u = User(email=email, password_hash=hash_password("password-1234"), role=role)
        s.add(u)
        s.commit()
        return u.id


def signed_in(email):
    c = TestClient(app)
    assert c.post("/auth/login", json={"email": email, "password": "password-1234"}).status_code == 200
    return c


def build_graded_paper(exam_id=None, tas=()):
    """An exam with a rubric and one graded paper: pages, grades, a grading run and assignments."""
    with SessionLocal() as s:
        exam = s.get(Exam, exam_id) if exam_id else Exam(name="E")
        if not exam_id:
            s.add(exam)
            s.flush()
            s.add(Rubric(exam_id=exam.id, version=1, questions=[Question(number="1", position=0, max_points=2), Question(number="2", position=1, max_points=3)]))
            s.flush()
        rubric = s.scalar(select(Rubric).where(Rubric.exam_id == exam.id))
        st = Student(external_id=f"stu-{s.scalar(select(func.count()).select_from(Student))}")
        s.add(st)
        s.flush()
        sub = Submission(exam_id=exam.id, student_id=st.id, rubric_id=rubric.id, pdf_path="x.pdf", status=SubmissionStatus.ready_for_review,
                         tas=[s.get(User, t) for t in tas])
        s.add(sub)
        s.flush()
        s.add_all([Page(submission_id=sub.id, index=i, image_path=f"p{i}.png") for i in (1, 2)])
        s.add_all([Grade(submission_id=sub.id, question_id=q.id, ai_score=1, ai_feedback="f") for q in rubric.questions])
        s.add(GradingRun(submission_id=sub.id, provider="fake", model="m"))
        s.commit()
        return exam.id, sub.id


def count(model):
    with SessionLocal() as s:
        return s.scalar(select(func.count()).select_from(model))


def test_the_latest_migration_round_trips():
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "-1")
    assert "position" not in {c["name"] for c in inspect(engine).get_columns("questions")}
    command.upgrade(cfg, "head")
    assert "position" in {c["name"] for c in inspect(engine).get_columns("questions")}


def test_skip_locked_never_hands_one_job_to_two_workers():
    with SessionLocal() as s:
        exam = Exam(name="E")
        s.add(exam)
        s.flush()
        for i in range(60):
            st = Student(external_id=f"s{i}")
            s.add(st)
            s.flush()
            s.add(Submission(exam_id=exam.id, student_id=st.id, pdf_path="x.pdf", status=SubmissionStatus.queued))
        s.commit()

    claimed: list[int] = []
    guard = threading.Lock()
    start = threading.Barrier(8)

    def worker(name):
        start.wait()  # everyone starts claiming at once
        while True:
            with SessionLocal() as db:
                sub = queue.claim_next(db, name)
                if sub is None:
                    return
                with guard:
                    claimed.append(sub.id)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert len(claimed) == 60 and len(set(claimed)) == 60, "a job was claimed twice or never"
    with SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(Submission).where(Submission.status == SubmissionStatus.grading)) == 60
        assert s.scalar(select(func.count()).select_from(Submission).where(Submission.claimed_by.is_(None))) == 0


def test_role_changes_use_row_locks_and_keep_an_admin():
    a, b = make_user("a@x.io", Role.admin), make_user("b@x.io", Role.admin)
    ca = signed_in("a@x.io")
    assert ca.patch(f"/auth/users/{b}", json={"role": "ta"}).status_code == 200  # SELECT ... FOR UPDATE on admins
    assert ca.patch(f"/auth/users/{a}", json={"role": "ta"}).status_code == 409  # can't demote yourself
    assert ca.delete(f"/auth/users/{b}").status_code == 204
    with SessionLocal() as s:
        assert [u.email for u in s.scalars(select(User))] == ["a@x.io"]


def test_deleting_papers_and_exams_satisfies_every_foreign_key(tmp_path):
    app.dependency_overrides[get_storage] = lambda: LocalStorage(tmp_path)
    try:
        ta = make_user("ta@x.io")
        make_user("admin@x.io", Role.admin)
        admin = signed_in("admin@x.io")

        exam_id, paper = build_graded_paper(tas=[ta])
        assert admin.delete(f"/submissions/{paper}").status_code == 204
        for model in (Submission, Page, Grade, GradingRun):
            assert count(model) == 0, model
        with SessionLocal() as s:
            assert s.scalar(select(func.count()).select_from(submission_tas)) == 0
        assert count(Exam) == 1 and count(Rubric) == 1  # the exam survives a paper delete

        _, p1 = build_graded_paper(exam_id, tas=[ta])
        _, p2 = build_graded_paper(exam_id)
        with SessionLocal() as s:
            s.scalars(select(Grade).where(Grade.submission_id == p1)).first().status = GradeStatus.reviewed
            s.commit()
        r = admin.delete(f"/exams/{exam_id}")
        assert r.status_code == 409 and "2 paper(s)" in r.json()["detail"]
        assert admin.delete(f"/exams/{exam_id}?force=true").status_code == 204
        for model in (Exam, Rubric, Question, Submission, Page, Grade, GradingRun):
            assert count(model) == 0, model
        assert count(Student) == 3  # students are kept
    finally:
        app.dependency_overrides.clear()


def test_a_paper_being_graded_cannot_be_deleted():
    make_user("admin@x.io", Role.admin)
    admin = signed_in("admin@x.io")
    _, paper = build_graded_paper()
    with SessionLocal() as s:
        s.get(Submission, paper).status = SubmissionStatus.grading
        s.commit()
    r = admin.delete(f"/submissions/{paper}?force=true")
    assert r.status_code == 409 and "being graded" in r.json()["detail"]
    assert count(Submission) == 1


def test_questions_come_back_in_the_order_the_rubric_was_written():
    make_user("admin@x.io", Role.admin)
    admin = signed_in("admin@x.io")
    exam = admin.post("/exams", json={"name": "E"}).json()["id"]
    order = ["B", "10", "A", "2", "1"]
    assert admin.post(f"/exams/{exam}/rubrics", json={"questions": [{"number": n, "max_points": 1} for n in order]}).status_code == 201
    assert [q["number"] for q in admin.get(f"/exams/{exam}/rubric").json()["questions"]] == order
