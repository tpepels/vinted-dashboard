"""Tiny Postgres/SQLite-backed job queue used by the optional worker.

The queue intentionally supports only coarse background tasks.  It avoids
running connector work inside arbitrary FastAPI request threads while keeping
self-hosted deployment simple.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app import db
from app.product_models import BackgroundJob


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(job_type: str, payload: dict[str, Any], workspace_id: uuid.UUID | None = None) -> uuid.UUID:
    with db.session_scope() as session:
        job = BackgroundJob(
            workspace_id=workspace_id,
            job_type=job_type,
            payload=payload,
            status="queued",
            available_at=utcnow(),
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
