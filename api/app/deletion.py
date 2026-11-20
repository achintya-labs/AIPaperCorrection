"""Permanent deletion of papers (and, through them, exams), including their stored files."""

import logging
from collections.abc import Iterable, Iterator

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import Grade, GradeStatus, GradingRun, Page, Submission, SubmissionStatus, submission_tas
from app.storage import Storage

log = logging.getLogger(__name__)
_BATCH = 500  # keeps IN (...) lists small enough for every database


def _batches(ids: list[int]) -> Iterator[list[int]]:
    for i in range(0, len(ids), _BATCH):
        yield ids[i : i + _BATCH]


def lock_for_delete(db: Session, ids: list[int]) -> None:
    """Lock these papers and refuse if a worker is grading one right now.

    A worker that finishes grading a paper deleted underneath it would write results for a paper that no
    longer exists. Locking the rows also stops a worker claiming one of them between this check and the
    delete (claiming skips locked rows).
    """
    busy: list[int] = []
    for batch in _batches(ids):
        rows = db.execute(
            select(Submission.id, Submission.status).where(Submission.id.in_(batch)).with_for_update()
        ).all()
        busy += [r.id for r in rows if r.status == SubmissionStatus.grading]
    if busy:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{len(busy)} paper(s) are being graded right now. Wait for grading to finish "
            "(a stuck job is requeued automatically after a while) and try again.",
        )


def reviewed_grade_count(db: Session, ids: Iterable[int]) -> int:
    total = 0
    for batch in _batches(list(ids)):
        total += db.scalar(
            select(func.count()).select_from(Grade).where(Grade.submission_id.in_(batch), Grade.status != GradeStatus.pending)
        ) or 0
    return total


def delete_submissions(db: Session, ids: list[int]) -> list[str]:
    """Delete papers and everything hanging off them. Returns the storage keys to remove AFTER the commit.

    Students are kept: they may have papers in other exams.
    """
    keys: list[str] = []
    for batch in _batches(ids):
        keys += list(db.scalars(select(Submission.pdf_path).where(Submission.id.in_(batch))))
        keys += list(db.scalars(select(Page.image_path).where(Page.submission_id.in_(batch))))
        db.execute(delete(Grade).where(Grade.submission_id.in_(batch)))
        db.execute(delete(Page).where(Page.submission_id.in_(batch)))
        db.execute(delete(GradingRun).where(GradingRun.submission_id.in_(batch)))
        db.execute(delete(submission_tas).where(submission_tas.c.submission_id.in_(batch)))
        db.execute(delete(Submission).where(Submission.id.in_(batch)))
    return keys


def purge_files(storage: Storage, keys: list[str]) -> None:
    """Best effort: the database is already consistent, a leftover file is only wasted space."""
    for key in keys:
        try:
            storage.delete(key)
        except Exception:
            log.warning("could not delete stored file %s", key, exc_info=True)
