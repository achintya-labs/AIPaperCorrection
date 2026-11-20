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
