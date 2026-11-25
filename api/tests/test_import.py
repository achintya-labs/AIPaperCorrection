import fitz
import pytest

from app import pdf
from app.auth import hash_password
from app.main import app
from app.models import Role, Submission, SubmissionStatus, User
from app.storage import LocalStorage, get_storage
from tests.test_auth import TestSession, client, login  # noqa: F401  (fixture reuse)
from tests.test_exams_scoping import RUBRIC, make_exam


def make_pdf(pages: int = 2) -> bytes:
    doc = fitz.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"page {i}")
    return doc.tobytes()


@pytest.fixture()
def storage(tmp_path):
    st = LocalStorage(tmp_path)
    app.dependency_overrides[get_storage] = lambda: st
    return st


def upload(client, exam_id, files, mapping=None, **form):
    parts = [("files", (name, data, "application/pdf")) for name, data in files]
    if mapping is not None:
        parts.append(("mapping_csv", ("map.csv", mapping.encode(), "text/csv")))
    data = {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in form.items()}
    return client.post(f"/exams/{exam_id}/imports", files=parts, data=data)


def add_tas(*emails):
    with TestSession() as s:
        for e in emails:
            s.add(User(email=e, password_hash=hash_password("pw"), role=Role.ta))
        s.commit()


def test_dry_run_persists_nothing(client, storage):
    exam_id = make_exam(client)
    r = upload(client, exam_id, [("s1.pdf", make_pdf(3))], dry_run=True)
    assert r.status_code == 200
    body = r.json()
    assert body["created"] == 0 and body["items"][0]["pages"] == 3
    assert client.get("/submissions").json() == []


def test_import_by_filename_with_csv_multi_ta(client, storage):
    exam_id = make_exam(client)
    add_tas("ta2@x.io")
    mapping = "student_id,name,ta_emails\ns1,Ann,ta@x.io;ta2@x.io\ns2,Bob,ta@x.io\n"
    r = upload(client, exam_id, [("s1.pdf", make_pdf()), ("s2.pdf", make_pdf())], mapping)
    assert r.status_code == 200 and r.json()["created"] == 2
    subs = {s["student"]["external_id"]: s for s in client.get("/submissions").json()}
    assert subs["s1"]["student"]["name"] == "Ann" and len(subs["s1"]["ta_ids"]) == 2
    assert len(subs["s2"]["ta_ids"]) == 1
    # No rubric yet, so nothing is queued for grading.
    assert subs["s1"]["status"] == "uploaded"
    with TestSession() as s:
        key = s.query(Submission).first().pdf_path
    assert key.startswith(f"submissions/{exam_id}/") and storage.exists(key)


def test_queued_when_rubric_exists(client, storage):
    exam_id = make_exam(client)
    client.post(f"/exams/{exam_id}/rubrics", json=RUBRIC)
    upload(client, exam_id, [("s1.pdf", make_pdf())])
    assert client.get("/submissions").json()[0]["status"] == SubmissionStatus.queued.value


def test_reimport_conflicts_and_bad_pdf(client, storage):
    exam_id = make_exam(client)
    upload(client, exam_id, [("s1.pdf", make_pdf())])
    r = upload(client, exam_id, [("s1.pdf", make_pdf()), ("s2.pdf", b"not a pdf")])
    statuses = {i["student_external_id"]: i["status"] for i in r.json()["items"]}
    assert statuses == {"s1": "conflict", "s2": "error"}
    assert r.json()["created"] == 0


def test_round_robin(client, storage):
    exam_id = make_exam(client)
    add_tas("ta2@x.io", "ta3@x.io")
    files = [(f"s{i}.pdf", make_pdf()) for i in range(3)]
    r = upload(client, exam_id, files, assign="round_robin", reviewers_per_paper=2)
    assert r.json()["created"] == 3
    emails = [tuple(i["ta_emails"]) for i in r.json()["items"]]
    assert emails == [("ta@x.io", "ta2@x.io"), ("ta3@x.io", "ta@x.io"), ("ta2@x.io", "ta3@x.io")]
    assert all(len(set(e)) == 2 for e in emails)


def test_csv_unknown_ta_is_item_error(client, storage):
    exam_id = make_exam(client)
    r = upload(client, exam_id, [("s1.pdf", make_pdf())], "student_id,ta_emails\ns1,ghost@x.io\n")
    assert r.json()["items"][0]["status"] == "error"
    assert client.get("/submissions").json() == []


def test_combined_pdf_page_ranges(client, storage):
    exam_id = make_exam(client)
    # Uneven ranges, with page 1 (cover) and page 5 left unassigned.
    mapping = "student_id,name,first_page,last_page\nA,Ann,2,4\nB,Bob,6,7\n"
    r = upload(client, exam_id, [("scan.pdf", make_pdf(8))], mapping)
    assert r.status_code == 200 and [i["pages"] for i in r.json()["items"]] == [3, 2]


@pytest.mark.parametrize(
    "rows",
    [
        "A,1,3\nB,3,5",  # overlap
        "A,1,9",  # past the end of a 8-page PDF
        "A,4,2",  # last < first
        "A,0,2",  # not 1-based
        "A,x,2",  # not an integer
    ],
)
def test_combined_bad_ranges(client, storage, rows):
    exam_id = make_exam(client)
    mapping = "student_id,first_page,last_page\n" + rows + "\n"
    assert upload(client, exam_id, [("scan.pdf", make_pdf(8))], mapping).status_code == 422
    assert client.get("/submissions").json() == []


def test_combined_needs_single_pdf_and_both_columns(client, storage):
    exam_id = make_exam(client)
    two = [("a.pdf", make_pdf()), ("b.pdf", make_pdf())]
    assert upload(client, exam_id, two, "student_id,first_page,last_page\nA,1,2\n").status_code == 422
    assert upload(client, exam_id, [("a.pdf", make_pdf())], "student_id,first_page\nA,1\n").status_code == 422


def test_ta_cannot_import(client, storage):
    exam_id = make_exam(client)
    client.post("/auth/logout")
    login(client, "ta@x.io")
    assert upload(client, exam_id, [("s1.pdf", make_pdf())]).status_code == 403


def test_pdf_render_and_storage_guard(tmp_path):
    pngs = list(pdf.render_pages(make_pdf(2), dpi=50))
    assert len(pngs) == 2 and pngs[0].startswith(b"\x89PNG")
    with pytest.raises(ValueError):
        LocalStorage(tmp_path).save("../escape.pdf", b"x")


def test_pdf_functions_survive_concurrent_threads():
    from concurrent.futures import ThreadPoolExecutor

    data = make_pdf(4)
    with ThreadPoolExecutor(8) as ex:
        renders = list(ex.map(lambda _: pdf.render_pages(data, dpi=40), range(16)))
        counts = list(ex.map(lambda _: pdf.page_count(data), range(16)))
    assert all(len(r) == 4 for r in renders) and set(counts) == {4}


def test_auto_split_propose_preview_and_commit(client, storage):
    from app.config import Settings
    from app.grader.fake import FakeProvider
    from app.jobs import worker
    from tests.test_auth import TestSession

    exam_id = make_exam(client)
    tas = client.get("/auth/users").json()
    ta_id = next(u["id"] for u in tas if u["email"] == "ta@x.io")

    r = client.post(
        f"/exams/{exam_id}/imports/auto-split",
        files={"file": ("combined.pdf", make_pdf(4), "application/pdf")},
    )
    assert r.status_code == 200
    body = r.json()
    token = body["split_token"]
    assert body["page_count"] == 4 and body["status"] == "queued" and body["splits"] == []

    # Resumable via list endpoint before/after worker runs.
    listed = client.get(f"/exams/{exam_id}/imports/auto-split").json()
    assert len(listed) == 1 and listed[0]["split_token"] == token

    assert worker.run_once(FakeProvider(), storage, Settings(grader_provider="fake"), session_factory=TestSession)
    polled = client.get(f"/exams/{exam_id}/imports/auto-split/{token}").json()
    assert polled["status"] == "ready"
    assert [(s["student_external_id"], s["first_page"], s["last_page"]) for s in polled["splits"]] == [
        ("s1", 1, 2),
        ("s2", 3, 4),
    ]

    # Visual verification page thumbnails work and 404 out of bounds / bad token.
    img = client.get(f"/exams/{exam_id}/imports/auto-split/{token}/pages/1/image")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png" and img.content[:4] == b"\x89PNG"
    assert client.get(f"/exams/{exam_id}/imports/auto-split/{token}/pages/9/image").status_code == 404
    assert client.get(f"/exams/{exam_id}/imports/auto-split/badtoken/pages/1/image").status_code == 404

    # The strip of previews uses small JPEG thumbnails, far lighter than the full page, remembered after the first look.
    thumb = client.get(f"/exams/{exam_id}/imports/auto-split/{token}/pages/1/thumb")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg" and thumb.content[:2] == b"\xff\xd8"
    assert len(thumb.content) < len(img.content) and "max-age=86400" in thumb.headers["cache-control"]
    from unittest.mock import patch
    with patch("app.routes.imports.pdf.render_page", side_effect=AssertionError("should come from the cache")):
        assert client.get(f"/exams/{exam_id}/imports/auto-split/{token}/pages/1/thumb").content == thumb.content
    assert client.get(f"/exams/{exam_id}/imports/auto-split/{token}/pages/9/thumb").status_code == 404
    assert client.get(f"/exams/{exam_id}/imports/auto-split/badtoken/pages/1/thumb").status_code == 404

    # Commit with edited roll numbers and per-paper TA assignment.
    commit = client.post(
        f"/exams/{exam_id}/imports/auto-split/{token}/commit",
        json={
            "items": [
                {"student_external_id": "2024CS01", "student_name": "Ann", "first_page": 1, "last_page": 2, "ta_ids": [ta_id]},
                {"student_external_id": "2024CS02", "student_name": "", "first_page": 3, "last_page": 4, "ta_ids": []},
            ]
        },
    )
    assert commit.status_code == 200
    res = commit.json()
    assert res["created"] == 2
    assert [i["ta_emails"] for i in res["items"]] == [["ta@x.io"], []]
    # Staged PDF and job are cleaned up after commit.
    assert client.get(f"/exams/{exam_id}/imports/auto-split/{token}").status_code == 404
    subs = client.get(f"/submissions?exam_id={exam_id}").json()
    assert len(subs) == 2


def test_auto_split_commit_validation_and_discard(client, storage):
    exam_id = make_exam(client)
    token = client.post(
        f"/exams/{exam_id}/imports/auto-split",
        files={"file": ("combined.pdf", make_pdf(4), "application/pdf")},
    ).json()["split_token"]

    # Overlapping ranges -> 422
    r = client.post(
        f"/exams/{exam_id}/imports/auto-split/{token}/commit",
        json={
            "items": [
                {"student_external_id": "A", "first_page": 1, "last_page": 3, "ta_ids": []},
                {"student_external_id": "B", "first_page": 3, "last_page": 4, "ta_ids": []},
            ]
        },
    )
    assert r.status_code == 422

    # Unknown TA -> 422
    r2 = client.post(
        f"/exams/{exam_id}/imports/auto-split/{token}/commit",
        json={"items": [{"student_external_id": "A", "first_page": 1, "last_page": 4, "ta_ids": [9999]}]},
    )
    assert r2.status_code == 422

    # Discard cleans up job and staged PDF.
    assert client.delete(f"/exams/{exam_id}/imports/auto-split/{token}").status_code == 204
    assert client.get(f"/exams/{exam_id}/imports/auto-split/{token}").status_code == 404


def test_auto_split_retry_after_failure(client, storage):
    from app.config import Settings
    from app.grader.base import GraderError, GraderProvider
    from app.grader.fake import FakeProvider
    from app.jobs import worker
    from tests.test_auth import TestSession

    class FailSplit(GraderProvider):
        name = "fail"
        def _attempt(self, *a, **kw): ...
        def propose_split(self, pages, model):
            raise GraderError("boom")

    exam_id = make_exam(client)
    token = client.post(
        f"/exams/{exam_id}/imports/auto-split",
        files={"file": ("combined.pdf", make_pdf(2), "application/pdf")},
    ).json()["split_token"]

    assert worker.run_once(FailSplit(), storage, Settings(grader_provider="fake"), session_factory=TestSession)
    assert client.get(f"/exams/{exam_id}/imports/auto-split/{token}").json()["status"] == "failed"

    retried = client.post(f"/exams/{exam_id}/imports/auto-split/{token}/retry")
    assert retried.status_code == 200 and retried.json()["status"] == "queued"

    assert worker.run_once(FakeProvider(), storage, Settings(grader_provider="fake"), session_factory=TestSession)
    assert client.get(f"/exams/{exam_id}/imports/auto-split/{token}").json()["status"] == "ready"


def test_auto_split_with_allowed_roll_numbers_txt(client, storage):
    from app.config import Settings
    from app.grader.fake import FakeProvider
    from app.jobs import worker
    from tests.test_auth import TestSession

    exam_id = make_exam(client)
    # Empty txt -> 422
    bad = client.post(
        f"/exams/{exam_id}/imports/auto-split",
        files={
            "file": ("combined.pdf", make_pdf(4), "application/pdf"),
            "roll_numbers_txt": ("rolls.txt", b"\n  \n", "text/plain"),
        },
    )
    assert bad.status_code == 422

    r = client.post(
        f"/exams/{exam_id}/imports/auto-split",
        files={
            "file": ("combined.pdf", make_pdf(4), "application/pdf"),
            "roll_numbers_txt": ("rolls.txt", b"2024CS01\n2024CS02\n2024CS01\n", "text/plain"),
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["allowed_roll_numbers"] == ["2024CS01", "2024CS02"]
    token = body["split_token"]

    assert worker.run_once(FakeProvider(), storage, Settings(grader_provider="fake"), session_factory=TestSession)
    polled = client.get(f"/exams/{exam_id}/imports/auto-split/{token}").json()
    assert [s["student_external_id"] for s in polled["splits"]] == ["2024CS01", "2024CS02"]
