"""Shared audit and execution lifecycle for supported marketplace operations.

Operations are workspace-scoped, idempotently *queued*, and explicitly
distinguish a successful transport from remote publication verification.
Uncertain write outcomes are never silently replayed. Existing marketplace
adapters remain responsible for the actual protocol implementation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import db, models
from app.diagnostics import redact_text
from app.product_models import BackgroundJob, MarketplaceOperation

ACTIVE = frozenset({"queued", "running"})
TERMINAL = frozenset({"succeeded", "needs_verification", "failed", "attention", "cancelled"})
SUPPORTED_TYPES = frozenset({"sync", "publish", "update", "photos", "close", "verify"})
# Replaying a remote write after a timeout can duplicate a listing or FTP
# photograph. Only explicit read-only importer handlers are generically safe
# to requeue; BIBLIO uploads and all photograph writes use targeted recovery.
SAFE_IMPORT_CHANNELS = frozenset({
    "ebay", "etsy", "woocommerce", "shopify", "bigcommerce",
    "squarespace", "wix", "depop",
})

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _identity(channel: str, operation_type: str, target_key: str) -> str:
    return f"{channel}:{operation_type}:{target_key}"


def _owned_targets(
    session: Session,
    workspace_id: uuid.UUID,
    inventory_item_id: uuid.UUID | None,
    channel_listing_id: uuid.UUID | None,
) -> None:
    if inventory_item_id is not None:
        item = session.get(models.InventoryItem, inventory_item_id)
        if item is None or item.workspace_id != workspace_id:
            raise ValueError("Inventory item not in workspace")
    if channel_listing_id is not None:
        listing = session.get(models.ChannelListing, channel_listing_id)
        if listing is None or listing.workspace_id != workspace_id:
            raise ValueError("Marketplace listing not in workspace")


def _queue_job(session: Session, op: MarketplaceOperation, delay_seconds: int = 0) -> uuid.UUID:
    if not op.job_type:
        raise ValueError("Operation has no background handler")
    job = BackgroundJob(
        workspace_id=op.workspace_id,
        job_type=op.job_type,
        payload={**dict(op.job_payload or {}), "operation_id": str(op.id)},
        status="queued",
        available_at=utcnow() + timedelta(seconds=max(0, int(delay_seconds))),
    )
    session.add(job)
    session.flush()
    op.job_id = job.id
    return job.id


def queue_operation(
    session: Session,
    workspace_id: uuid.UUID,
    channel: str,
    operation_type: str,
    target_key: str,
    *,
    job_type: str,
    payload: dict[str, Any] | None = None,
    inventory_item_id: uuid.UUID | None = None,
    channel_listing_id: uuid.UUID | None = None,
    delay_seconds: int = 0,
) -> tuple[MarketplaceOperation, bool]:
    """Atomically create an operation AND its job; coalesce identical active work.

    An active operation on the same channel/type/target with different
    parameters is rejected, rather than silently dropping a full resync or
    merging unlike photo attempts. No credentials are stored in the payload.
    """
    if operation_type not in SUPPORTED_TYPES:
        raise ValueError("Unsupported marketplace operation")
    if not channel or not target_key:
        raise ValueError("Channel and target are required")
    if any(key.lower() in {"password", "oauth_token", "access_token", "secret", "api_key"}
           for key in (payload or {})):
        raise ValueError("Operation payload must not contain credentials")
    _owned_targets(session, workspace_id, inventory_item_id, channel_listing_id)
    key = _identity(channel, operation_type, target_key)
    existing = session.execute(
        select(MarketplaceOperation).where(
            MarketplaceOperation.workspace_id == workspace_id,
            MarketplaceOperation.active_key == key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        requested = dict(payload or {})
        stored = dict(existing.job_payload or {})
        if (
            channel == "biblio"
            and operation_type == "photos"
            and existing.status == "queued"
            and stored.get("automatic_photo_retry")
            and (requested.get("force_photos") or requested.get("failed_photos_only"))
            and existing.job_type == job_type
        ):
            # A user retry must not be blocked for ~26 hours by the
            # scheduled low-priority photo pickup. Promote that SAME job.
            job = session.get(BackgroundJob, existing.job_id)
            if job is not None and job.status == "queued":
                existing.job_payload = requested
                job.payload = {**requested, "operation_id": str(existing.id)}
                job.available_at = utcnow()
                return existing, False
        if existing.job_type != job_type or stored != requested:
            raise ValueError("Another operation on this listing is still active")
        return existing, False

    op = MarketplaceOperation(
        workspace_id=workspace_id,
        channel=channel,
        operation_type=operation_type,
        target_key=target_key,
        active_key=key,
        inventory_item_id=inventory_item_id,
        channel_listing_id=channel_listing_id,
        job_type=job_type,
        job_payload=dict(payload or {}),
        status="queued",
        verification="not_checked",
    )
    session.add(op)
    # Database-enforced unique key prevents concurrent duplicate creates.
    # IntegrityError is intentionally propagated to rollback the losing
    # transaction, rather than creating duplicate background jobs.
    session.flush()
    _queue_job(session, op, delay_seconds=delay_seconds)
    return op, True


def begin_operation(operation_id: uuid.UUID, job_id: uuid.UUID | None = None) -> bool:
    with db.session_scope() as session:
        op = session.get(MarketplaceOperation, operation_id)
        if op is None:
            raise RuntimeError("Marketplace operation not found")
        if job_id is not None and op.job_id != job_id:
            raise RuntimeError("Marketplace operation/job mismatch")
        if op.status in TERMINAL:
            return False
        if op.status != "queued":
            # An incomplete old process may have had an external effect.
            # Do not repeat an already-running write.
            op.status = "attention"
            op.last_error = "Previously started operation requires manual reconciliation"
            op.active_key = None
            op.completed_at = utcnow()
            return False
        op.status = "running"
        op.started_at = utcnow()
        op.attempts += 1
        return True


def _public_result(result: dict[str, Any] | None) -> dict[str, Any]:
    """Persist stable operational evidence but no remote secrets or bulk PII."""
    safe = {}
    allowed = {
        "items", "active", "orders", "linked", "inventory_uploaded",
        "inventory_total", "deletes", "deletes_uploaded", "photos_uploaded",
        "photos_total", "photos_skipped", "photo_count", "photo_retry_scheduled",
        "remote", "external_id", "listing_id", "url", "already_complete",
        "message", "skipped", "remote_verified", "quantity", "status",
        "price_cents", "currency",
    }
    for key, value in (result or {}).items():
        if key in allowed and isinstance(value, (str, int, float, bool, type(None))):
            safe[key] = str(value)[:500] if isinstance(value, str) else value
    return safe


def complete_operation(operation_id: uuid.UUID, result: dict[str, Any] | None = None) -> None:
    with db.session_scope() as session:
        op = session.get(MarketplaceOperation, operation_id)
        if op is None or op.status in TERMINAL:
            return
        safe = _public_result(result)
        if op.operation_type in {"update", "close"} and (result or {}).get("remote_verified") is True:
            op.status = "succeeded"
            op.verification = "remote_verified"
        elif op.operation_type == "close" and (result or {}).get("skipped"):
            # A late stock restoration can cancel the work after the worker
            # began. Do not label an intentionally skipped close "sent".
            op.status = "cancelled"
            op.verification = "not_checked"
        elif op.channel == "biblio" and op.operation_type in {"sync", "publish", "update", "photos"}:
            pending_photos = int((result or {}).get("photos_total") or 0) - int(
                (result or {}).get("photos_uploaded") or 0
            )
            if (result or {}).get("photo_errors") or pending_photos > 0:
                op.status = "attention"
                op.verification = "manual_required"
                op.last_error = "BIBLIO reported missing or failed photo transfers; inspect per-file FTP results"
                safe["photos_missing_or_failed"] = max(0, pending_photos)
            elif op.operation_type == "sync" and not any(
                int((result or {}).get(key) or 0) for key in (
                    "active", "deletes", "photos_uploaded"
                )
            ):
                op.status = "succeeded"
                op.verification = "no_remote_changes"
            else:
                op.status = "needs_verification"
                op.verification = "manual_required"
        elif op.operation_type in {"publish", "update", "photos", "close"}:
            # FTP/API acceptance alone cannot prove remote publication.
            op.status = "needs_verification"
            op.verification = "manual_required"
        else:
            op.status = "succeeded"
            op.verification = "snapshot_imported" if op.operation_type == "sync" else "not_checked"
        op.result = safe
        op.active_key = None
        op.completed_at = utcnow()
        if op.status != "attention":
            op.last_error = None


def fail_operation(operation_id: uuid.UUID, error: str, *, will_retry: bool = False) -> None:
    with db.session_scope() as session:
        op = session.get(MarketplaceOperation, operation_id)
        if op is None or op.status in TERMINAL:
            return
        op.last_error = redact_text(str(error))[:2000]
        if will_retry:
            op.status = "queued"
            return
        # A failed write may already have reached the remote marketplace.
        op.status = "attention" if op.operation_type in {"publish", "update", "close"} else "failed"
        op.active_key = None
        op.completed_at = utcnow()


def start_inline(
    workspace_id: uuid.UUID,
    channel: str,
    operation_type: str,
    target_key: str,
    *,
    inventory_item_id: uuid.UUID | None = None,
    channel_listing_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Reserve one synchronous remote write before invoking the adapter.

    After an uncertain failure, this target requires investigation instead
    of another create request; this protects against duplicate remote posts.
    """
    if operation_type not in {"publish", "update", "close"}:
        raise ValueError("Unsupported inline remote operation")
    with db.session_scope() as session:
        _owned_targets(session, workspace_id, inventory_item_id, channel_listing_id)
        key = _identity(channel, operation_type, target_key)
        existing = session.execute(
            select(MarketplaceOperation).where(
                MarketplaceOperation.workspace_id == workspace_id,
                MarketplaceOperation.channel == channel,
                MarketplaceOperation.operation_type == operation_type,
                MarketplaceOperation.target_key == target_key,
            ).order_by(MarketplaceOperation.created_at.desc())
        ).scalars().first()
        if existing is not None and existing.status in (
            "running", "queued", "attention", "needs_verification"
        ):
            raise ValueError(
                f"A previous {operation_type} may already have reached the marketplace. "
                "Check the remote listing before another write."
            )
        op = MarketplaceOperation(
            workspace_id=workspace_id,
            channel=channel,
            operation_type=operation_type,
            target_key=target_key,
            active_key=key,
            inventory_item_id=inventory_item_id,
            channel_listing_id=channel_listing_id,
            job_type=None,
            job_payload={},
            status="running",
            verification="not_checked",
            attempts=1,
            started_at=utcnow(),
        )
        session.add(op)
        session.flush()
        return op.id


def can_retry_operation(op: MarketplaceOperation) -> bool:
    """Single source of truth for API affordances and actual retry permission."""
    return (
        op.operation_type == "sync"
        and op.channel in SAFE_IMPORT_CHANNELS
        and op.target_key == "all"
        and op.job_type == f"{op.channel}_sync"
        and not dict(op.job_payload or {})
        and op.status in {"failed", "attention"}
    )


def recovery_instruction(op: MarketplaceOperation) -> dict[str, str] | None:
    """One concrete, non-destructive next action for an unresolved result."""
    if op.status not in {"failed", "attention", "needs_verification"}:
        return None
    # Prefer a fresh exact-record read over vague advice to check manually.
    # An unavailable original-write snapshot never enables remote inspection.
    from app.remote_reconciliation import can_inspect
    if can_inspect(op):
        return {
            "kind": "inspect_remote",
            "label": "Check live marketplace result",
            "detail": "Read the linked marketplace record and compare it with what was "
                      "originally requested. Nothing will be resent or changed.",
        }
    if can_retry_operation(op):
        return {
            "kind": "retry_import", "label": "Retry data import",
            "detail": "This reads marketplace data again. It does not publish, edit or close listings.",
        }
    if op.channel == "biblio" and op.operation_type == "photos":
        return {
            "kind": "biblio_photos", "label": "Inspect book photos",
            "detail": "Check individual transfer results and the BIBLIO listing. "
                      "Use the book's photo-repair controls rather than repeating an uncertain upload.",
        }
    if op.channel == "biblio":
        return {
            "kind": "biblio_compare", "label": "Compare BIBLIO inventory",
            "detail": "Check BIBLIO's own inventory export and transfer results before sending anything again.",
        }
    if op.inventory_item_id:
        return {
            "kind": "inspect_item", "label": "Review linked item",
            "detail": "Check the marketplace listing and recorded result before making another change.",
        }
    return {
        "kind": "manual_review", "label": "Check marketplace result",
        "detail": "The outcome is not independently verified. Review it on the marketplace first; "
                  "another automatic write is blocked.",
    }


def retry_operation(session: Session, workspace_id: uuid.UUID, operation_id: uuid.UUID) -> MarketplaceOperation:
    op = session.get(MarketplaceOperation, operation_id)
    if op is None or op.workspace_id != workspace_id:
        raise ValueError("Marketplace operation not found")
    if not can_retry_operation(op):
        raise ValueError(
            "Automatic retry is only available for failed read-only imports. "
            "This operation cannot be retried automatically; inspect BIBLIO "
            "transfers or the remote listing before another upload."
        )
    if not op.job_type:
        raise ValueError("Operation has no retry handler")
    key = _identity(op.channel, op.operation_type, op.target_key)
    conflict = session.execute(
        select(MarketplaceOperation.id).where(
            MarketplaceOperation.workspace_id == workspace_id,
            MarketplaceOperation.active_key == key,
        )
    ).scalar_one_or_none()
    if conflict is not None:
        raise ValueError("A new operation for this listing is already pending")
    op.active_key = key
    op.status = "queued"
    op.completed_at = None
    op.last_error = None
    _queue_job(session, op)
    return op


def serialize(op: MarketplaceOperation) -> dict[str, Any]:
    from app.remote_reconciliation import can_inspect

    def iso(value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    return {
        "id": str(op.id),
        "channel": op.channel,
        "type": op.operation_type,
        "target": op.target_key,
        "inventory_item_id": str(op.inventory_item_id) if op.inventory_item_id else None,
        "listing_id": str(op.channel_listing_id) if op.channel_listing_id else None,
        "job_id": str(op.job_id) if op.job_id else None,
        "status": op.status,
        "verification": op.verification,
        "attempts": op.attempts,
        "error": op.last_error,
        "result": dict(op.result or {}),
        "created_at": iso(op.created_at),
        "started_at": iso(op.started_at),
        "completed_at": iso(op.completed_at),
        "can_retry": can_retry_operation(op),
        "can_inspect_remote": can_inspect(op),
        "next_step": recovery_instruction(op),
    }
