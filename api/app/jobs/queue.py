"""DB-backed job queue: a submission with status `queued` is a pending grading job."""

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger("worker.queue")

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, selectinload

from app import pdf
from app.config import Settings
from app.grader.base import GraderProvider, PageImage, QuestionSpec, RubricSpec
from app.models import (
    Exam,
    Grade,
    GradingRun,
    Page,
    Rubric,
    SplitJob,
    SplitJobStatus,
    Submission,
    SubmissionStatus,
)
from app.storage import Storage


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def claim_next(db: Session, worker_id: str | None = None) -> Submission | None:
    """Atomically take the oldest queued job. SKIP LOCKED lets many workers claim concurrently."""
    sub = db.scalar(
        select(Submission)
        .where(Submission.status == SubmissionStatus.queued)
        .order_by(Submission.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if sub is None:
        return None
    sub.status = SubmissionStatus.grading
    sub.claimed_at = utcnow()
    sub.claimed_by = worker_id
    sub.error = None
    db.commit()
    return sub


def recover_stale(db: Session, older_than: timedelta) -> int:
    """Requeue jobs whose worker died mid-grading or mid-splitting."""
    cutoff = utcnow() - older_than
    r1 = db.execute(
        update(Submission)
        .where(Submission.status == SubmissionStatus.grading, Submission.claimed_at < cutoff)
        .values(status=SubmissionStatus.queued, claimed_at=None, claimed_by=None)
    )
    r2 = db.execute(
        update(SplitJob)
        .where(SplitJob.status == SplitJobStatus.processing, SplitJob.claimed_at < cutoff)
        .values(status=SplitJobStatus.queued, claimed_at=None, claimed_by=None)
    )
    db.commit()
    return r1.rowcount + r2.rowcount


def recover_own(db: Session, worker_id: str) -> int:
    """At worker start-up: requeue jobs this worker had claimed before it was restarted.

    Safe because no thread of this worker is running yet. It relies on worker ids being unique per
    running worker (a container's hostname, or WORKER_ID). Anything else still falls back to the
    slower recover_stale.
    """
    r1 = db.execute(
        update(Submission)
        .where(Submission.status == SubmissionStatus.grading, Submission.claimed_by == worker_id)
        .values(status=SubmissionStatus.queued, claimed_at=None, claimed_by=None)
    )
    r2 = db.execute(
        update(SplitJob)
        .where(SplitJob.status == SplitJobStatus.processing, SplitJob.claimed_by == worker_id)
        .values(status=SplitJobStatus.queued, claimed_at=None, claimed_by=None)
    )
    db.commit()
    return r1.rowcount + r2.rowcount

def _rubric_for(db: Session, sub: Submission) -> Rubric | None:
    if sub.rubric_id:
        return db.get(Rubric, sub.rubric_id)
    return db.scalar(
        select(Rubric)
        .where(Rubric.exam_id == sub.exam_id)
        .options(selectinload(Rubric.questions))
        .order_by(Rubric.version.desc())
        .limit(1)
    )


def _fail(db: Session, sub: Submission, settings: Settings, provider: GraderProvider, model: str, msg: str):
    log.warning("submission %s failed (provider=%s model=%s): %s", sub.id, provider.name, model, msg)
    db.rollback()
    sub = db.merge(sub)
    sub.status = SubmissionStatus.failed
    sub.error = msg
    db.add(GradingRun(submission_id=sub.id, provider=provider.name, model=model, error=msg))
    db.commit()


def process(db: Session, sub: Submission, provider: GraderProvider, storage: Storage, settings: Settings) -> None:
    """Render, grade and persist one claimed submission. Never raises; failures mark it `failed`."""
    exam = db.get(Exam, sub.exam_id)
    model = (exam.model_override if exam else None) or settings.claude_model
    try:
        rubric = _rubric_for(db, sub)
        if rubric is None or not rubric.questions:
            return _fail(db, sub, settings, provider, model, "Exam has no rubric")
        spec = RubricSpec(
            questions=[
                QuestionSpec(
                    number=q.number,
                    text=q.text,
                    max_points=q.max_points,
                    expected_answer=q.expected_answer,
                    criteria=q.criteria or [],
                )
                for q in rubric.questions
            ]
        )
        question_ids = {q.number: q.id for q in rubric.questions}
        rubric_id = rubric.id
        pdf_bytes = storage.read(sub.pdf_path)
        images = [
            PageImage(index=i, png=png)
            for i, png in enumerate(pdf.render_pages(pdf_bytes, dpi=settings.render_dpi), start=1)
        ]
        # Don't sit in an open transaction for the minutes the model call can take.
        db.rollback()

        outcome = provider.grade(images, spec, model)
    except Exception as e:  # GraderError, PdfError, storage errors, ...
        return _fail(db, sub, settings, provider, model, str(e) or type(e).__name__)

    try:
        res = outcome.result
        page_keys = {}
        for img in images:
            key = f"pages/{sub.id}/{img.index}.png"
            storage.save(key, img.png)
            page_keys[img.index] = key

        sub = db.merge(sub)
        db.execute(delete(Page).where(Page.submission_id == sub.id))
        db.execute(delete(Grade).where(Grade.submission_id == sub.id))
        for p in res.pages:
            db.add(
                Page(
                    submission_id=sub.id,
                    index=p.page,
                    image_path=page_keys[p.page],
                    transcription=p.transcription,
                    question_numbers=p.question_numbers,
                    confidence=p.confidence,
                )
            )
        for q in res.questions:
            db.add(
                Grade(
                    submission_id=sub.id,
                    question_id=question_ids[q.question_number],
                    ai_score=q.score,
                    ai_feedback=q.feedback,
                    ai_confidence=q.confidence,
                )
            )
        db.add(
            GradingRun(
                submission_id=sub.id,
                provider=provider.name,
                model=outcome.model or model,
                raw_output=outcome.raw_output,
            )
        )
        sub.rubric_id = rubric_id
        sub.overall_confidence = res.overall_confidence
        sub.status = SubmissionStatus.ready_for_review
        sub.error = None
        db.commit()
    except Exception as e:
        _fail(db, sub, settings, provider, model, f"Saving results failed: {e}")


def claim_next_split(db: Session, worker_id: str | None = None) -> SplitJob | None:
    """Atomically take the oldest queued PDF auto-split job."""
    job = db.scalar(
        select(SplitJob)
        .where(SplitJob.status == SplitJobStatus.queued)
        .order_by(SplitJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    job.status = SplitJobStatus.processing
    job.claimed_at = utcnow()
    job.claimed_by = worker_id
    job.error = None
    db.commit()
    return job


def process_split(db: Session, job: SplitJob, provider: GraderProvider, storage: Storage, settings: Settings) -> None:
    """Render pages and run Claude auto-split for one claimed SplitJob. Never raises."""
    job_id, exam_id, pdf_path = job.id, job.exam_id, job.pdf_path
    allowed_rolls = list(job.allowed_roll_numbers or [])
    exam = db.get(Exam, exam_id)
    model = settings.claude_split_model or (exam.model_override if exam else None) or settings.claude_model
    try:
        pdf_bytes = storage.read(pdf_path)
        images = [
            PageImage(index=i, png=png)
            for i, png in enumerate(pdf.render_pages(pdf_bytes, dpi=100), start=1)
        ]
        db.rollback()
        result = provider.propose_split(images, model, allowed_roll_numbers=allowed_rolls or None)
        fresh = db.get(SplitJob, job_id)
        if fresh is None:
            return
        fresh.splits = [sp.model_dump() for sp in result.splits]
        fresh.status = SplitJobStatus.ready
        fresh.error = None
        db.commit()
    except Exception as e:
        msg = str(e) or type(e).__name__
        log.warning("split job %s failed (exam=%s model=%s): %s", job_id, exam_id, model, msg)
        db.rollback()
        fresh = db.get(SplitJob, job_id)
        if fresh is None:
            return
        fresh.status = SplitJobStatus.failed
        fresh.error = msg
        db.commit()
