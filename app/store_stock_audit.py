"""Audited, read-only remote stock checks for linked physical inventory.

Every network operation is a GET/query through an existing connector reader.
Remote stock is never written, and master physical quantities remain unchanged.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app import db, models
from app.constants import Channel
from app.product_models import BackgroundJob
from app.stock_relations import is_physical

STORES = (Channel.WOOCOMMERCE, Channel.SHOPIFY, Channel.WIX)
JOB_TYPE = "store_stock_audit"
MAX_TARGETS = 100


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def candidates(session: Any, workspace_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = session.execute(
        select(models.ChannelListing, models.InventoryItem)
        .join(models.InventoryItem, models.ChannelListing.inventory_item_id == models.InventoryItem.id)
        .where(
            models.ChannelListing.workspace_id == workspace_id,
            models.InventoryItem.workspace_id == workspace_id,
            models.ChannelListing.channel.in_(STORES),
        )
        .order_by(models.ChannelListing.channel, models.InventoryItem.title,
                  models.ChannelListing.external_id)
    ).all()
    counts: dict[tuple[uuid.UUID, str], int] = {}
    for listing, item in rows:
        key = (item.id, listing.channel)
        counts[key] = counts.get(key, 0) + 1
    result = []
    for listing, item in rows:
        if not is_physical(item):
            continue
        duplicated = counts[(item.id, listing.channel)] != 1
        result.append({
            "listing_id": str(listing.id),
            "item_id": str(item.id),
            "title": item.title,
            "sku": item.sku,
            "channel": listing.channel,
            "external_id": listing.external_id,
            "expected_sku": listing.external_sku,
            "local_quantity": int(item.quantity or 0),
            "skip_reason": (
                "Multiple linked listings in this store; reconcile the links first"
                if duplicated else None
            ),
        })
    return result


def _read(workspace_id: uuid.UUID, target: dict[str, Any]) -> dict[str, Any]:
    channel = target["channel"]
    if channel == Channel.WOOCOMMERCE:
        from app.connectors.hosted import read_woocommerce_workspace_stock
        reader = read_woocommerce_workspace_stock
    elif channel == Channel.SHOPIFY:
        from app.connectors.hosted import read_shopify_workspace_stock
        reader = read_shopify_workspace_stock
    elif channel == Channel.WIX:
        from app.connectors.wix_stock import read_wix_workspace_stock
        reader = read_wix_workspace_stock
    else:
        raise ValueError("Store is not supported by the stock audit")
    return reader(
        workspace_id,
        external_id=target["external_id"],
        expected_sku=target["expected_sku"],
    )


def _result(workspace_id: uuid.UUID, job_id: str, target: dict[str, Any]) -> None:
    when = utcnow().isoformat()
    state = "skipped" if target["skip_reason"] else "error"
    remote_quantity: int | None = None
    message = target["skip_reason"]
    remote: dict[str, Any] | None = None
    if not message:
        try:
            remote = _read(workspace_id, target)
            remote_quantity = remote.get("quantity")
            if type(remote_quantity) is not int or remote_quantity < 0:
                raise ValueError("Store did not return a usable stock quantity")
            if remote.get("manage_stock") is not True:
                raise ValueError("Quantity tracking is disabled in the store")
            expected_status = "instock" if target["local_quantity"] else "outofstock"
            state = (
                "matched"
                if remote_quantity == target["local_quantity"]
                and remote.get("stock_status") == expected_status
                else "mismatch"
            )
        except ValueError as exc:
            message = str(exc)[:250] or "Remote identity or stock could not be verified"
        except Exception:
            # Credentials, remote response bodies, and HTTP exceptions can hold
            # secrets. Never store them in a user-visible audit report.
            message = "Store could not be reached or its stock could not be read"
    with db.session_scope() as session:
        listing = session.get(models.ChannelListing, uuid.UUID(target["listing_id"]))
        item = session.get(models.InventoryItem, uuid.UUID(target["item_id"]))
        if (listing is None or item is None
                or listing.workspace_id != workspace_id or item.workspace_id != workspace_id):
            return
        if (
            listing.inventory_item_id != item.id
            or listing.channel != target["channel"]
            or listing.external_id != target["external_id"]
            or listing.external_sku != target["expected_sku"]
            or not is_physical(item)
            or int(item.quantity or 0) != target["local_quantity"]
        ):
            state = "changed"
            message = "Item or linked listing changed during the check; run it again"
            remote_quantity = None
        extra = dict(listing.extra or {})
        extra["store_stock_audit"] = {
            "run_id": job_id, "state": state,
            "external_id": target["external_id"],
            "external_sku": target["expected_sku"],
            "local_quantity": target["local_quantity"],
            "remote_quantity": remote_quantity, "checked_at": when,
            "message": message,
        }
        if state in {"matched", "mismatch"} and remote is not None:
            extra["stock_last_checked_at"] = when
            extra["stock_last_remote_quantity"] = remote_quantity
            extra["stock_last_master_quantity"] = target["local_quantity"]
            extra["stock_remote_readback_verified"] = state == "matched"
            if state == "matched":
                extra["stock_synced_at"] = when
                extra["stock_synced_quantity"] = remote_quantity
        elif state in {"error", "changed"}:
            # A failed observation cannot be presented as a fresh verification.
            extra["stock_remote_readback_verified"] = False
        listing.extra = extra


def run(workspace_id: uuid.UUID, job_id: str) -> dict[str, Any]:
    """Called by the durable worker. Per-listing failures do not abort a scan."""
    with db.session_scope() as session:
        targets = candidates(session, workspace_id)
    if len(targets) > MAX_TARGETS:
        raise ValueError("Too many linked listings for one audit; limit is 100")
    for target in targets:
        _result(workspace_id, job_id, target)
    return {"checked": len(targets)}


def latest(workspace_id: uuid.UUID) -> dict[str, Any]:
    with db.session_scope() as session:
        job = session.execute(
            select(BackgroundJob).where(
                BackgroundJob.workspace_id == workspace_id,
                BackgroundJob.job_type == JOB_TYPE,
            ).order_by(BackgroundJob.created_at.desc(), BackgroundJob.id.desc())
        ).scalars().first()
        targets = candidates(session, workspace_id)
        if not job:
            return {
                "job": None, "eligible": len(targets),
                "results": [], "counts": {},
                "limit": MAX_TARGETS,
            }
        job_id = str(job.id)
        rows = session.execute(
            select(models.ChannelListing, models.InventoryItem)
            .join(models.InventoryItem, models.ChannelListing.inventory_item_id == models.InventoryItem.id)
            .where(
                models.ChannelListing.workspace_id == workspace_id,
                models.InventoryItem.workspace_id == workspace_id,
                models.ChannelListing.channel.in_(STORES),
            )
        ).all()
        results = []
        for listing, item in rows:
            info = dict((listing.extra or {}).get("store_stock_audit") or {})
            if info.get("run_id") != job_id:
                continue
            changed_since_check = (
                int(item.quantity or 0) != info.get("local_quantity")
                or listing.external_id != info.get("external_id")
                or listing.external_sku != info.get("external_sku")
                or not is_physical(item)
            )
            if changed_since_check:
                info["state"] = "changed"
                info["message"] = "Stock or listing link changed after the check; run it again"
                info["remote_quantity"] = None
            results.append({
                "item_id": str(item.id), "title": item.title,
                "channel": listing.channel, "sku": item.sku,
                "local_quantity": info.get("local_quantity"),
                "remote_quantity": info.get("remote_quantity"),
                "state": info.get("state"), "message": info.get("message"),
                "checked_at": info.get("checked_at"),
            })
        priority = {"mismatch": 0, "error": 1, "changed": 2, "skipped": 3, "matched": 4}
        results.sort(key=lambda row: (priority.get(row["state"], 5),
                                      row["channel"], row["title"]))
        counts: dict[str, int] = {}
        for row in results:
            counts[row["state"]] = counts.get(row["state"], 0) + 1
        return {
            "job": {
                "id": job_id, "status": job.status,
                "started_at": job.locked_at.isoformat() if job.locked_at else None,
                "created_at": job.created_at.isoformat(),
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            },
            "eligible": len(targets), "limit": MAX_TARGETS,
            "results": results, "counts": counts,
        }
