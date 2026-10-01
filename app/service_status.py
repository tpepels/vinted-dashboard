"""Cross-process service liveness stored in the application database."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from app import db
from app.product_models import ServiceHeartbeat


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def touch(service: str, *, detail: dict[str, Any] | None = None) -> None:
    name = str(service or "").strip()[:50]
    if not name:
        raise ValueError("Service name is required")
    instance = (
        os.getenv("RENDER_INSTANCE_ID")
        or os.getenv("HOSTNAME")
        or ""
    ).strip()[:200] or None
    with db.session_scope() as session:
        row = session.get(ServiceHeartbeat, name)
        if row is None:
            row = ServiceHeartbeat(service=name)
            session.add(row)
        row.instance_id = instance
        row.last_seen_at = utcnow()
        row.detail = dict(detail or {})


def status(service: str, *, max_age_seconds: int = 90) -> dict[str, Any]:
    with db.session_scope() as session:
        row = session.get(ServiceHeartbeat, service)
        if row is None:
            return {
                "service": service,
                "healthy": False,
                "last_seen_at": None,
                "age_seconds": None,
            }
        last_seen = row.last_seen_at
        age = max(0.0, (utcnow() - last_seen).total_seconds())
        return {
            "service": service,
            "healthy": age <= max(1, int(max_age_seconds)),
            "last_seen_at": last_seen.isoformat(),
            "age_seconds": round(age, 1),
        }
