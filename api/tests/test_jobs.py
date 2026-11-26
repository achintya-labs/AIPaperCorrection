from datetime import timedelta

import pytest

from app.config import Settings
from app.db import Base
from app.grader.base import GraderError
from app.grader.fake import FakeProvider
from app.jobs import queue
from app.jobs.worker import run_once
from app.models import (
    Exam,
    Grade,
    GradeStatus,
    GradingRun,
    Page,
    Question,
    Rubric,
    Student,
    Submission,
    SubmissionStatus,
)
from app.storage import LocalStorage
from tests.test_auth import TestSession, client, engine, login  # noqa: F401  (fixture reuse)
from tests.test_import import make_pdf
from tests.test_exams_scoping import make_exam

SETTINGS = Settings(claude_model="default-model", render_dpi=50)


@pytest.fixture()
def tables():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def storage(tmp_path, tables):
    return LocalStorage(tmp_path)


def seed(storage, with_rubric=True, status=SubmissionStatus.queued, pages=3, n_subs=1):
    with TestSession() as s:
        exam = Exam(name="E")
        s.add(exam)
        s.flush()
        if with_rubric:
            s.add(
                Rubric(
                    exam_id=exam.id,
                    version=1,
                    questions=[
                        Question(number="1", max_points=4),
                        Question(number="2", max_points=6, criteria=[{"description": "x", "points": 6}]),
                    ],
                )
            )
        ids = []
        for i in range(n_subs):
            st = Student(external_id=f"s{i}")
            s.add(st)
            s.flush()
            key = f"submissions/{exam.id}/{i}.pdf"
            storage.save(key, make_pdf(pages))
            sub = Submission(exam_id=exam.id, student_id=st.id, pdf_path=key, status=status)
            s.add(sub)
            s.flush()
            ids.append(sub.id)
        s.commit()
        return exam.id, ids


def run(storage, provider=None):
    return run_once(provider or FakeProvider(), storage, SETTINGS, session_factory=TestSession)


def test_end_to_end_grading(storage):
    exam_id, (sid,) = seed(storage)
    assert run(storage) is True
    with TestSession() as s:
        sub = s.get(Submission, sid)
        assert sub.status == SubmissionStatus.ready_for_review
        assert sub.overall_confidence == 0.5 and sub.rubric_id is not None and sub.claimed_at
        pages = s.query(Page).filter_by(submission_id=sid).order_by(Page.index).all()
        assert [p.index for p in pages] == [1, 2, 3]
        assert all(storage.exists(p.image_path) for p in pages)
        grades = s.query(Grade).filter_by(submission_id=sid).all()
        assert sorted(g.ai_score for g in grades) == [2, 3]
        # AI output only; TA fields untouched until review.
        assert all(g.final_score is None and g.status == GradeStatus.pending for g in grades)
        (gr,) = s.query(GradingRun).filter_by(submission_id=sid).all()
        assert gr.provider == "fake" and gr.model == "default-model" and gr.error is None
    assert run(storage) is False  # queue drained


def test_exam_model_override_used(storage):
    exam_id, _ = seed(storage)
    with TestSession() as s:
        s.get(Exam, exam_id).model_override = "special-model"
        s.commit()
    run(storage)
    with TestSession() as s:
        assert s.query(GradingRun).one().model == "special-model"


class Boom(FakeProvider):
    def _attempt(self, *a, **k):
        raise GraderError("model exploded")


def test_provider_failure_marks_failed(storage):
    _, (sid,) = seed(storage)
    run(storage, Boom())
    with TestSession() as s:
        sub = s.get(Submission, sid)
        assert sub.status == SubmissionStatus.failed and sub.error == "model exploded"
        assert s.query(Page).count() == 0 and s.query(Grade).count() == 0
        assert s.query(GradingRun).one().error == "model exploded"


def test_missing_rubric_fails(storage):
    _, (sid,) = seed(storage, with_rubric=False)
    run(storage)
    with TestSession() as s:
        assert s.get(Submission, sid).error == "Exam has no rubric"


def test_corrupt_pdf_fails(storage):
    _, (sid,) = seed(storage)
    with TestSession() as s:
        storage.save(s.get(Submission, sid).pdf_path, b"garbage")
    run(storage)
    with TestSession() as s:
        sub = s.get(Submission, sid)
        assert sub.status == SubmissionStatus.failed and "PDF" in sub.error


def test_claims_oldest_first_and_only_once(storage):
    _, ids = seed(storage, n_subs=2)
    with TestSession() as s1, TestSession() as s2:
        a = queue.claim_next(s1)
        b = queue.claim_next(s2)
        assert (a.id, b.id) == tuple(ids)
        assert queue.claim_next(s1) is None
        assert a.status == SubmissionStatus.grading


def test_recover_stale(storage):
    _, (sid,) = seed(storage)
    with TestSession() as s:
        queue.claim_next(s)
        assert queue.recover_stale(s, timedelta(minutes=5)) == 0  # fresh claim is left alone
        s.get(Submission, sid).claimed_at = queue.utcnow() - timedelta(hours=1)
        s.commit()
        assert queue.recover_stale(s, timedelta(minutes=5)) == 1
        sub = s.get(Submission, sid)
        assert sub.status == SubmissionStatus.queued and sub.claimed_at is None


def test_claim_records_worker_and_restart_recovers_only_own_jobs(storage):
    _, ids = seed(storage, n_subs=3)
    with TestSession() as s:
        a = queue.claim_next(s, "worker-a")
        b = queue.claim_next(s, "worker-b")
        c = queue.claim_next(s)  # unnamed claimer, e.g. older code
        assert (a.claimed_by, b.claimed_by, c.claimed_by) == ("worker-a", "worker-b", None)

        assert queue.recover_own(s, "worker-z") == 0  # someone else's restart touches nothing
        assert queue.recover_own(s, "worker-a") == 1  # worker-a restarted: its job is requeued at once
        s.expire_all()
        assert s.get(Submission, a.id).status == SubmissionStatus.queued
        assert s.get(Submission, a.id).claimed_by is None and s.get(Submission, a.id).claimed_at is None
        assert s.get(Submission, b.id).status == SubmissionStatus.grading  # still running elsewhere
        assert queue.claim_next(s, "worker-a").id == a.id  # and it is claimable again


def test_recover_own_ignores_finished_jobs(storage):
    seed(storage)
    run_once(FakeProvider(), storage, SETTINGS, session_factory=TestSession, wid="worker-a")
    with TestSession() as s:
        assert queue.recover_own(s, "worker-a") == 0  # ready_for_review stays ready_for_review
        assert s.query(Submission).one().status == SubmissionStatus.ready_for_review


def test_regrade_replaces_ai_output(storage):
    _, (sid,) = seed(storage)
    run(storage)
    with TestSession() as s:
        s.get(Submission, sid).status = SubmissionStatus.queued
        s.commit()
    run(storage)
    with TestSession() as s:
        assert s.query(Page).count() == 3 and s.query(Grade).count() == 2
        assert s.query(GradingRun).count() == 2


# --- admin endpoints ---


def test_queue_endpoint(client, storage):
    exam_id = make_exam(client)
    with TestSession() as s:
        st = Student(external_id="s1")
        s.add(st)
        s.flush()
        for status in (SubmissionStatus.uploaded, SubmissionStatus.failed, SubmissionStatus.ready_for_review):
            st = Student(external_id=f"x-{status.value}")
            s.add(st)
            s.flush()
            s.add(Submission(exam_id=exam_id, student_id=st.id, pdf_path="p", status=status))
        s.commit()
    assert client.post(f"/exams/{exam_id}/queue").status_code == 409  # no rubric yet
    client.post(f"/exams/{exam_id}/rubrics", json={"questions": [{"number": "1", "max_points": 1}]})
    assert client.post(f"/exams/{exam_id}/queue").json() == {"queued": 2}
    statuses = sorted(x["status"] for x in client.get("/submissions").json())
    assert statuses == ["queued", "queued", "ready_for_review"]
    assert client.post("/exams/999/queue").status_code == 404


def test_regrade_endpoint_rules(client, storage):
    exam_id = make_exam(client)
    client.post(f"/exams/{exam_id}/rubrics", json={"questions": [{"number": "1", "max_points": 1}]})
    client.post(f"/exams/{exam_id}/rubrics", json={"questions": [{"number": "1", "max_points": 2}]})
    _, _ = None, None
    with TestSession() as s:
        st = Student(external_id="s1")
        s.add(st)
        s.flush()
        sub = Submission(exam_id=exam_id, student_id=st.id, pdf_path="p", status=SubmissionStatus.ready_for_review)
        s.add(sub)
        s.flush()
        q = s.query(Question).first()
        s.add(Grade(submission_id=sub.id, question_id=q.id, ai_score=1, status=GradeStatus.reviewed))
        s.commit()
        sid = sub.id

    assert client.post(f"/submissions/{sid}/regrade", json={}).status_code == 409  # reviewed grades
    assert client.post(f"/submissions/{sid}/regrade", json={"force": True, "rubric_version": 7}).status_code == 422
    r = client.post(f"/submissions/{sid}/regrade", json={"force": True, "rubric_version": 1})
    assert r.status_code == 200
    with TestSession() as s:
        sub = s.get(Submission, sid)
        assert sub.status == SubmissionStatus.queued
        assert s.get(Rubric, sub.rubric_id).version == 1
    assert client.post(f"/submissions/{sid}/regrade", json={"force": True}).status_code == 409  # queued
    assert client.post("/submissions/999/regrade", json={}).status_code == 404


def test_ta_cannot_queue_or_regrade(client, storage):
    exam_id = make_exam(client)
    client.post("/auth/logout")
    login(client, "ta@x.io")
    assert client.post(f"/exams/{exam_id}/queue").status_code == 403
    assert client.post("/submissions/1/regrade", json={}).status_code == 403


def _seed_subs(client, exam_id, statuses):
    ids = []
    with TestSession() as s:
        for i, status in enumerate(statuses):
            st = Student(external_id=f"b{i}")
            s.add(st)
            s.flush()
            sub = Submission(exam_id=exam_id, student_id=st.id, pdf_path="p", status=status)
            s.add(sub)
            s.flush()
            ids.append(sub.id)
        s.commit()
    return ids


def test_bulk_grade_selected(client, storage):
    exam_id = make_exam(client)
    a, b, c, d = _seed_subs(client, exam_id, [SubmissionStatus.uploaded, SubmissionStatus.failed,
                                              SubmissionStatus.grading, SubmissionStatus.uploaded])
    assert client.post("/submissions/bulk/grade", json={"submission_ids": [a]}).status_code == 409  # no rubric
    client.post(f"/exams/{exam_id}/rubrics", json={"questions": [{"number": "1", "max_points": 1}]})
    r = client.post("/submissions/bulk/grade", json={"submission_ids": [a, b, c]})
    assert r.json() == {"queued": 2, "skipped": 1}
    status = {x["id"]: x["status"] for x in client.get("/submissions").json()}
    assert status[a] == status[b] == "queued" and status[c] == "grading" and status[d] == "uploaded"
    assert client.post("/submissions/bulk/grade", json={"submission_ids": [a, 999]}).status_code == 404
    assert client.post("/submissions/bulk/grade", json={"submission_ids": []}).status_code == 422


def test_bulk_assignment_modes(client, storage):
    exam_id = make_exam(client)
    a, b = _seed_subs(client, exam_id, [SubmissionStatus.uploaded] * 2)
    from app.models import User
    with TestSession() as s:
        ta = s.query(User).filter_by(email="ta@x.io").one().id
    put = lambda **kw: client.put("/submissions/bulk/assignment", json={"submission_ids": [a, b], **kw})  # noqa: E731
    assert put(ta_ids=[ta]).json() == {"updated": 2}
    ta_ids = lambda: [x["ta_ids"] for x in client.get("/submissions").json()]  # noqa: E731
    assert ta_ids() == [[ta], [ta]]
    assert put(ta_ids=[ta], mode="add").status_code == 200 and ta_ids() == [[ta], [ta]]  # no duplicates
    assert put(ta_ids=[ta], mode="remove").status_code == 200 and ta_ids() == [[], []]
    assert put(ta_ids=[], mode="add").status_code == 422
    assert put(ta_ids=[9999]).status_code == 422
    assert client.put("/submissions/bulk/assignment", json={"submission_ids": [999], "ta_ids": []}).status_code == 404


def test_bulk_delete(client, storage):
    exam_id = make_exam(client)
    a, b, c = _seed_subs(client, exam_id, [SubmissionStatus.uploaded, SubmissionStatus.ready_for_review,
                                           SubmissionStatus.grading])
    with TestSession() as s:
        q = Question(rubric=Rubric(exam_id=exam_id, version=1), number="1", max_points=1)
        s.add(q)
        s.flush()
        s.add(Grade(submission_id=b, question_id=q.id, ai_score=1, status=GradeStatus.reviewed))
        s.commit()
    post = lambda ids, **kw: client.post("/submissions/bulk/delete", json={"submission_ids": ids, **kw})  # noqa: E731
    assert post([a, 999]).status_code == 404
    assert post([a, c]).status_code == 409  # one is being graded: nothing deleted
    assert post([a, b]).status_code == 409  # TA-reviewed grades need force
    assert len(client.get("/submissions").json()) == 3
    assert post([a, b], force=True).json() == {"deleted": 2}
    assert [x["id"] for x in client.get("/submissions").json()] == [c]


def test_export_zip_filters(client, storage):
    import io
    import zipfile
    from app.main import app
    from app.storage import get_storage
    app.dependency_overrides[get_storage] = lambda: storage
    exam_id = make_exam(client)
    a, b = _seed_subs(client, exam_id, [SubmissionStatus.uploaded, SubmissionStatus.ready_for_review])
    with TestSession() as s:
        for sid in (a, b):
            sub = s.get(Submission, sid)
            sub.pdf_path = f"x{sid}.pdf"
            storage.save(sub.pdf_path, make_pdf())
        s.commit()
    names = lambda r: sorted(zipfile.ZipFile(io.BytesIO(r.content)).namelist())  # noqa: E731
    assert names(client.get(f"/exams/{exam_id}/export.zip")) == ["b0.pdf", "b1.pdf"]
    assert names(client.get(f"/exams/{exam_id}/export.zip?ids={b}")) == ["b1.pdf"]
    assert names(client.get(f"/exams/{exam_id}/export.zip?ungraded=true")) == ["b0.pdf"]
    assert client.get(f"/exams/{exam_id}/export.zip?ids=x").status_code == 422
    # Raw vs graded single-paper downloads: an ungraded paper gets the original from both; a graded one an annotated copy.
    raw_a, exp_a = client.get(f"/submissions/{a}/raw.pdf"), client.get(f"/submissions/{a}/export.pdf")
    assert raw_a.status_code == 200 and exp_a.content == raw_a.content == storage.read(f"x{a}.pdf")
    raw_b, exp_b = client.get(f"/submissions/{b}/raw.pdf"), client.get(f"/submissions/{b}/export.pdf")
    assert raw_b.content == storage.read(f"x{b}.pdf") and exp_b.content != raw_b.content
    assert client.get("/submissions/999/raw.pdf").status_code == 404
