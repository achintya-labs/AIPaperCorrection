import logging
import os
import signal
import socket
import threading
import time
from collections.abc import Callable
from datetime import timedelta

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.grader import GraderProvider, get_provider
from app.jobs import queue
from app.storage import Storage, get_storage

log = logging.getLogger("worker")


def worker_id() -> str:
    """Identifies this worker for restart recovery. Containers have unique, restart-stable hostnames."""
    return os.environ.get("WORKER_ID") or socket.gethostname()


def run_once(
    provider: GraderProvider,
    storage: Storage,
    settings: Settings,
    session_factory=SessionLocal,
    wid: str | None = None,
) -> bool:
    """Claim and process a single job (grading or PDF auto-split). Returns False if both queues were empty."""
    wid_val = wid or worker_id()
    with session_factory() as db:
        sub = queue.claim_next(db, wid_val)
        if sub is not None:
            log.info("grading submission %s", sub.id)
            queue.process(db, sub, provider, storage, settings)
            log.info("submission %s -> %s", sub.id, sub.status.value)
            return True
        split_job = queue.claim_next_split(db, wid_val)
        if split_job is not None:
            log.info("splitting PDF job %s (exam %s)", split_job.id, split_job.exam_id)
            queue.process_split(db, split_job, provider, storage, settings)
            log.info("split job %s -> %s", split_job.id, split_job.status.value)
            return True
        return False


def _loop(stop: threading.Event, step: Callable[[], bool], poll: float) -> None:
    while not stop.is_set():
        try:
            busy = step()
        except Exception:
            log.exception("worker step failed")
            busy = False
        if not busy:
            stop.wait(poll)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(threadName)s %(message)s")
    settings = get_settings()
    provider, storage = get_provider(settings), get_storage()
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    wid = worker_id()
    # After a host restart the database may still be starting; retry for about a minute.
    for attempt in range(30):
        try:
            with SessionLocal() as db:
                n = queue.recover_own(db, wid)
            if n:
                log.warning("requeued %d job(s) orphaned by a previous run of worker %s", n, wid)
            break
        except Exception as e:
            if attempt == 29 or stop.is_set():
                # The periodic stale-job sweep will still catch them, just later.
                log.exception("could not recover own jobs at start-up")
                break
            log.info("database not ready (%s); retrying", type(e).__name__)
            stop.wait(2)

    # A job can legitimately run for timeout * attempts; anything older was orphaned.
    stale = timedelta(seconds=settings.grader_timeout_seconds * settings.grader_max_attempts + 120)

    def recover() -> bool:
        with SessionLocal() as db:
            n = queue.recover_stale(db, stale)
        if n:
            log.warning("requeued %d stale job(s)", n)
        return False

    threads = [
        threading.Thread(
            target=_loop,
            args=(stop, lambda: run_once(provider, storage, settings, wid=wid), settings.job_poll_seconds),
            name=f"grader-{i}",
        )
        for i in range(settings.worker_concurrency)
    ]
    threads.append(threading.Thread(target=_loop, args=(stop, recover, 60), name="recovery"))
    log.info("worker up: provider=%s concurrency=%d", provider.name, settings.worker_concurrency)
    for t in threads:
        t.start()
    while not stop.is_set():
        time.sleep(0.5)
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
