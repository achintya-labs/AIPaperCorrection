import pytest
from sqlalchemy import func, select

from app.main import app
from app.models import (
    Exam, Grade, GradingRun, Page, Question, Rubric, Student, Submission, SubmissionStatus, submission_tas,
)
from app.storage import get_storage
from tests.test_auth import TestSession, client, login  # noqa: F401  (fixture reuse)
from tests.test_review import env  # noqa: F401  (a graded paper, 3 pages, 2 questions, on exam env.exam_id)


def storage():
    return app.dependency_overrides[get_storage]()


def count(model, **where):
    with TestSession() as s:
        q = select(func.count()).select_from(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return s.scalar(q)


def files_of(sid):
    with TestSession() as s:
        sub = s.get(Submission, sid)
        return [sub.pdf_path, *[p.image_path for p in s.scalars(select(Page).where(Page.submission_id == sid))]]


def add_second_exam_with_paper(env):
    """Exam B with its own rubric and one graded paper, to prove deletes don't spill over."""
    with TestSession() as s:
        exam = Exam(name="Exam B")
        s.add(exam)
        s.flush()
        rub = Rubric(exam_id=exam.id, version=1, questions=[Question(number="1", max_points=2)])
        st = Student(external_id="other-student")
        s.add_all([rub, st])
        s.flush()
        sub = Submission(exam_id=exam.id, student_id=st.id, rubric_id=rub.id, pdf_path="b.pdf", status=SubmissionStatus.ready_for_review)
        s.add(sub)
        s.flush()
        s.add(Grade(submission_id=sub.id, question_id=rub.questions[0].id, ai_score=1, ai_feedback="b"))
        s.commit()
        return exam.id, sub.id


def approve_first_grade(c, sid):
    g = c.get(f"/submissions/{sid}/detail").json()["grades"][0]
    r = c.patch(f"/grades/{g['id']}", json={"version": g["version"], "final_score": g["ai_score"], "final_feedback": g["ai_feedback"]})
    assert r.status_code == 200


def set_status(sid, status):
    with TestSession() as s:
        s.get(Submission, sid).status = status
        s.commit()


# ---------------- papers ----------------


def test_admin_deletes_a_paper_and_everything_attached_to_it(env):
    keys = files_of(env.sid)
    assert len(keys) == 4 and all(storage().exists(k) for k in keys)  # the PDF and 3 page images
    students_before = count(Student)

    assert env.admin.delete(f"/submissions/{env.sid}").status_code == 204

    assert env.admin.get(f"/submissions/{env.sid}").status_code == 404
    for model in (Grade, Page, GradingRun):
        assert count(model, submission_id=env.sid) == 0, model
    with TestSession() as s:
        assert s.scalar(select(func.count()).select_from(submission_tas).where(submission_tas.c.submission_id == env.sid)) == 0
    assert not any(storage().exists(k) for k in keys)  # files are removed from storage too
    assert count(Student) == students_before  # the student record stays


def test_deleting_one_paper_leaves_other_papers_alone(env):
    _, other = add_second_exam_with_paper(env)
    assert env.admin.delete(f"/submissions/{env.sid}").status_code == 204
    assert env.admin.get(f"/submissions/{other}").status_code == 200
    assert count(Grade, submission_id=other) == 1


def test_only_admins_can_delete_and_missing_is_404(env):
    ta = env.as_user("ta@x.io")
    assert ta.delete(f"/submissions/{env.sid}").status_code == 403
    assert ta.delete(f"/exams/{env.exam_id}").status_code == 403
    assert env.admin.get(f"/submissions/{env.sid}").status_code == 200  # nothing happened
    assert env.admin.delete("/submissions/9999").status_code == 404
    assert env.admin.delete("/exams/9999").status_code == 404


def test_a_paper_with_ta_reviewed_grades_needs_force(env):
    approve_first_grade(env.as_user("ta@x.io"), env.sid)
    r = env.admin.delete(f"/submissions/{env.sid}")
    assert r.status_code == 409 and "1 TA-reviewed grade" in r.json()["detail"] and "force=true" in r.json()["detail"]
    assert env.admin.get(f"/submissions/{env.sid}").status_code == 200
    assert env.admin.delete(f"/submissions/{env.sid}?force=true").status_code == 204
    assert env.admin.get(f"/submissions/{env.sid}").status_code == 404


def test_a_paper_being_graded_cannot_be_deleted_even_with_force(env):
    set_status(env.sid, SubmissionStatus.grading)
    r = env.admin.delete(f"/submissions/{env.sid}?force=true")
    assert r.status_code == 409 and "being graded" in r.json()["detail"]
    assert count(Grade, submission_id=env.sid) > 0


# ---------------- exams ----------------


def test_deleting_an_exam_with_papers_needs_force_and_then_removes_it_all(env):
    keys = files_of(env.sid)
    r = env.admin.delete(f"/exams/{env.exam_id}")
    assert r.status_code == 409 and "1 paper(s)" in r.json()["detail"] and "force=true" in r.json()["detail"]
    assert env.admin.get("/exams").json() != []  # still there

    assert env.admin.delete(f"/exams/{env.exam_id}?force=true").status_code == 204
    assert env.admin.get("/exams").json() == []
    assert count(Exam) == 0 and count(Rubric) == 0 and count(Question) == 0
    assert count(Submission) == 0 and count(Grade) == 0 and count(Page) == 0 and count(GradingRun) == 0
    assert not any(storage().exists(k) for k in keys)
    assert count(Student) >= 1  # students are kept: they may have papers in other exams


def test_the_review_count_in_the_exam_message(env):
    approve_first_grade(env.as_user("ta@x.io"), env.sid)
    assert "1 TA-reviewed grade" in env.admin.delete(f"/exams/{env.exam_id}").json()["detail"]


def test_an_empty_exam_deletes_without_force(env):
    new = env.admin.post("/exams", json={"name": "Empty"}).json()["id"]
    env.admin.post(f"/exams/{new}/rubrics", json={"questions": [{"number": "1", "max_points": 1}]})
    rubrics_before = count(Rubric)
    assert env.admin.delete(f"/exams/{new}").status_code == 204
    assert count(Rubric) == rubrics_before - 1 and count(Question, rubric_id=None) == 0
    assert all(e["id"] != new for e in env.admin.get("/exams").json())


def test_deleting_an_exam_leaves_other_exams_intact(env):
    other_exam, other_sub = add_second_exam_with_paper(env)
    assert env.admin.delete(f"/exams/{env.exam_id}?force=true").status_code == 204
    assert [e["id"] for e in env.admin.get("/exams").json()] == [other_exam]
    assert env.admin.get(f"/submissions/{other_sub}").status_code == 200
    assert env.admin.get(f"/exams/{other_exam}/rubric").status_code == 200
    assert count(Grade, submission_id=other_sub) == 1


def test_an_exam_with_a_paper_being_graded_cannot_be_deleted(env):
    set_status(env.sid, SubmissionStatus.grading)
    r = env.admin.delete(f"/exams/{env.exam_id}?force=true")
    assert r.status_code == 409 and "being graded" in r.json()["detail"]
    assert count(Exam) == 1 and count(Submission) == 1


def test_deleted_exam_and_paper_are_gone_from_the_tas_view(env):
    ta = env.as_user("ta@x.io")
    assert len(ta.get("/exams").json()) == 1 and len(ta.get("/submissions").json()) == 1
    env.admin.delete(f"/exams/{env.exam_id}?force=true")
    assert ta.get("/exams").json() == [] and ta.get("/submissions").json() == []
