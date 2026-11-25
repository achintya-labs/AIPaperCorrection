import csv
import io
import re
import threading
import uuid
from collections import OrderedDict
from enum import Enum
from pathlib import PurePath
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import pdf
from app.auth import require_admin
from app.config import get_settings
from app.db import get_db
from app.models import Exam, Role, SplitJob, SplitJobStatus, Student, Submission, SubmissionStatus, User
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


_SPLIT_TOKEN = re.compile(r"^[a-f0-9]{32}$")


class ProposedSplit(BaseModel):
    student_external_id: str
    student_name: str = ""
    first_page: int
    last_page: int
    confidence: float | None = None


class AutoSplitResult(BaseModel):
    split_token: str
    filename: str
    page_count: int
    status: SplitJobStatus
    error: str | None = None
    allowed_roll_numbers: list[str] = []
    roll_names: dict[str, str] = {}  # roll number -> name, when the roster CSV had a name column
    splits: list[ProposedSplit]
    existing_student_ids: list[str]


class AutoSplitItemIn(BaseModel):
    student_external_id: str
    student_name: str = ""
    first_page: int = Field(ge=1)
    last_page: int = Field(ge=1)
    ta_ids: list[int] = []


class AutoSplitCommitIn(BaseModel):
    items: list[AutoSplitItemIn] = Field(min_length=1)


def _split_key(exam_id: int, token: str) -> str:
    if not _SPLIT_TOKEN.fullmatch(token):
        raise HTTPException(404, "Split session not found")
    return f"tmp_splits/{exam_id}/{token}.pdf"


def _get_split_job(db: Session, exam_id: int, token: str) -> SplitJob:
    if not _SPLIT_TOKEN.fullmatch(token):
        raise HTTPException(404, "Split session not found")
    job = db.scalar(select(SplitJob).where(SplitJob.exam_id == exam_id, SplitJob.token == token))
    if not job:
        raise HTTPException(404, "Split session not found")
    return job


def _existing_student_ids(db: Session, exam_id: int) -> list[str]:
    return sorted(
        s.student.external_id
        for s in db.scalars(select(Submission).where(Submission.exam_id == exam_id))
    )


def _split_out(job: SplitJob, existing: list[str]) -> AutoSplitResult:
    return AutoSplitResult(
        split_token=job.token,
        filename=job.filename,
        page_count=job.page_count,
        status=job.status,
        error=job.error,
        allowed_roll_numbers=list(job.allowed_roll_numbers or []),
        roll_names=dict(job.roll_names or {}),
        splits=[ProposedSplit(**sp) for sp in (job.splits or [])],
        existing_student_ids=existing,
    )


def _header_key(cell: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", cell.lower()).strip("_")


_ROLL_HEADERS = {"roll_number", "roll_numbers", "roll", "rollno", "roll_no", "student_id", "studentid", "id",
                 "registration_number", "reg_no"}
_NAME_HEADERS = {"name", "student_name", "full_name"}


def _parse_roster_csv(raw: bytes) -> tuple[list[str], dict[str, str]]:
    """The optional roster for auto-split: UTF-8 CSV with either one column (roll numbers) or two (roll number, name).

    A header row is optional (`roll_number` or `student_id`, then `name`). Blank lines are skipped. Anything unclear is a
    422 that names the line, so a typo is caught before Claude spends minutes on it. Returns (roll numbers in file order,
    {roll number: name} for those that have a name).
    """

    def bad(msg: str):
        raise HTTPException(422, f"Roster CSV: {msg}")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        bad("the file must be UTF-8 text (in Excel: Save As, CSV UTF-8)")
    try:
        parsed = [(n, [c.strip() for c in row]) for n, row in enumerate(csv.reader(io.StringIO(text)), start=1)]
    except csv.Error as e:
        bad(f"not valid CSV ({e})")
    rows = [(n, r) for n, r in parsed if any(r)]
    if not rows:
        bad("the file is empty")
    for n, r in rows:
        if any(";" in c or "\t" in c for c in r):
            bad(f"line {n} looks like it uses ';' or tabs as separators; use commas")
        if len(r) > 2:
            bad(f"line {n} has {len(r)} columns; use one (roll number) or two (roll number, name)")

    if _header_key(rows[0][1][0]) in _ROLL_HEADERS:
        n, header = rows.pop(0)
        if len(header) == 2 and header[1] and _header_key(header[1]) not in _NAME_HEADERS:
            bad(f"line {n}: the second column is called {header[1]!r}; name it 'name' (or leave the header off)")
        if not rows:
            bad("it has a header but no roll numbers")

    rolls: list[str] = []
    names: dict[str, str] = {}
    first_line: dict[str, int] = {}
    for n, r in rows:
        roll = r[0]
        if not roll:
            bad(f"line {n}: the roll number is empty")
        if roll in first_line:
            bad(f"line {n}: roll number {roll!r} appears twice (first on line {first_line[roll]})")
        first_line[roll] = n
        rolls.append(roll)
        if len(r) == 2 and r[1]:
            names[roll] = r[1]
    return rolls, names


@router.post("/exams/{exam_id}/imports/auto-split", response_model=AutoSplitResult)
async def auto_split_import(
    exam_id: int,
    file: UploadFile = File(...),
    roll_numbers_csv: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
):
    """Upload one combined PDF (and optionally a roster CSV) and queue a background job for Claude to propose splits
    & roll numbers."""
    if not db.get(Exam, exam_id):
        raise HTTPException(404, "Exam not found")

    allowed_rolls, roll_names = _parse_roster_csv(await _read_upload(roll_numbers_csv)) if roll_numbers_csv else ([], {})
    data = await _read_upload(file)
    try:
        total_pages = await run_in_threadpool(pdf.page_count, data)
    except pdf.PdfError as e:
        raise HTTPException(422, str(e))

    token = uuid.uuid4().hex
    key = _split_key(exam_id, token)
    storage.save(key, data)
    job = SplitJob(
        exam_id=exam_id,
        token=token,
        filename=file.filename or "combined.pdf",
        pdf_path=key,
        page_count=total_pages,
        status=SplitJobStatus.queued,
        allowed_roll_numbers=allowed_rolls,
        roll_names=roll_names,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return _split_out(job, _existing_student_ids(db, exam_id))


@router.get("/exams/{exam_id}/imports/auto-split", response_model=list[AutoSplitResult])
def list_auto_splits(exam_id: int, db: Session = Depends(get_db)):
    if not db.get(Exam, exam_id):
        raise HTTPException(404, "Exam not found")
    jobs = db.scalars(
        select(SplitJob).where(SplitJob.exam_id == exam_id).order_by(SplitJob.id.desc())
    ).all()
    existing = _existing_student_ids(db, exam_id) if jobs else []
    return [_split_out(j, existing) for j in jobs]


@router.get("/exams/{exam_id}/imports/auto-split/{split_token}", response_model=AutoSplitResult)
def get_auto_split(exam_id: int, split_token: str, db: Session = Depends(get_db)):
    job = _get_split_job(db, exam_id, split_token)
    return _split_out(job, _existing_student_ids(db, exam_id))


@router.delete("/exams/{exam_id}/imports/auto-split/{split_token}", status_code=204)
def delete_auto_split(
    exam_id: int,
    split_token: str,
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
):
    job = _get_split_job(db, exam_id, split_token)
    key = job.pdf_path
    db.delete(job)
    db.commit()
    storage.delete(key)


@router.post("/exams/{exam_id}/imports/auto-split/{split_token}/retry", response_model=AutoSplitResult)
def retry_auto_split(exam_id: int, split_token: str, db: Session = Depends(get_db)):
    job = _get_split_job(db, exam_id, split_token)
    if job.status in (SplitJobStatus.queued, SplitJobStatus.processing):
        raise HTTPException(409, "Split job is already queued or processing")
    job.status = SplitJobStatus.queued
    job.error = None
    job.claimed_at = None
    job.claimed_by = None
    db.commit()
    db.refresh(job)
    return _split_out(job, _existing_student_ids(db, exam_id))


# Small previews are rendered at a low resolution as JPEG (about 15 KB instead of 770 KB per page) and remembered in
# memory, so a strip of 150 thumbnails costs a few MB, not 100+ MB, and a second look is free. The browser also keeps
# them for a day (the token never changes what page N looks like).
THUMB_DPI = 30
THUMB_QUALITY = 60
_THUMB_MAX = 2000
_thumbs: "OrderedDict[tuple[str, int], bytes]" = OrderedDict()
_thumbs_lock = threading.Lock()


@router.get("/exams/{exam_id}/imports/auto-split/{split_token}/pages/{index}/thumb")
def auto_split_page_thumb(
    exam_id: int,
    split_token: str,
    index: int,
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
):
    job = _get_split_job(db, exam_id, split_token)
    key = (split_token, index)
    with _thumbs_lock:
        jpeg = _thumbs.get(key)
        if jpeg is not None:
            _thumbs.move_to_end(key)
    if jpeg is None:
        if not storage.exists(job.pdf_path):
            raise HTTPException(404, "Split session not found")
        try:
            jpeg = pdf.render_page(storage.read(job.pdf_path), index, dpi=THUMB_DPI, jpeg_quality=THUMB_QUALITY)
        except pdf.PdfError as e:
            raise HTTPException(404, str(e))
        with _thumbs_lock:
            _thumbs[key] = jpeg
            while len(_thumbs) > _THUMB_MAX:
                _thumbs.popitem(last=False)
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/exams/{exam_id}/imports/auto-split/{split_token}/pages/{index}/image")
def auto_split_page_image(
    exam_id: int,
    split_token: str,
    index: int,
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
):
    job = _get_split_job(db, exam_id, split_token)
    if not storage.exists(job.pdf_path):
        raise HTTPException(404, "Split session not found")
    try:
        png = pdf.render_page(storage.read(job.pdf_path), index, dpi=110)
    except pdf.PdfError as e:
        raise HTTPException(404, str(e))
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@router.post("/exams/{exam_id}/imports/auto-split/{split_token}/commit", response_model=ImportResult)
async def commit_auto_split_import(
    exam_id: int,
    split_token: str,
    body: AutoSplitCommitIn,
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
):
    if not db.get(Exam, exam_id):
        raise HTTPException(404, "Exam not found")
    job = _get_split_job(db, exam_id, split_token)
    if job.status == SplitJobStatus.processing:
        raise HTTPException(409, "Split job is currently processing")
    key = job.pdf_path
    if not storage.exists(key):
        raise HTTPException(404, "Split session not found")

    seen: set[str] = set()
    cleaned_ids: list[str] = []
    for it in body.items:
        sid = it.student_external_id.strip()
        if not sid:
            raise HTTPException(422, "Every paper must have a student roll number / ID")
        if sid in seen:
            raise HTTPException(422, f"Duplicate student_id in split: {sid}")
        seen.add(sid)
        cleaned_ids.append(sid)

    all_ta_ids = {tid for it in body.items for tid in it.ta_ids}
    users_by_id = (
        {
            u.id: u
            for u in db.scalars(select(User).where(User.id.in_(all_ta_ids), User.active, User.role == Role.ta))
        }
        if all_ta_ids
        else {}
    )
    if len(users_by_id) != len(all_ta_ids):
        raise HTTPException(422, "Unknown or inactive TA")

    raw_pdf = storage.read(key)
    ranges = [(it.first_page, it.last_page) for it in body.items]
    try:
        parts = await run_in_threadpool(pdf.extract_ranges, raw_pdf, ranges)
    except pdf.PdfError as e:
        raise HTTPException(422, str(e))

    existing = set(_existing_student_ids(db, exam_id))
    rubric = latest_rubric(db, exam_id)

    items: list[ImportItem] = []
    plan: list[tuple[ImportItem, str, bytes, list[User]]] = []
    for sid, it, part in zip(cleaned_ids, body.items, parts):
        tas = [users_by_id[tid] for tid in dict.fromkeys(it.ta_ids)]
        item = ImportItem(
            student_external_id=sid,
            filename=f"pages {it.first_page}-{it.last_page}",
            pages=it.last_page - it.first_page + 1,
            status="ok",
            ta_emails=[t.email for t in tas],
        )
        items.append(item)
        if sid in existing:
            item.status, item.detail = "conflict", "Student already has a submission for this exam"
            continue
        plan.append((item, it.student_name.strip(), part, tas))

    saved: list[str] = []
    try:
        for item, name, part, tas in plan:
            student = db.scalar(select(Student).where(Student.external_id == item.student_external_id))
            if student is None:
                student = Student(external_id=item.student_external_id, name=name)
                db.add(student)
                db.flush()
            elif name and not student.name:
                student.name = name
            sub_key = f"submissions/{exam_id}/{uuid.uuid4().hex}.pdf"
            storage.save(sub_key, part)
            saved.append(sub_key)
            sub = Submission(
                exam_id=exam_id,
                student_id=student.id,
                pdf_path=sub_key,
                tas=tas,
                status=SubmissionStatus.queued if rubric else SubmissionStatus.uploaded,
            )
            db.add(sub)
            db.flush()
            item.submission_id = sub.id
        db.delete(job)
        db.commit()
    except Exception:
        db.rollback()
        for sub_key in saved:
            storage.delete(sub_key)
        raise

    storage.delete(key)
    return ImportResult(dry_run=False, created=len(plan), items=items)
