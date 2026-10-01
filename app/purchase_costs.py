"""Purchase-to-inventory acquisition-cost reconciliation.

Vinted purchase history can help fill missing acquisition costs, but order
amounts are not safe to apply silently: they may include fees/shipping and not
all purchases are resale stock.  This module therefore generates deterministic
suggestions and requires an explicit user apply.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models


def _norm(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _eligible_items(
    session: Session,
    workspace_id: uuid.UUID,
) -> list[models.InventoryItem]:
    return session.execute(
        select(models.InventoryItem).where(
            models.InventoryItem.workspace_id == workspace_id,
            models.InventoryItem.cost_cents.is_(None),
        )
    ).scalars().all()


def _purchase_rows(
    session: Session,
    workspace_id: uuid.UUID,
) -> list[models.Sale]:
    return session.execute(
        select(models.Sale)
        .where(
            models.Sale.workspace_id == workspace_id,
            models.Sale.direction == "buy",
            models.Sale.total_cents.is_not(None),
        )
        .order_by(models.Sale.occurred_at.desc(), models.Sale.last_seen_at.desc())
    ).scalars().all()


def purchase_cost_suggestions(
    session: Session,
    workspace_id: uuid.UUID,
) -> dict[str, Any]:
    """Return safe one-to-one purchase→stock cost suggestions.

    Existing explicit purchase linkage wins. Otherwise an exact normalized
    title is suggested only when there is exactly one uncosted purchase and one
    uncosted inventory item with that title. Duplicate purchase titles or
    duplicate stock copies stay ambiguous.
    """
    items = _eligible_items(session, workspace_id)
    purchases = _purchase_rows(session, workspace_id)

    items_by_id = {item.id: item for item in items}
    already_linked_item_ids = {
        row.inventory_item_id
        for row in purchases
        if row.inventory_item_id is not None
    }

    suggestions: list[dict[str, Any]] = []
    handled_purchase_ids: set[uuid.UUID] = set()

    # Explicit purchase→item links are deterministic and can still be used to
    # fill a missing cost.
    for purchase in purchases:
        if purchase.inventory_item_id is None:
            continue
        item = items_by_id.get(purchase.inventory_item_id)
        if item is None:
            continue
        suggestions.append(
            _serialize_suggestion(
                purchase,
                item,
                match_reason="Already linked purchase",
                confidence="high",
            )
        )
        handled_purchase_ids.add(purchase.id)

    remaining_purchases = [
        row
        for row in purchases
        if row.id not in handled_purchase_ids and row.inventory_item_id is None
    ]
    remaining_items = [
        item
        for item in items
        if item.id not in already_linked_item_ids
    ]

    purchases_by_title: dict[str, list[models.Sale]] = {}
    for purchase in remaining_purchases:
        key = _norm(purchase.title)
        if key:
            purchases_by_title.setdefault(key, []).append(purchase)

    items_by_title: dict[str, list[models.InventoryItem]] = {}
    for item in remaining_items:
        key = _norm(item.title)
        if key:
            items_by_title.setdefault(key, []).append(item)

    ambiguous = 0
    unmatched = 0
    for key, purchase_group in purchases_by_title.items():
        item_group = items_by_title.get(key, [])
        if len(purchase_group) == 1 and len(item_group) == 1:
            suggestions.append(
                _serialize_suggestion(
                    purchase_group[0],
                    item_group[0],
                    match_reason="Unique exact title",
                    confidence="high",
                )
            )
        elif item_group:
            ambiguous += len(purchase_group)
        else:
            unmatched += len(purchase_group)

    # Purchases with no usable title are unmatched history.
    unmatched += sum(1 for row in remaining_purchases if not _norm(row.title))

    suggestions.sort(
        key=lambda row: (
            row["purchase"]["occurred_at"] or "",
            row["purchase"]["title"].casefold(),
        ),
        reverse=True,
    )
    return {
        "suggestions": suggestions,
        "count": len(suggestions),
        "ambiguous_count": ambiguous,
        "unmatched_count": unmatched,
    }


def _serialize_suggestion(
    purchase: models.Sale,
    item: models.InventoryItem,
    *,
    match_reason: str,
    confidence: str,
) -> dict[str, Any]:
    return {
        "id": f"{purchase.id}:{item.id}",
        "confidence": confidence,
        "match_reason": match_reason,
        "purchase": {
            "id": str(purchase.id),
            "channel": purchase.channel,
            "external_order_id": purchase.external_order_id,
            "title": purchase.title or "Untitled purchase",
            "total_cents": purchase.total_cents,
            "currency": purchase.currency or item.currency or "EUR",
            "occurred_at": purchase.occurred_at.isoformat() if purchase.occurred_at else None,
            "counterparty": purchase.counterparty,
        },
        "item": {
            "id": str(item.id),
            "sku": item.sku,
            "title": item.title,
            "status": item.status,
            "currency": item.currency,
        },
    }


def apply_purchase_cost(
    session: Session,
    workspace_id: uuid.UUID,
    *,
    purchase_id: uuid.UUID,
    inventory_item_id: uuid.UUID,
    cost_cents: int,
) -> dict[str, Any]:
    if cost_cents < 0:
        raise ValueError("Cost cannot be negative")

    purchase = session.get(models.Sale, purchase_id)
    item = session.get(models.InventoryItem, inventory_item_id)
    if (
        purchase is None
        or item is None
        or purchase.workspace_id != workspace_id
        or item.workspace_id != workspace_id
    ):
        raise ValueError("Purchase or inventory item not found")
    if purchase.direction != "buy":
        raise ValueError("Only purchase orders can set acquisition cost")
    if item.cost_cents is not None:
        raise ValueError("Inventory item already has an acquisition cost")

    # Re-validate that this exact pair is still among the deterministic
    # suggestions. This prevents stale UI state from applying an ambiguous
    # match after the underlying inventory changed.
    current = purchase_cost_suggestions(session, workspace_id)
    valid_pairs = {
        (
            uuid.UUID(row["purchase"]["id"]),
            uuid.UUID(row["item"]["id"]),
        )
        for row in current["suggestions"]
    }
    if (purchase.id, item.id) not in valid_pairs:
        raise ValueError("Purchase is no longer an unambiguous match for this item")

    item.cost_cents = int(cost_cents)
    if purchase.currency:
        item.currency = purchase.currency
    purchase.inventory_item_id = item.id

    attributes = dict(item.attributes or {})
    attributes.update(
        {
            "cost_source": "vinted_purchase" if purchase.channel == "vinted" else f"{purchase.channel}_purchase",
            "cost_source_sale_id": str(purchase.id),
            "cost_source_order_id": purchase.external_order_id,
            "cost_source_order_amount_cents": purchase.total_cents,
            "cost_source_applied_cents": int(cost_cents),
            "cost_source_adjusted": (
                purchase.total_cents is not None
                and int(cost_cents) != int(purchase.total_cents)
            ),
            "cost_recorded_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    item.attributes = attributes
    session.flush()

    return {
        "purchase_id": str(purchase.id),
        "inventory_item_id": str(item.id),
        "cost_cents": item.cost_cents,
        "currency": item.currency,
        "adjusted": bool(attributes["cost_source_adjusted"]),
    }
