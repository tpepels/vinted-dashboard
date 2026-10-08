"""Canonical boundary between physical stock and remote marketplace records.

A marketplace SKU, ISBN or title identifies a *candidate* relation, never
proof that two listings advertise the same physical copy. Imported listings
create provisional records until explicitly linked or merged.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.constants import ItemCategory, ItemStatus
from app.stock_policy import sale_counts_as_sold
from app.workspace_bootstrap import normalize_sku

AUTHORITY_KEY = "stock_authority"
PHYSICAL = "physical"
PROVISIONAL = "provisional"


def is_physical(item: models.InventoryItem) -> bool:
    return str((item.attributes or {}).get(AUTHORITY_KEY) or "") == PHYSICAL


def is_provisional(item: models.InventoryItem) -> bool:
    attrs = dict(item.attributes or {})
    return (
        attrs.get(AUTHORITY_KEY) == PROVISIONAL
        or bool(attrs.get("connector_import_placeholder"))
    )


def sale_quantity(sale: models.Sale) -> int:
    try:
        value = int((sale.extra or {}).get("quantity") or 1)
    except (TypeError, ValueError, OverflowError):
        value = 1
    return max(1, value)


def consuming_quantity(session: Session, item: models.InventoryItem) -> int:
    rows = session.execute(
        select(models.Sale).where(
            models.Sale.workspace_id == item.workspace_id,
            models.Sale.inventory_item_id == item.id,
            models.Sale.direction == "sell",
        )
    ).scalars().all()
    return sum(sale_quantity(sale) for sale in rows if sale_counts_as_sold(sale))


def record_physical_quantity(
    session: Session,
    item: models.InventoryItem,
    quantity: int,
    *,
    confirmed: bool = True,
) -> None:
    """Record a human-observed remaining quantity; sales may already exist.

    Use baseline = current remaining stock + currently consuming orders.
    Future replays of those same orders therefore do not decrement again.
    """
    if int(quantity) < 0:
        raise ValueError("Physical quantity cannot be negative")
    attrs = dict(item.attributes or {})
    attrs[AUTHORITY_KEY] = PHYSICAL
    attrs["stock_base_quantity"] = int(quantity) + consuming_quantity(session, item)
    if confirmed:
        attrs["relationship_confirmed_at"] = datetime.now(timezone.utc).isoformat()
    for key in ("connector_import_placeholder", "connector_import_channel", "connector_import_external_id"):
        attrs.pop(key, None)
    item.attributes = attrs
    item.quantity = int(quantity)
    item.status = ItemStatus.ACTIVE if int(quantity) else ItemStatus.ARCHIVED


def confirm_physical_relation(session: Session, item: models.InventoryItem) -> None:
    """Confirm a user-established linkage without resetting existing baseline."""
    if is_physical(item):
        return
    record_physical_quantity(session, item, int(item.quantity or 0))


def _safe_synthetic_sku(channel: str, external_id: str) -> str:
    raw = f"{channel.upper()}-{external_id}"
    if len(raw) <= 96:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return raw[:86] + "-" + digest


def _unused_sku(session: Session, workspace_id: uuid.UUID, candidate: str) -> str:
    """Never collide with verified/master SKUs or a different imported item."""
    root = candidate[:100]
    for attempt in range(100):
        suffix = "" if attempt == 0 else f"-{attempt}"
        value = root[:100 - len(suffix)] + suffix
        exists = session.execute(
            select(models.InventoryItem.id).where(
                models.InventoryItem.workspace_id == workspace_id,
                models.InventoryItem.sku == value,
            )
        ).scalar_one_or_none()
        if exists is None:
            return value
    raise ValueError("Could not allocate a unique imported marketplace SKU")


def create_provisional_item(
    session: Session,
    *,
    workspace_id: uuid.UUID,
    channel: str,
    external_id: str,
    raw_sku: Any,
    title: str,
    quantity: int = 0,
    currency: str | None = None,
    category: str = ItemCategory.GENERAL,
) -> models.InventoryItem:
    """Create a *separate* provisional record, even if the SKU matches stock.

    The original marketplace SKU is kept in ChannelListing.external_sku;
    it may later be proposed by the reconciliation UI. A coincident SKU alone
    never authorizes a silent merge.
    """
    source_sku = normalize_sku(raw_sku)
    if source_sku and len(source_sku) <= 100:
        candidate = source_sku
        exists = session.execute(
            select(models.InventoryItem.id).where(
                models.InventoryItem.workspace_id == workspace_id,
                models.InventoryItem.sku == candidate,
            )
        ).scalar_one_or_none()
        if exists is not None:
            candidate = _safe_synthetic_sku(channel, external_id)
    else:
        candidate = _safe_synthetic_sku(channel, external_id)
    unique_sku = _unused_sku(session, workspace_id, candidate)
    item = models.InventoryItem(
        workspace_id=workspace_id,
        sku=unique_sku,
        title=title,
        category=category,
        quantity=max(0, int(quantity)),
        status=ItemStatus.ACTIVE if int(quantity) > 0 else ItemStatus.ARCHIVED,
        currency=currency,
        attributes={
            AUTHORITY_KEY: PROVISIONAL,
            "connector_import_placeholder": True,
            "connector_import_channel": channel,
            "connector_import_external_id": external_id,
            **({"connector_import_sku": source_sku} if source_sku else {}),
        },
    )
    session.add(item)
    session.flush()
    return item
