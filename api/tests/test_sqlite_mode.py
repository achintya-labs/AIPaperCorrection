"""The minimal, no-server mode: DATABASE_URL=sqlite:///file.db (see run.sh --native)."""

import os
import subprocess
import sys
import threading
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app import db as app_db
from app.jobs import queue
from app.models import Exam, Student, Submission, SubmissionStatus

API_DIR = Path(__file__).resolve().parent.parent


def alembic(url, *args):
    env = {**os.environ, "DATABASE_URL": url}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=API_DIR, env=env, capture_output=True, text=True)


def test_migrations_apply_to_an_empty_sqlite_file_and_match_the_models(tmp_path):
    url = f"sqlite:///{tmp_path / 'g.db'}"
    up = alembic(url, "upgrade", "head")
    assert up.returncode == 0, up.stderr
    check = alembic(url, "check")
    assert check.returncode == 0 and "No new upgrade operations" in (check.stdout + check.stderr), check.stderr
    # and the newest migration round-trips
    assert alembic(url, "downgrade", "-1").returncode == 0
    assert alembic(url, "upgrade", "head").returncode == 0


def test_sqlite_engine_is_set_up_for_two_processes_and_foreign_keys(tmp_path):
    engine = app_db.make_engine(f"sqlite:///{tmp_path / 'p.db'}")
    with engine.connect() as c:
        assert c.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert c.execute(text("PRAGMA busy_timeout")).scalar() == 30000


def test_concurrent_workers_never_claim_the_same_job(tmp_path):
    """SQLite ignores FOR UPDATE, so claim_next uses a conditional UPDATE: 8 racing threads get 60 distinct jobs."""
    engine = app_db.make_engine(f"sqlite:///{tmp_path / 'q.db'}")
    app_db.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as s:
        exam = Exam(name="E")
        s.add(exam)
        s.flush()
        for i in range(60):
            st = Student(external_id=f"s{i}")
            s.add(st)
            s.flush()
            s.add(Submission(exam_id=exam.id, student_id=st.id, pdf_path="x.pdf", status=SubmissionStatus.queued))
        s.commit()

    claimed, guard, start = [], threading.Lock(), threading.Barrier(8)

    def worker(name):
        start.wait()
        while True:
            with Session() as db:
                sub = queue.claim_next(db, name)
                if sub is None:
                    return
                assert sub.status == SubmissionStatus.grading and sub.claimed_by == name
                with guard:
                    claimed.append(sub.id)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(claimed) == 60 and len(set(claimed)) == 60  # every job exactly once
