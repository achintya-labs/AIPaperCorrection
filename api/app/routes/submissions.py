from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from app.auth import current_user, require_permission
from app.db import get_db
from app import deletion
from app.permissions import Permission, permissions_for
from app.routes.exams import visible_exams
from app.models import Exam, Grade, GradeStatus, Question, Role, Student, Submission, SubmissionStatus, User
from app.storage import Storage, get_storage

router = APIRouter(tags=["submissions"])


class StudentIn(BaseModel):
    external_id: str
    name: str = ""


class StudentPatch(BaseModel):
    external_id: str | None = None  # roll number
    name: str | None = None


class StudentOut(StudentIn):
    id: int

    model_config = {"from_attributes": True}


class SubmissionOut(BaseModel):
    id: int
    exam_id: int
    student: StudentOut
    ta_ids: list[int]
    status: SubmissionStatus
    overall_confidence: float | None
    # Filled in by the list endpoint only.
    grades_total: int = 0
    grades_reviewed: int = 0
    min_confidence: float | None = None
    score_total: float | None = None
    max_score: float | None = None

    model_config = {"from_attributes": True}


class AssignIn(BaseModel):
    ta_ids: list[int]


class BulkDeleteIn(BaseModel):
    submission_ids: list[int] = Field(min_length=1)
    force: bool = False  # allow discarding TA-reviewed grades


class BulkDeleteResult(BaseModel):
    deleted: int


class BulkAssignIn(BaseModel):
    submission_ids: list[int] = Field(min_length=1)
    ta_ids: list[int]
    mode: Literal["replace", "add", "remove", "round_robin"] = "replace"


class BulkAssignResult(BaseModel):
    updated: int


def visible_submissions(user: User):
    """The single place TA scoping is enforced: admins see all, TAs only those they're assigned to."""
    q = select(Submission).options(selectinload(Submission.student), selectinload(Submission.tas))
    if user.role != Role.admin:
        q = q.where(Submission.tas.any(User.id == user.id))
    return q


def get_visible_submission(
    submission_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Submission:
    sub = db.scalar(visible_submissions(user).where(Submission.id == submission_id))
    if not sub:
        # 404 rather than 403 so TAs can't probe for other TAs' submission ids.
        raise HTTPException(404, "Submission not found")
    return sub


@router.post("/students", response_model=list[StudentOut], status_code=201, dependencies=[Depends(require_permission(Permission.papers_manage))])
def upsert_students(body: list[StudentIn], db: Session = Depends(get_db)):
    existing = {
        s.external_id: s
        for s in db.scalars(select(Student).where(Student.external_id.in_([b.external_id for b in body])))
    }
    out = []
    for b in body:
        s = existing.get(b.external_id)
        if s:
            s.name = b.name or s.name
        else:
            s = Student(**b.model_dump())
            db.add(s)
        out.append(s)
    db.commit()
    return out


@router.patch("/students/{student_id}", response_model=StudentOut, dependencies=[Depends(require_permission(Permission.papers_manage))])
def update_student(student_id: int, body: StudentPatch, db: Session = Depends(get_db)):
    """Correct a student's roll number (external_id) and/or name."""
    student = db.get(Student, student_id)
    if not student:
        raise HTTPException(404, "Student not found")
    if body.external_id is None and body.name is None:
        raise HTTPException(422, "Provide external_id and/or name")
    if body.external_id is not None:
        roll = body.external_id.strip()
        if not roll:
            raise HTTPException(422, "Roll number can't be empty")
        clash = db.scalar(select(Student.id).where(Student.external_id == roll, Student.id != student.id))
        if clash is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Another student already has the roll number {roll}")
        student.external_id = roll
    if body.name is not None:
        student.name = body.name.strip()
    db.commit()
    return student


@router.get("/students", response_model=list[StudentOut], dependencies=[Depends(require_permission(Permission.papers_manage))])
def list_students(db: Session = Depends(get_db)):
    return db.scalars(select(Student).order_by(Student.external_id)).all()


@router.get("/submissions", response_model=list[SubmissionOut])
def list_submissions(
    exam_id: int | None = None,
    status: SubmissionStatus | None = None,
    scope: Literal["mine", "exam"] = "mine",
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """`scope=mine` (default): admins all papers, TAs the ones assigned to them (the review queue).
    `scope=exam` (needs `exam_id` and `tab.papers`): every paper of that exam, for the read-only Papers tab; a TA still
    gets 404 for an exam they are not assigned to. Reviewing stays limited to one's own papers either way."""
    if scope == "exam":
        if exam_id is None:
            raise HTTPException(422, "scope=exam needs exam_id")
        if Permission.tab_papers not in permissions_for(user.role):
            raise HTTPException(403, "You don't have permission to do that")
        if not db.scalar(visible_exams(user).where(Exam.id == exam_id)):
            raise HTTPException(404, "Exam not found")
        q = select(Submission).options(selectinload(Submission.student), selectinload(Submission.tas)).order_by(Submission.id)
    else:
        q = visible_submissions(user).order_by(Submission.id)
    if exam_id is not None:
        q = q.where(Submission.exam_id == exam_id)
    if status is not None:
        q = q.where(Submission.status == status)
    subs = db.scalars(q).all()
    progress = {
        row.submission_id: row
        for row in db.execute(
            select(
                Grade.submission_id,
                func.count().label("total"),
                func.sum(case((Grade.status != GradeStatus.pending, 1), else_=0)).label("reviewed"),
                func.min(Grade.ai_confidence).label("min_conf"),
                func.sum(func.coalesce(Grade.final_score, Grade.ai_score, 0.0)).label("score_total"),
                func.sum(Question.max_points).label("max_score"),
            )
            .join(Question, Grade.question_id == Question.id)
            .where(Grade.submission_id.in_([s.id for s in subs]))
            .group_by(Grade.submission_id)
        )
    }
    out = []
    for s in subs:
        item = SubmissionOut.model_validate(s)
        if p := progress.get(s.id):
            item.grades_total, item.grades_reviewed = p.total, int(p.reviewed or 0)
            item.min_confidence = p.min_conf
            if p.total:
                item.score_total = float(p.score_total or 0.0)
                item.max_score = float(p.max_score or 0.0)
        out.append(item)
    return out


@router.get("/submissions/{submission_id}", response_model=SubmissionOut)
def get_submission(sub: Submission = Depends(get_visible_submission)):
    return sub


@router.put(
    "/submissions/bulk/assignment",
    response_model=BulkAssignResult,
    dependencies=[Depends(require_permission(Permission.papers_manage))],
)
def assign_selected(body: BulkAssignIn, db: Session = Depends(get_db)):
    """Assign TAs to several papers at once: replace each paper's TAs, add to them, or remove from them."""
    ids = set(body.submission_ids)
    subs = db.scalars(select(Submission).options(selectinload(Submission.tas)).where(Submission.id.in_(ids))).all()
    if len(subs) != len(ids):
        raise HTTPException(404, "Submission not found")
    ta_ids = set(body.ta_ids)
    if not ta_ids and body.mode != "replace":
        raise HTTPException(422, "Choose at least one TA")
    tas = db.scalars(select(User).where(User.id.in_(ta_ids), User.active)).all() if ta_ids else []
    if len(tas) != len(ta_ids):
        raise HTTPException(422, "Unknown or inactive TA")
    if body.mode == "round_robin":
        # Deal the papers out in turn (by id, so repeating it is predictable), keeping each paper's existing TAs.
        order = sorted(tas, key=lambda t: t.id)
        for i, sub in enumerate(sorted(subs, key=lambda s: s.id)):
            ta = order[i % len(order)]
            if ta not in sub.tas:
                sub.tas = [*sub.tas, ta]
        db.commit()
        return BulkAssignResult(updated=len(subs))
    for sub in subs:
        if body.mode == "replace":
            sub.tas = list(tas)
        elif body.mode == "add":
            sub.tas = [*sub.tas, *(t for t in tas if t not in sub.tas)]
        else:
            sub.tas = [t for t in sub.tas if t.id not in ta_ids]
    db.commit()
    return BulkAssignResult(updated=len(subs))


@router.put(
    "/submissions/{submission_id}/assignment",
    response_model=SubmissionOut,
    dependencies=[Depends(require_permission(Permission.papers_manage))],
)
def assign_submission(
    submission_id: int, body: AssignIn, db: Session = Depends(get_db)
):
    sub = db.get(Submission, submission_id)
    if not sub:
        raise HTTPException(404, "Submission not found")
    ids = set(body.ta_ids)
    tas = db.scalars(select(User).where(User.id.in_(ids), User.active)).all() if ids else []
    if len(tas) != len(ids):
        raise HTTPException(422, "Unknown or inactive TA")
    sub.tas = list(tas)
    db.commit()
    return sub


@router.post("/submissions/bulk/delete", response_model=BulkDeleteResult, dependencies=[Depends(require_permission(Permission.papers_manage))])
def delete_selected(
    body: BulkDeleteIn,
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
):
    """Permanently delete several papers (see DELETE /submissions/{id}). All or nothing: if any is being graded, or
    any holds TA-reviewed grades and force is false, nothing is deleted."""
    ids = sorted(set(body.submission_ids))
    found = set(db.scalars(select(Submission.id).where(Submission.id.in_(ids))))
    if found != set(ids):
        raise HTTPException(404, "Submission not found")
    deletion.lock_for_delete(db, ids)
    reviewed = deletion.reviewed_grade_count(db, ids)
    if reviewed and not body.force:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"These {len(ids)} paper(s) hold {reviewed} TA-reviewed grade(s); deleting them discards that work. "
            "Pass force=true to delete them anyway.",
        )
    keys = deletion.delete_submissions(db, ids)
    db.commit()
    deletion.purge_files(storage, keys)
    return BulkDeleteResult(deleted=len(ids))


@router.delete("/submissions/{submission_id}", status_code=204, dependencies=[Depends(require_permission(Permission.papers_manage))])
def delete_submission(
    submission_id: int,
    force: bool = False,
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
):
    """Permanently delete one paper: its grades, page images, grading history and assignments, and the
    stored PDF. The student record stays. A paper with TA-reviewed grades needs force=true."""
    if not db.get(Submission, submission_id):
        raise HTTPException(404, "Submission not found")
    deletion.lock_for_delete(db, [submission_id])
    reviewed = deletion.reviewed_grade_count(db, [submission_id])
    if reviewed and not force:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This paper has {reviewed} TA-reviewed grade(s); deleting it discards that work. "
            "Pass force=true to delete it anyway.",
        )
    keys = deletion.delete_submissions(db, [submission_id])
    db.commit()
    deletion.purge_files(storage, keys)
