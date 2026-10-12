"""Tiny Postgres/SQLite-backed job queue used by the optional worker.

The queue intentionally supports only coarse background tasks.  It avoids
running connector work inside arbitrary FastAPI request threads while keeping
self-hosted deployment simple.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app import db
from app.product_models import BackgroundJob

BIBLIO_PAUSE_MARKER = "_biblio_upload_pause_deferred"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(
    job_type: str,
    payload: dict[str, Any],
    workspace_id: uuid.UUID | None = None,
    *,
    delay_seconds: int = 0,
) -> uuid.UUID:
    with db.session_scope() as session:
        job = BackgroundJob(
            workspace_id=workspace_id,
            job_type=job_type,
            payload=payload,
            status="queued",
            available_at=utcnow() + timedelta(seconds=max(0, int(delay_seconds or 0))),
        )
        session.add(job)
        session.flush()
        return job.id


def enqueue_unique(
    job_type: str,
    payload: dict[str, Any],
    workspace_id: uuid.UUID | None = None,
    *,
    delay_seconds: int = 0,
) -> uuid.UUID:
    """Queue a job unless the same workspace/type/payload is already pending."""
    normalized_payload = dict(payload or {})
    with db.session_scope() as session:
        existing = session.execute(
            select(BackgroundJob).where(
                BackgroundJob.workspace_id == workspace_id,
                BackgroundJob.job_type == job_type,
                BackgroundJob.status.in_(["queued", "running"]),
            )
        ).scalars().all()
        for row in existing:
            # A paused job still represents the same operation; its internal
            # pause marker must not cause duplicate auto-sync jobs.
            candidate = {key: value for key, value in dict(row.payload or {}).items()
                         if key != BIBLIO_PAUSE_MARKER}
            if candidate == normalized_payload:
                return row.id
        job = BackgroundJob(
            workspace_id=workspace_id,
            job_type=job_type,
            payload=normalized_payload,
            status="queued",
            available_at=utcnow() + timedelta(seconds=max(0, int(delay_seconds or 0))),
        )
        session.add(job)
        session.flush()
        return job.id


def claim_one() -> dict[str, Any] | None:
    with db.session_scope() as session:
        job = session.execute(
            select(BackgroundJob)
            .where(
                BackgroundJob.status == "queued",
                BackgroundJob.available_at <= utcnow(),
            )
            .order_by(BackgroundJob.created_at)
            .limit(1)
        ).scalar_one_or_none()
        if job is None:
            return None
        job.status = "running"
        job.locked_at = utcnow()
        job.attempts += 1
        session.flush()
        return {
            "id": str(job.id),
            "workspace_id": str(job.workspace_id) if job.workspace_id else None,
            "job_type": job.job_type,
            "payload": dict(job.payload or {}),
        }



def defer_without_attempt(job_id: str, until: datetime) -> None:
    """Return a claimed job to the queue when uploads are temporarily paused.

    A pause is not a connection failure: it must not consume a retry attempt or
    mark any marketplace operation as started, failed, or completed.
    """
    with db.session_scope() as session:
        job = session.get(BackgroundJob, uuid.UUID(job_id))
        if job is None or job.status != "running":
            return
        job.status = "queued"
        job.available_at = until
        job.locked_at = None
        job.attempts = max(0, int(job.attempts or 0) - 1)
        job.payload = {**dict(job.payload or {}), BIBLIO_PAUSE_MARKER: True}


def release_paused_biblio_jobs(workspace_id: uuid.UUID) -> int:
    """Make only pause-deferred BIBLIO jobs available when resuming early.

    Preserve photo retry delays for jobs not postponed by the pause.
    """
    released = 0
    with db.session_scope() as session:
        pending = session.execute(
            select(BackgroundJob).where(
                BackgroundJob.workspace_id == workspace_id,
                BackgroundJob.job_type == "biblio_sync",
                BackgroundJob.status == "queued",
            )
        ).scalars().all()
        now = utcnow()
        for row in pending:
            payload = dict(row.payload or {})
            if not payload.pop(BIBLIO_PAUSE_MARKER, False):
                continue
            row.payload = payload
            row.available_at = now
            released += 1
    return released


def clear_biblio_pause_marker(job_id: str) -> None:
    """Remove the internal marker once a deferred job is ready to run."""
    with db.session_scope() as session:
        job = session.get(BackgroundJob, uuid.UUID(job_id))
        if job is None:
            return
        payload = dict(job.payload or {})
        if payload.pop(BIBLIO_PAUSE_MARKER, False):
            job.payload = payload


def complete(job_id: str) -> None:
    with db.session_scope() as session:
        job = session.get(BackgroundJob, uuid.UUID(job_id))
        if job is None:
            return
        job.status = "success"
        job.completed_at = utcnow()


def fail(job_id: str, error: str) -> None:
    with db.session_scope() as session:
        job = session.get(BackgroundJob, uuid.UUID(job_id))
        if job is None:
            return
        job.status = "error"
        job.completed_at = utcnow()
        job.last_error = error[:2000]


def retry(
    job_id: str,
    error: str,
    *,
    max_attempts: int = 3,
    base_delay_seconds: int = 30,
) -> dict[str, Any]:
    """Requeue a claimed job with bounded exponential backoff.

    The same job row is reused, so retries cannot multiply queue entries.
    """
    with db.session_scope() as session:
        job = session.get(BackgroundJob, uuid.UUID(job_id))
        if job is None:
            return {"will_retry": False, "attempts": 0}
        job.last_error = str(error)[:2000]
        if int(job.attempts or 0) >= max(1, max_attempts):
            job.status = "error"
            job.completed_at = utcnow()
            return {"will_retry": False, "attempts": int(job.attempts or 0)}
        delay = max(0, int(base_delay_seconds)) * (2 ** max(0, int(job.attempts or 1) - 1))
        job.status = "queued"
        job.available_at = utcnow() + timedelta(seconds=delay)
        job.locked_at = None
        job.completed_at = None
        return {
            "will_retry": True,
            "attempts": int(job.attempts or 0),
            "delay_seconds": delay,
        }
