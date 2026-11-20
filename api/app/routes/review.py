import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.auth import current_user, require_admin
from app.db import get_db
from app.models import Exam, Grade, GradeStatus, Page, Submission, SubmissionStatus, User
from app.routes.exams import latest_rubric
from app.routes.submissions import SubmissionOut, get_visible_submission, visible_submissions
from app.storage import Storage, get_storage

router = APIRouter(tags=["review"])

REVIEWABLE = (SubmissionStatus.ready_for_review, SubmissionStatus.reviewed)


class PageOut(BaseModel):
    index: int
    question_numbers: list[str]
    transcription: str
    confidence: float | None

    model_config = {"from_attributes": True}


class GradeOut(BaseModel):
    id: int
    question_id: int
    question_number: str
    question_text: str
    expected_answer: str
    criteria: list
    max_points: float
    ai_score: float | None
    ai_feedback: str
    ai_confidence: float | None
    final_score: float | None
    final_feedback: str | None
    status: GradeStatus
    version: int
    reviewed_by: int | None


class SubmissionDetail(SubmissionOut):
    error: str | None
    pages: list[PageOut]
    grades: list[GradeOut]


class GradeUpdate(BaseModel):
    version: int
    final_score: float = Field(ge=0)
    final_feedback: str


def grade_out(g: Grade) -> GradeOut:
    q = g.question
    return GradeOut(
        id=g.id,
        question_id=q.id,
        question_number=q.number,
        question_text=q.text,
        expected_answer=q.expected_answer,
        criteria=q.criteria or [],
        max_points=q.max_points,
        ai_score=g.ai_score,
        ai_feedback=g.ai_feedback,
        ai_confidence=g.ai_confidence,
        final_score=g.final_score,
        final_feedback=g.final_feedback,
        status=g.status,
        version=g.version,
        reviewed_by=g.reviewed_by,
    )


@router.get("/submissions/{submission_id}/detail", response_model=SubmissionDetail)
def submission_detail(sub: Submission = Depends(get_visible_submission), db: Session = Depends(get_db)):
    grades = db.scalars(
        select(Grade).where(Grade.submission_id == sub.id).options(selectinload(Grade.question))
    ).all()
    # Same order as the rubric. A paper's grades all belong to one rubric version, so positions compare.
    grades.sort(key=lambda g: (g.question.position, g.question_id))
    pages = db.scalars(select(Page).where(Page.submission_id == sub.id).order_by(Page.index)).all()
    base = SubmissionOut.model_validate(sub).model_dump()
    return SubmissionDetail(
        **base,
        error=sub.error,
        pages=[PageOut.model_validate(p) for p in pages],
        grades=[grade_out(g) for g in grades],
    )


@router.get("/submissions/{submission_id}/pages/{index}/image")
def page_image(
    index: int,
    sub: Submission = Depends(get_visible_submission),
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
):
    page = db.scalar(select(Page).where(Page.submission_id == sub.id, Page.index == index))
    if not page or not storage.exists(page.image_path):
        raise HTTPException(404, "Page not found")
    return Response(
        storage.read(page.image_path),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.patch("/grades/{grade_id}", response_model=GradeOut)
def update_grade(
    grade_id: int,
    body: GradeUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    grade = db.get(Grade, grade_id)
    # Same 404 for "missing" and "not yours" so ids can't be probed.
    sub = grade and db.scalar(visible_submissions(user).where(Submission.id == grade.submission_id))
    if not grade or not sub:
        raise HTTPException(404, "Grade not found")
    if sub.status not in REVIEWABLE:
        raise HTTPException(409, f"Submission is {sub.status.value}; it can't be reviewed yet")
    max_points = grade.question.max_points
    if body.final_score > max_points:
        raise HTTPException(422, f"Score cannot exceed {max_points:g}")

    unchanged = (
        grade.ai_score is not None
        and abs(body.final_score - grade.ai_score) < 1e-9
        and body.final_feedback == grade.ai_feedback
    )
    # Compare-and-set on version: of two TAs editing the same grade, the second gets a 409.
    result = db.execute(
        update(Grade)
        .where(Grade.id == grade_id, Grade.version == body.version)
        .values(
            final_score=body.final_score,
            final_feedback=body.final_feedback,
            status=GradeStatus.reviewed if unchanged else GradeStatus.overridden,
            reviewed_by=user.id,
            reviewed_at=datetime.now(timezone.utc).replace(tzinfo=None),
            version=Grade.version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.refresh(grade)
    if result.rowcount == 0:
        raise HTTPException(
            409,
            detail={
                "message": "This grade was changed by someone else. Review their edit and try again.",
                "current": grade_out(grade).model_dump(mode="json"),
            },
        )
    return grade_out(grade)


@router.post("/submissions/{submission_id}/complete", response_model=SubmissionOut)
def complete_review(sub: Submission = Depends(get_visible_submission), db: Session = Depends(get_db)):
    if sub.status not in REVIEWABLE:
        raise HTTPException(409, f"Submission is {sub.status.value}; it can't be completed")
    grades = db.scalars(select(Grade).where(Grade.submission_id == sub.id)).all()
    pending = sum(g.status == GradeStatus.pending for g in grades)
    if not grades or pending:
        raise HTTPException(409, f"{pending or 'No'} grade(s) still need review")
    sub.status = SubmissionStatus.reviewed
    db.commit()
    return sub


def _safe_cell(value) -> str:
    """Stop spreadsheet formula injection from student names/ids that start with = + - @."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@router.get("/exams/{exam_id}/export.csv", dependencies=[Depends(require_admin)])
def export_grades(exam_id: int, db: Session = Depends(get_db)):
    exam = db.get(Exam, exam_id)
    if not exam:
        raise HTTPException(404, "Exam not found")
    rubric = latest_rubric(db, exam_id)
    numbers = [q.number for q in rubric.questions] if rubric else []

    subs = db.scalars(
        select(Submission)
        .where(Submission.exam_id == exam_id)
        .options(selectinload(Submission.student), selectinload(Submission.tas))
        .order_by(Submission.id)
    ).all()
    grades_by_sub: dict[int, dict[str, Grade]] = {}
    for g in db.scalars(
        select(Grade)
        .join(Submission, Grade.submission_id == Submission.id)
        .where(Submission.exam_id == exam_id)
        .options(selectinload(Grade.question))
    ):
        grades_by_sub.setdefault(g.submission_id, {})[g.question.number] = g

    out = io.StringIO()
    w = csv.writer(out)
    # Scores and totals first (stable positions for spreadsheets), then one feedback column per question.
    w.writerow(
        ["student_id", "name", *[f"Q{n}" for n in numbers], "total", "status", "graders", *[f"Q{n} feedback" for n in numbers]]
    )
    for s in subs:
        gs = grades_by_sub.get(s.id, {})
        scores, feedback = [], []
        for n in numbers:
            g = gs.get(n)
            # TA's final values win; fall back to Claude's for grades not yet touched.
            scores.append(None if g is None else (g.final_score if g.final_score is not None else g.ai_score))
            feedback.append("" if g is None else (g.final_feedback if g.final_feedback is not None else g.ai_feedback))
        total = sum(x for x in scores if x is not None) if any(x is not None for x in scores) else None
        w.writerow(
            [
                _safe_cell(s.student.external_id),
                _safe_cell(s.student.name),
                *["" if x is None else f"{x:g}" for x in scores],
                "" if total is None else f"{total:g}",
                s.status.value,
                _safe_cell("; ".join(t.email for t in s.tas)),
                *[_safe_cell(f) for f in feedback],  # free text: guard against formula injection like names
            ]
        )
    return Response(
        out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="exam-{exam_id}-grades.csv"'},
    )
