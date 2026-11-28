import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import stats
from app.auth import current_user, require_permission
from app.db import get_db
from app.models import Exam, Grade, Question, Submission, SubmissionStatus, User
from app.permissions import Permission
from app.routes.exams import latest_rubric, visible_exams
from app.routes.review import _safe_cell

router = APIRouter(prefix="/exams", tags=["overview"])

GRADED = (SubmissionStatus.ready_for_review, SubmissionStatus.reviewed)


class Series(BaseModel):
    count: int  # papers in this series
    mean: float | None  # marks; null when there are none
    std_dev: float | None  # population standard deviation


class Bin(BaseModel):
    start: float
    end: float
    graded: int
    reviewed: int


class Overview(BaseModel):
    exam_id: int
    exam_name: str
    total_papers: int
    max_score: float | None  # marks available (latest rubric, or the largest total seen)
    graded: Series  # Claude's scores, for papers that have been graded
    reviewed: Series  # the TA-approved scores, for papers whose review was completed
    bin_width: float  # the width used for `bins`
    auto_bin_width: float  # the width chosen when none is asked for
    bins: list[Bin]


def _overview(db: Session, exam: Exam, bin_width: float | None) -> Overview:
    totals = {
        row.submission_id: row
        for row in db.execute(
            select(
                Grade.submission_id,
                func.sum(func.coalesce(Grade.ai_score, 0.0)).label("ai"),
                func.sum(func.coalesce(Grade.final_score, Grade.ai_score, 0.0)).label("final"),
                func.sum(Question.max_points).label("max"),
            )
            .join(Question, Grade.question_id == Question.id)
            .join(Submission, Grade.submission_id == Submission.id)
            .where(Submission.exam_id == exam.id)
            .group_by(Grade.submission_id)
        )
    }
    papers = db.execute(select(Submission.id, Submission.status).where(Submission.exam_id == exam.id)).all()
    graded, reviewed = [], []
    for sub_id, status in papers:
        t = totals.get(sub_id)
        if t is None or status not in GRADED:
            continue
        graded.append(float(t.ai))
        if status == SubmissionStatus.reviewed:
            reviewed.append(float(t.final))

    rubric = latest_rubric(db, exam.id)
    candidates = [sum(q.max_points for q in rubric.questions)] if rubric else []
    candidates += [float(t.max) for t in totals.values() if t.max]
    span = max(candidates, default=0.0)

    auto = stats.auto_bin_width(graded or reviewed, span)
    width = bin_width or auto
    try:
        start, count = stats.bin_edges([*graded, *reviewed], span, width)
    except ValueError as e:
        raise HTTPException(422, str(e))
    g_counts = stats.histogram(graded, start, width, count)
    r_counts = stats.histogram(reviewed, start, width, count)
    return Overview(
        exam_id=exam.id,
        exam_name=exam.name,
        total_papers=len(papers),
        max_score=span or None,
        graded=Series(count=len(graded), mean=stats.mean(graded), std_dev=stats.std_dev(graded)),
        reviewed=Series(count=len(reviewed), mean=stats.mean(reviewed), std_dev=stats.std_dev(reviewed)),
        bin_width=width,
        auto_bin_width=auto,
        bins=[
            Bin(start=round(start + i * width, 10), end=round(start + (i + 1) * width, 10), graded=g, reviewed=r)
            for i, (g, r) in enumerate(zip(g_counts, r_counts))
        ],
    )


def _exam_for(db: Session, user: User, exam_id: int) -> Exam:
    # The same scoping as the exam list: a TA gets 404 for an exam they are not assigned to.
    exam = db.scalar(visible_exams(user).where(Exam.id == exam_id))
    if not exam:
        raise HTTPException(404, "Exam not found")
    return exam


@router.get("/{exam_id}/overview", response_model=Overview, dependencies=[Depends(require_permission(Permission.tab_overview))])
def exam_overview(
    exam_id: int,
    bin_width: float | None = Query(None, gt=0, description="Marks per histogram bar; default is chosen from the data"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Statistics and histogram data for one exam. `graded` uses Claude's scores for every graded paper;
    `reviewed` uses the final (TA-approved) scores of papers whose review is complete."""
    return _overview(db, _exam_for(db, user, exam_id), bin_width)


@router.get("/{exam_id}/overview.csv", dependencies=[Depends(require_permission(Permission.tab_overview, Permission.overview_export))])
def export_overview(
    exam_id: int,
    bin_width: float | None = Query(None, gt=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """The same numbers as the overview, as a CSV: a statistics block, then a blank line, then one row per histogram bin."""
    o = _overview(db, _exam_for(db, user, exam_id), bin_width)
    out = io.StringIO()
    w = csv.writer(out)
    fmt = lambda x: "" if x is None else f"{x:.4g}" if isinstance(x, float) else x  # noqa: E731
    w.writerow(["exam", _safe_cell(o.exam_name)])
    w.writerow(["papers_total", o.total_papers])
    w.writerow(["max_score", fmt(o.max_score)])
    w.writerow([])
    w.writerow(["statistic", "graded", "reviewed"])
    w.writerow(["papers", o.graded.count, o.reviewed.count])
    w.writerow(["mean", fmt(o.graded.mean), fmt(o.reviewed.mean)])
    w.writerow(["std_dev", fmt(o.graded.std_dev), fmt(o.reviewed.std_dev)])
    w.writerow([])
    w.writerow(["bin_start", "bin_end", "graded", "reviewed"])
    for b in o.bins:
        w.writerow([f"{b.start:g}", f"{b.end:g}", b.graded, b.reviewed])
    return Response(
        out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="exam-{exam_id}-overview.csv"'},
    )
