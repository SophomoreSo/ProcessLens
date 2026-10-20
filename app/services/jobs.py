"""Single-process background job runner for imports.

Limits (documented in README): jobs run in one worker thread inside the web
process, are not distributed, and do not survive a restart. Each import writes
its data in a single database transaction, so an interrupted job leaves no
partial data; on start-up unfinished jobs are marked ``interrupted``.

Live progress is kept in memory because the import transaction holds the
SQLite write lock; the job row is updated at start and finish.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from sqlalchemy import select

from app.database import session_scope
from app.models import ImportJob
from app.models.dataset import utcnow

log = logging.getLogger("processlens.jobs")

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="processlens-job")
_lock = threading.Lock()
_live: dict[str, dict] = {}
_futures: dict[str, Future] = {}


def new_job_id() -> str:
    return uuid.uuid4().hex


def set_progress(job_id: str, progress: float, stage: str) -> None:
    with _lock:
        _live[job_id] = {"progress": round(min(max(progress, 0.0), 1.0), 3), "stage": stage}


def live_progress(job_id: str) -> dict | None:
    with _lock:
        return _live.get(job_id)


def clear_progress(job_id: str) -> None:
    with _lock:
        _live.pop(job_id, None)


def submit(job_id: str, fn: Callable, *args) -> Future:
    fut = _executor.submit(fn, job_id, *args)
    with _lock:
        _futures[job_id] = fut
    return fut


def wait_for(job_id: str, timeout: float = 120) -> None:
    """Block until a job finishes (used by tests and the CLI demo loader)."""
    with _lock:
        fut = _futures.get(job_id)
    if fut is not None:
        fut.result(timeout=timeout)


def recover_interrupted_jobs() -> int:
    """Mark jobs left queued/running by a previous process as interrupted."""
    with session_scope() as db:
        jobs = db.scalars(select(ImportJob).where(ImportJob.status.in_(["queued", "running"]))).all()
        for job in jobs:
            job.status = "interrupted"
            job.stage = "Interrupted by restart"
            job.error = ("The server stopped while this import was running. No data from it was committed. "
                         "Start the import again from the import wizard.")
            job.updated_at = utcnow()
        if jobs:
            log.warning("Marked %d unfinished job(s) as interrupted", len(jobs))
        return len(jobs)
