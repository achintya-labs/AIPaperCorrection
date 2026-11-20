import csv
import io
import uuid
from enum import Enum
from pathlib import PurePath
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import pdf
from app.auth import require_admin
from app.config import get_settings
from app.db import get_db
from app.models import Exam, Role, Student, Submission, SubmissionStatus, User
from app.routes.exams import latest_rubric
from app.storage import Storage, get_storage

router = APIRouter(tags=["import"], dependencies=[Depends(require_admin)])


class AssignMode(str, Enum):
    csv = "csv"
    round_robin = "round_robin"
    none = "none"


class ImportItem(BaseModel):
    student_external_id: str
    filename: str
    pages: int | None = None
    status: Literal["ok", "conflict", "error"]
    detail: str = ""
    ta_emails: list[str] = []
    submission_id: int | None = None


class ImportResult(BaseModel):
    dry_run: bool
    created: int
    items: list[ImportItem]


class _Row(BaseModel):
    name: str = ""
    ta_emails: list[str] = []
    first_page: int | None = None
    last_page: int | None = None


def _parse_mapping(raw: bytes) -> dict[str, _Row]:
    """
    CSV columns: student_id, name (optional), ta_emails (optional, ';'-separated),
    first_page + last_page (optional pair; 1-based inclusive; present => combined-PDF import).
    """
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    except UnicodeDecodeError:
        raise HTTPException(422, "Mapping CSV must be UTF-8")
    if not reader.fieldnames or "student_id" not in reader.fieldnames:
        raise HTTPException(422, "Mapping CSV needs a 'student_id' column")
    has_first, has_last = "first_page" in reader.fieldnames, "last_page" in reader.fieldnames
    if has_first != has_last:
        raise HTTPException(422, "Mapping CSV needs both 'first_page' and 'last_page', or neither")
    rows: dict[str, _Row] = {}
    for r in reader:
        sid = (r.get("student_id") or "").strip()
        if not sid:
            continue
        if sid in rows:
            raise HTTPException(422, f"Duplicate student_id in CSV: {sid}")
        emails = [e.strip().lower() for e in (r.get("ta_emails") or "").split(";") if e.strip()]
        row = _Row(name=(r.get("name") or "").strip(), ta_emails=emails)
        if has_first:
            try:
                row.first_page, row.last_page = int(r["first_page"]), int(r["last_page"])
            except (TypeError, ValueError):
                raise HTTPException(422, f"Student {sid}: first_page/last_page must be integers")
        rows[sid] = row
    return rows


async def _read_upload(f: UploadFile) -> bytes:
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await f.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"{f.filename} exceeds {get_settings().max_upload_mb} MB")
    return data


@router.post("/exams/{exam_id}/imports", response_model=ImportResult)
async def import_submissions(
    exam_id: int,
    files: list[UploadFile] = File(...),
    mapping_csv: UploadFile | None = File(None),
    assign: AssignMode | None = Form(None),
    reviewers_per_paper: int = Form(1, ge=1),
    dry_run: bool = Form(False),
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
):
    """
    Two layouts:
      - one PDF per student, named `<student_id>.pdf`
      - one combined PDF + a CSV giving each student's `first_page`/`last_page`
    Call with dry_run=true first to preview, then repeat with dry_run=false to commit.
    """
    if not db.get(Exam, exam_id):
        raise HTTPException(404, "Exam not found")

    mapping = _parse_mapping(await _read_upload(mapping_csv)) if mapping_csv else {}
    if assign is None:
        assign = AssignMode.csv if any(r.ta_emails for r in mapping.values()) else AssignMode.none

    # (student_id, filename, pdf bytes | None, pre-existing error)
    sources: list[tuple[str, str, bytes | None, str]] = []
    combined = any(r.first_page is not None for r in mapping.values())
    if combined:
        if len(files) != 1:
            raise HTTPException(422, "Combined import (page ranges in CSV) needs exactly one PDF")
        data = await _read_upload(files[0])
        ids = list(mapping)
        try:
            ranges = [(mapping[i].first_page, mapping[i].last_page) for i in ids]
            parts = await run_in_threadpool(pdf.extract_ranges, data, ranges)
        except pdf.PdfError as e:
            raise HTTPException(422, str(e))
        sources = [(sid, files[0].filename or "combined.pdf", part, "") for sid, part in zip(ids, parts)]
    else:
        seen: set[str] = set()
        for f in files:
            sid = PurePath(f.filename or "").stem.strip()
            data = await _read_upload(f)
            if not sid:
                sources.append(("", f.filename or "", None, "Cannot derive student id from filename"))
            elif sid in seen:
                sources.append((sid, f.filename or "", None, "Duplicate student id in upload"))
            else:
                seen.add(sid)
                sources.append((sid, f.filename or "", data, ""))

    users_by_email = {
        u.email.lower(): u
        for u in db.scalars(select(User).where(User.active, User.role == Role.ta))
    }
    pool = sorted(users_by_email.values(), key=lambda u: u.id)
    if assign == AssignMode.round_robin and not pool:
        raise HTTPException(422, "No active TAs to assign to")
    reviewers = min(reviewers_per_paper, len(pool)) if pool else 0

    existing = {
        s.student.external_id
        for s in db.scalars(select(Submission).where(Submission.exam_id == exam_id))
    }
    rubric = latest_rubric(db, exam_id)

    items: list[ImportItem] = []
    plan: list[tuple[ImportItem, bytes, list[User]]] = []
    rr = 0
    for sid, filename, data, err in sources:
        item = ImportItem(student_external_id=sid, filename=filename, status="ok")
        items.append(item)
        if err:
            item.status, item.detail = "error", err
            continue
        if sid in existing:
            item.status, item.detail = "conflict", "Student already has a submission for this exam"
            continue
        try:
            item.pages = await run_in_threadpool(pdf.page_count, data)
        except pdf.PdfError as e:
            item.status, item.detail = "error", str(e)
            continue

        tas: list[User] = []
        if assign == AssignMode.csv:
            emails = mapping.get(sid, _Row()).ta_emails
            missing = [e for e in emails if e not in users_by_email]
            if not emails or missing:
                item.status = "error"
                item.detail = f"Unknown TA email(s): {', '.join(missing)}" if missing else "No TA in CSV"
                continue
            tas = [users_by_email[e] for e in emails]
        elif assign == AssignMode.round_robin:
            tas = [pool[(rr + i) % len(pool)] for i in range(reviewers)]
            rr += reviewers
        item.ta_emails = [t.email for t in tas]
        plan.append((item, data, tas))

    if dry_run or not plan:
        return ImportResult(dry_run=dry_run, created=0, items=items)

    saved: list[str] = []
    try:
        for item, data, tas in plan:
            student = db.scalar(select(Student).where(Student.external_id == item.student_external_id))
            name = mapping.get(item.student_external_id, _Row()).name
            if student is None:
                student = Student(external_id=item.student_external_id, name=name)
                db.add(student)
                db.flush()
            elif name and not student.name:
                student.name = name
            # Server-generated key: never trust the uploaded filename for storage paths.
            key = f"submissions/{exam_id}/{uuid.uuid4().hex}.pdf"
            storage.save(key, data)
            saved.append(key)
            sub = Submission(
                exam_id=exam_id,
                student_id=student.id,
                pdf_path=key,
                tas=tas,
                # Being queued is what enqueues the job; grading needs a rubric to exist.
                status=SubmissionStatus.queued if rubric else SubmissionStatus.uploaded,
            )
            db.add(sub)
            db.flush()
            item.submission_id = sub.id
        db.commit()
    except Exception:
        db.rollback()
        for key in saved:
            storage.delete(key)
        raise
    return ImportResult(dry_run=False, created=len(plan), items=items)
