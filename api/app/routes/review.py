import csv
import io
import re
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app import pdf
from app.auth import current_user, require_admin
from app.db import get_db
from app.grader.base import score_floor
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
    final_score: float  # may be negative; the range is checked against the question below
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
    if body.final_score > max_points or body.final_score < score_floor(max_points):
        raise HTTPException(422, f"Score must be between {score_floor(max_points):g} and {max_points:g}")

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


def _safe_filename(value: str, fallback: str = "paper") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return cleaned or fallback


def _to_comment(g: Grade) -> pdf.QuestionComment:
    score = g.final_score if g.final_score is not None else g.ai_score
    feedback = g.final_feedback if g.final_feedback is not None else g.ai_feedback
    return pdf.QuestionComment(
        question_number=g.question.number,
        score=score,
        max_points=g.question.max_points,
        feedback=feedback or "",
    )


def _annotate_submission_pdf(
    raw_pdf: bytes, pages: list[Page], grades: list[Grade]
) -> bytes:
    ordered_grades = sorted(grades, key=lambda g: (g.question.position, g.question_id))
    by_num = {g.question.number: g for g in ordered_grades}
    placed: set[str] = set()
    page_comments: dict[int, list[pdf.QuestionComment]] = {}

    for p in sorted(pages, key=lambda x: x.index):
        for qnum in p.question_numbers or []:
            if qnum in by_num and qnum not in placed:
                page_comments.setdefault(p.index, []).append(_to_comment(by_num[qnum]))
                placed.add(qnum)

    unplaced = [g for g in ordered_grades if g.question.number not in placed]
    if unplaced:
        target_page = max((p.index for p in pages), default=1)
        page_comments.setdefault(target_page, []).extend(_to_comment(g) for g in unplaced)

    return pdf.annotate_pdf(raw_pdf, page_comments)


def _section_of(number: str) -> str | None:
    """'II.3' -> 'II'. Question numbers without a dot have no section."""
    return number.split(".", 1)[0] if "." in number else None


def _sections(grades: list[Grade]) -> list[pdf.SectionScore]:
    """Group marks by section (the part of the question number before the first dot), in rubric order."""
    ordered = sorted(grades, key=lambda g: (g.question.position, g.question_id))
    groups: dict[str, list[Grade]] = {}
    for g in ordered:
        sec = _section_of(g.question.number)
        groups.setdefault("All questions" if sec is None and not any(
            _section_of(x.question.number) for x in ordered) else ("Other questions" if sec is None else f"Section {sec}"), []).append(g)
    out = []
    for name, gs in groups.items():
        qs = tuple(
            (g.question.number, g.final_score if g.final_score is not None else g.ai_score, g.question.max_points)
            for g in gs
        )
        out.append(pdf.SectionScore(name, sum(s or 0 for _, s, _ in qs), sum(m for _, _, m in qs), qs))
    return out


def _export_pdf(sub: Submission, exam_name: str, raw_pdf: bytes, pages: list[Page], grades: list[Grade]) -> bytes:
    annotated = _annotate_submission_pdf(raw_pdf, pages, grades)
    student = sub.student.external_id + (f" · {sub.student.name}" if sub.student.name else "")
    unreviewed = any(g.status == GradeStatus.pending for g in grades)
    note = "Some marks are Claude's suggestions that no TA has reviewed yet." if unreviewed else ""
    return pdf.add_cover_page(annotated, exam_name, student, _sections(grades), note)


@router.get("/submissions/{submission_id}/export.pdf")
def export_submission_pdf(
    sub: Submission = Depends(get_visible_submission),
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
):
    if not storage.exists(sub.pdf_path):
        raise HTTPException(404, "PDF not found")
    pages = db.scalars(select(Page).where(Page.submission_id == sub.id).order_by(Page.index)).all()
    grades = db.scalars(
        select(Grade).where(Grade.submission_id == sub.id).options(selectinload(Grade.question))
    ).all()
    exam = db.get(Exam, sub.exam_id)
    annotated = _export_pdf(sub, exam.name if exam else f"Exam {sub.exam_id}", storage.read(sub.pdf_path), list(pages), list(grades))
    stem = _safe_filename(sub.student.external_id, fallback=f"submission-{sub.id}")
    return Response(
        annotated,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{stem}.pdf"'},
    )


@router.get("/exams/{exam_id}/export.zip", dependencies=[Depends(require_admin)])
def export_exam_pdfs(
    exam_id: int,
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
):
    exam = db.get(Exam, exam_id)
    if not exam:
        raise HTTPException(404, "Exam not found")
    subs = db.scalars(
        select(Submission)
        .where(Submission.exam_id == exam_id)
        .options(
            selectinload(Submission.student),
            selectinload(Submission.pages),
            selectinload(Submission.grades).selectinload(Grade.question),
        )
        .order_by(Submission.id)
    ).all()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        used_names: set[str] = set()
        for s in subs:
            if not storage.exists(s.pdf_path):
                continue
            annotated = _export_pdf(s, exam.name, storage.read(s.pdf_path), s.pages, s.grades)
            base = _safe_filename(s.student.external_id, fallback=f"submission-{s.id}")
            name = f"{base}.pdf"
            if name in used_names:
                name = f"{base}-{s.id}.pdf"
            used_names.add(name)
            zf.writestr(name, annotated)

    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="exam-{exam_id}-papers.zip"'},
    )
