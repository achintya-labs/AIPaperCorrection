from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.auth import current_user, require_permission
from app import deletion, pdf
from app.db import get_db
from app.models import Exam, Question, Role, Rubric, SplitJob, Submission, User, submission_tas
from app.permissions import Permission
from app.storage import Storage, get_storage

router = APIRouter(prefix="/exams", tags=["exams"])


class Criterion(BaseModel):
    description: str
    points: float  # negative = a deduction (negative marking)


class QuestionIn(BaseModel):
    number: str
    text: str = ""
    max_points: float = Field(gt=0)
    expected_answer: str = ""
    criteria: list[Criterion] = []


class QuestionOut(QuestionIn):
    id: int

    model_config = {"from_attributes": True}


class RubricIn(BaseModel):
    questions: list[QuestionIn] = Field(min_length=1)


class RubricOut(BaseModel):
    id: int
    exam_id: int
    version: int
    questions: list[QuestionOut]

    model_config = {"from_attributes": True}


class ExamIn(BaseModel):
    name: str
    model_override: str | None = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Exam name can't be empty")
        return v


class ExamOut(ExamIn):
    id: int

    model_config = {"from_attributes": True}


def _validate_rubric(body: RubricIn) -> None:
    numbers = [q.number for q in body.questions]
    if len(set(numbers)) != len(numbers):
        raise HTTPException(422, "Duplicate question numbers")
    for q in body.questions:
        if sum(c.points for c in q.criteria if c.points > 0) > q.max_points:
            raise HTTPException(422, f"Criteria points exceed max_points for question {q.number}")


def visible_exams(user: User):
    """Admins see every exam; a TA sees only exams in which at least one paper is assigned to them."""
    q = select(Exam).order_by(Exam.id)
    if user.role != Role.admin:
        assigned = (
            select(Submission.exam_id)
            .join(submission_tas, submission_tas.c.submission_id == Submission.id)
            .where(submission_tas.c.user_id == user.id)
        )
        q = q.where(Exam.id.in_(assigned))
    return q


def latest_rubric(db: Session, exam_id: int) -> Rubric | None:
    return db.scalar(
        select(Rubric)
        .where(Rubric.exam_id == exam_id)
        .options(selectinload(Rubric.questions))
        .order_by(Rubric.version.desc())
        .limit(1)
    )


@router.post("", response_model=ExamOut, status_code=201, dependencies=[Depends(require_permission(Permission.exams_create))])
def create_exam(body: ExamIn, db: Session = Depends(get_db)):
    exam = Exam(**body.model_dump())
    db.add(exam)
    db.commit()
    return exam


@router.get("", response_model=list[ExamOut])
def list_exams(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(visible_exams(user)).all()


@router.patch("/{exam_id}", response_model=ExamOut, dependencies=[Depends(require_permission(Permission.tab_advanced))])
def update_exam(exam_id: int, body: ExamIn, db: Session = Depends(get_db)):
    exam = db.get(Exam, exam_id)
    if not exam:
        raise HTTPException(404, "Exam not found")
    for k, v in body.model_dump().items():
        setattr(exam, k, v)
    db.commit()
    return exam


@router.post(
    "/{exam_id}/rubrics",
    response_model=RubricOut,
    status_code=201,
    dependencies=[Depends(require_permission(Permission.rubric_edit))],
)
def create_rubric_version(exam_id: int, body: RubricIn, db: Session = Depends(get_db)):
    """Rubrics are immutable; editing creates a new version so past grading runs stay traceable."""
    if not db.get(Exam, exam_id):
        raise HTTPException(404, "Exam not found")
    _validate_rubric(body)
    version = (db.scalar(select(func.max(Rubric.version)).where(Rubric.exam_id == exam_id)) or 0) + 1
    rubric = Rubric(
        exam_id=exam_id,
        version=version,
        questions=[Question(**q.model_dump(), position=i) for i, q in enumerate(body.questions)],
    )
    db.add(rubric)
    db.commit()
    return rubric


class RubricPdfIn(RubricIn):
    version: int | None = None  # shown in the PDF header; omit for an unsaved draft


@router.post("/{exam_id}/rubric.pdf", dependencies=[Depends(require_permission(Permission.rubric_edit))])
def rubric_pdf(exam_id: int, body: RubricPdfIn, db: Session = Depends(get_db)):
    """A PDF of the rubric JSON you send (so unsaved edits can be printed too), generated on the fly. Nothing is stored."""
    exam = db.get(Exam, exam_id)
    if not exam:
        raise HTTPException(404, "Exam not found")
    _validate_rubric(body)
    data = pdf.rubric_pdf(exam.name, body.version, [q.model_dump() for q in body.questions])
    name = f"rubric-exam-{exam_id}" + (f"-v{body.version}" if body.version else "-draft")
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})


@router.get("/{exam_id}/rubric", response_model=RubricOut, dependencies=[Depends(require_permission(Permission.tab_rubric))])
def get_latest_rubric(exam_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    # The rubric holds model answers, so a TA only gets it for exams they grade (404 otherwise, as elsewhere).
    if not db.scalar(visible_exams(user).where(Exam.id == exam_id)):
        raise HTTPException(404, "Exam not found")
    rubric = latest_rubric(db, exam_id)
    if not rubric:
        raise HTTPException(404, "No rubric for this exam")
    return rubric


@router.delete("/{exam_id}", status_code=204, dependencies=[Depends(require_permission(Permission.exams_delete))])
def delete_exam(
    exam_id: int,
    force: bool = False,
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
):
    """Permanently delete an exam: all rubric versions and every paper in it (grades, page images, stored PDFs).
    An exam that still has papers needs force=true. Students are kept."""
    if not db.get(Exam, exam_id):
        raise HTTPException(404, "Exam not found")
    ids = list(db.scalars(select(Submission.id).where(Submission.exam_id == exam_id)))
    deletion.lock_for_delete(db, ids)
    if ids and not force:
        reviewed = deletion.reviewed_grade_count(db, ids)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This exam has {len(ids)} paper(s), with {reviewed} TA-reviewed grade(s). "
            "Deleting the exam permanently deletes them too. Pass force=true to delete it anyway.",
        )
    keys = deletion.delete_submissions(db, ids)
    split_keys = list(db.scalars(select(SplitJob.pdf_path).where(SplitJob.exam_id == exam_id)))
    db.execute(delete(SplitJob).where(SplitJob.exam_id == exam_id))
    rubric_ids = select(Rubric.id).where(Rubric.exam_id == exam_id)
    db.execute(delete(Question).where(Question.rubric_id.in_(rubric_ids)))
    db.execute(delete(Rubric).where(Rubric.exam_id == exam_id))
    db.execute(delete(Exam).where(Exam.id == exam_id))
    db.commit()
    deletion.purge_files(storage, [*keys, *split_keys])
