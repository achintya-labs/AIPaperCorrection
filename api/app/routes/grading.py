from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.db import get_db
from app.grader import get_provider
from app.models import Exam, Grade, GradeStatus, Rubric, Submission, SubmissionStatus

router = APIRouter(tags=["grading"], dependencies=[Depends(require_admin)])


class QueueResult(BaseModel):
    queued: int


class RegradeIn(BaseModel):
    rubric_version: int | None = None  # None = latest at the time the job runs
    force: bool = False  # allow discarding TA-reviewed grades


@router.post("/exams/{exam_id}/queue", response_model=QueueResult)
def queue_exam(exam_id: int, db: Session = Depends(get_db)):
    """Queue every submission that is waiting (uploaded, e.g. imported before the rubric) or failed."""
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


@router.post("/submissions/{submission_id}/regrade", response_model=QueueResult)
def regrade(submission_id: int, body: RegradeIn, db: Session = Depends(get_db)):
    sub = db.get(Submission, submission_id)
    if not sub:
        raise HTTPException(404, "Submission not found")
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


@router.get("/grader/status")
def grader_status():
    """Is the grading backend usable? (e.g. is Claude logged in?) Shown as a banner to admins."""
    try:
        return get_provider().status()
    except Exception as e:  # misconfiguration (missing token, unknown provider, ...)
        return {"ready": False, "detail": str(e)}
