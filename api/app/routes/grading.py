from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.db import get_db
from app.grader import GraderError, get_provider
from app.models import Exam, Grade, GradeStatus, Rubric, Submission, SubmissionStatus

router = APIRouter(tags=["grading"], dependencies=[Depends(require_admin)])


class QueueResult(BaseModel):
    queued: int


class BulkGradeIn(BaseModel):
    submission_ids: list[int] = Field(min_length=1)
    force: bool = False  # allow discarding TA-reviewed grades


class BulkGradeResult(BaseModel):
    queued: int
    skipped: int  # already queued/being graded, or TA-reviewed (without force)


class RegradeIn(BaseModel):
    rubric_version: int | None = None  # None = latest at the time the job runs
    force: bool = False  # allow discarding TA-reviewed grades


def _require_rubric(db: Session, exam_ids: set[int]) -> None:
    for exam_id in exam_ids:
        if db.scalar(select(Rubric.id).where(Rubric.exam_id == exam_id).limit(1)) is None:
            raise HTTPException(409, "Create a rubric before grading papers")


@router.post("/exams/{exam_id}/queue", response_model=QueueResult)
def queue_exam(exam_id: int, db: Session = Depends(get_db)):
    """Grade everything that hasn't been graded: queue every submission that is uploaded or failed."""
    if not db.get(Exam, exam_id):
        raise HTTPException(404, "Exam not found")
    if db.scalar(select(Rubric.id).where(Rubric.exam_id == exam_id).limit(1)) is None:
        raise HTTPException(409, "Create a rubric before queueing submissions")
    result = db.execute(
        update(Submission)
        .where(
            Submission.exam_id == exam_id,
            Submission.status.in_([SubmissionStatus.uploaded, SubmissionStatus.failed]),
        )
        .values(status=SubmissionStatus.queued, error=None)
    )
    db.commit()
    return QueueResult(queued=result.rowcount)


@router.post("/submissions/bulk/grade", response_model=BulkGradeResult)
def grade_selected(body: BulkGradeIn, db: Session = Depends(get_db)):
    """Start grading (or regrading) the chosen papers with the latest rubric. Papers already queued or being
    graded are skipped, and so are papers with TA-reviewed grades unless force=true."""
    ids = set(body.submission_ids)
    subs = db.scalars(select(Submission).where(Submission.id.in_(ids))).all()
    if len(subs) != len(ids):
        raise HTTPException(404, "Submission not found")
    _require_rubric(db, {s.exam_id for s in subs})
    reviewed = set(
        db.scalars(
            select(Grade.submission_id).where(Grade.submission_id.in_(ids), Grade.status != GradeStatus.pending)
        )
    )
    queued = 0
    for sub in subs:
        if sub.status in (SubmissionStatus.queued, SubmissionStatus.grading):
            continue
        if sub.id in reviewed and not body.force:
            continue
        sub.rubric_id = None  # latest rubric at the time the job runs
        sub.status = SubmissionStatus.queued
        sub.error = None
        queued += 1
    db.commit()
    return BulkGradeResult(queued=queued, skipped=len(subs) - queued)


@router.post("/submissions/{submission_id}/regrade", response_model=QueueResult)
def regrade(submission_id: int, body: RegradeIn, db: Session = Depends(get_db)):
    sub = db.get(Submission, submission_id)
    if not sub:
        raise HTTPException(404, "Submission not found")
    _require_rubric(db, {sub.exam_id})
    if sub.status in (SubmissionStatus.queued, SubmissionStatus.grading):
        raise HTTPException(409, "Submission is already queued or being graded")
    reviewed = db.scalar(
        select(Grade.id)
        .where(Grade.submission_id == sub.id, Grade.status != GradeStatus.pending)
        .limit(1)
    )
    if reviewed and not body.force:
        raise HTTPException(409, "TA-reviewed grades would be discarded; pass force=true to proceed")

    rubric_id = None
    if body.rubric_version is not None:
        rubric_id = db.scalar(
            select(Rubric.id).where(Rubric.exam_id == sub.exam_id, Rubric.version == body.rubric_version)
        )
        if rubric_id is None:
            raise HTTPException(422, f"No rubric version {body.rubric_version} for this exam")
    sub.rubric_id = rubric_id
    sub.status = SubmissionStatus.queued
    sub.error = None
    db.commit()
    return QueueResult(queued=1)


class UsageWindow(BaseModel):
    key: str
    label: str
    used_percent: float | None  # 0-100; null when only a status is known
    resets_at: str | None  # ISO-8601
    status: str | None  # allowed | allowed_warning | rejected
    detail: str | None = None


class UsageOut(BaseModel):
    source: str  # "subscription" (plan limits: session + weekly) or "api_key" (per-minute API limits)
    model: str
    checked_at: str
    windows: list[UsageWindow]


@router.post("/grader/usage", response_model=UsageOut)
def grader_usage():
    """Check the remaining quota now. Sends one tiny Haiku request (through the runner), so it is a POST and the UI
    only calls it when an admin presses the button."""
    try:
        return get_provider().usage()
    except GraderError as e:
        raise HTTPException(502, str(e))
    except ValueError as e:  # misconfiguration, as for /grader/status
        raise HTTPException(502, str(e))


@router.get("/grader/status")
def grader_status():
    """Is the grading backend usable? (e.g. is Claude logged in?) Shown as a banner to admins."""
    try:
        return get_provider().status()
    except Exception as e:  # misconfiguration (missing token, unknown provider, ...)
        return {"ready": False, "detail": str(e)}
