"""Cross-channel sold reconciliation.

A confirmed seller-side sale consumes one physical master-stock item. Any
other active marketplace listing linked to that item becomes an audited close
action. Remote closing is only queued for connectors with a supported close
path. Vinted remains manual: the app surfaces the listing and records the
user's acknowledgement, but does not perform an unattended destructive Vinted
action.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db, models
from app.constants import Channel, ItemStatus, ListingStatus
from app.product_models import BackgroundJob, CrossChannelAction
from app.stock_policy import sale_counts_as_sold
from app.stock_relations import is_physical
from app.connectors.workspace_sync import (
    item_has_remaining_stock_on_sale_channel,
    recompute_inventory_item,
)


ACTION_TYPE = "close_listing"
REMOTE_CHANNELS = {Channel.EBAY, Channel.BIBLIO}
OPEN_LISTING_STATUSES = {
    ListingStatus.ACTIVE,
    getattr(ListingStatus, "RESERVED", "reserved"),
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalized_title(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _without_other_consuming_sales(
    session: Session,
    sale: models.Sale,
    items: list[models.InventoryItem],
) -> list[models.InventoryItem]:
    if not items:
        return []
    item_ids = [item.id for item in items]
    linked_sales = session.execute(
        select(models.Sale).where(
            models.Sale.workspace_id == sale.workspace_id,
            models.Sale.direction == "sell",
            models.Sale.inventory_item_id.in_(item_ids),
            models.Sale.id != sale.id,
        )
    ).scalars().all()
    consumed_ids = {
        row.inventory_item_id
        for row in linked_sales
        if row.inventory_item_id is not None and sale_counts_as_sold(row)
    }
    return [item for item in items if item.id not in consumed_ids]


def _sale_title_candidates(
    session: Session,
    sale: models.Sale,
) -> list[models.InventoryItem]:
    normalized = _normalized_title(sale.title)
    if not normalized:
        return []

    listings = session.execute(
        select(models.ChannelListing).where(
            models.ChannelListing.workspace_id == sale.workspace_id,
            models.ChannelListing.channel == sale.channel,
        )
    ).scalars().all()
    listing_item_ids = {
        row.inventory_item_id
        for row in listings
        if row.inventory_item_id is not None
        and _normalized_title(row.title) == normalized
        and (
            sale.occurred_at is None
            or row.first_seen_at is None
            or row.first_seen_at <= sale.occurred_at
        )
    }
    if listing_item_ids:
        candidates = [
            item
            for item_id in sorted(listing_item_ids, key=str)
            if (item := session.get(models.InventoryItem, item_id)) is not None
        ]
        return _without_other_consuming_sales(session, sale, candidates)

    # An imported or otherwise historical sale may outlive its marketplace
    # listing record. In that case only consider a non-active historical
    # master item with the exact title.
    # Never attach an old sale to a newer active copy merely because its title
    # happens to be the same.
    items = session.execute(
        select(models.InventoryItem).where(
            models.InventoryItem.workspace_id == sale.workspace_id,
            models.InventoryItem.status != ItemStatus.ACTIVE,
        )
    ).scalars().all()
    candidates = [
        item for item in items
        if _normalized_title(item.title) == normalized
    ]
    return _without_other_consuming_sales(session, sale, candidates)


def _sale_match_kind(
    session: Session,
    sale: models.Sale,
) -> tuple[str, list[models.InventoryItem]]:
    candidates = _sale_title_candidates(session, sale)
    if len(candidates) == 1:
        return "unique", candidates
    if len(candidates) > 1:
        return "ambiguous", candidates
    return "unmatched", []


def resolve_sale_item(
    session: Session,
    sale: models.Sale,
    *,
    external_item_id: str | None = None,
) -> models.InventoryItem | None:
    """Resolve a sale to stock using only exact identifiers.

    Existing explicit linkage wins. Otherwise an exact marketplace listing id
    is used. For historical Vinted orders that lack an item id, an exact
    normalized title is accepted only when it
    identifies exactly one listing in that workspace/channel.
    """
    if sale.inventory_item_id:
        item = session.get(models.InventoryItem, sale.inventory_item_id)
        if item is not None and item.workspace_id == sale.workspace_id:
            return item

    if external_item_id:
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == sale.workspace_id,
                models.ChannelListing.channel == sale.channel,
                models.ChannelListing.external_id == str(external_item_id),
            )
        ).scalar_one_or_none()
        if listing is not None and listing.inventory_item_id:
            sale.inventory_item_id = listing.inventory_item_id
            return session.get(models.InventoryItem, listing.inventory_item_id)

    kind, candidates = _sale_match_kind(session, sale)
    if kind != "unique":
        return None
    item = candidates[0]
    sale.inventory_item_id = item.id
    extra = dict(sale.extra or {})
    extra["auto_link_reason"] = "exact_unique_title"
    sale.extra = extra
    return item


def _action_mode(channel: str) -> tuple[str, str]:
    if channel == Channel.VINTED:
        return "manual", "attention"
    if channel in REMOTE_CHANNELS:
        return "remote", "queued"
    return "manual", "attention"


def _enqueue_action_job(session: Session, action: CrossChannelAction) -> None:
    session.add(
        BackgroundJob(
            workspace_id=action.workspace_id,
            job_type="cross_channel_close",
            payload={"action_id": str(action.id)},
            status="queued",
            available_at=utcnow(),
        )
    )
    # SessionLocal has autoflush disabled; make every planned action/job pair
    # immediately visible to subsequent queries in the same transaction.
    session.flush()


def auto_link_unlinked_sales(
    session: Session,
    workspace_id: uuid.UUID,
) -> dict[str, int]:
    """Safely link historical seller-side sales to existing physical stock.

    Only deterministic matches are accepted:
    1. exact marketplace item id already stored on the sale;
    2. exact normalized title resolving to one plausible historical item.

    Same-title duplicates remain manual. Historical sales with no retained
    stock record are left as unmatched history rather than forced onto a newer
    active copy.
    """
    sales = session.execute(
        select(models.Sale).where(
            models.Sale.workspace_id == workspace_id,
            models.Sale.direction == "sell",
            models.Sale.inventory_item_id.is_(None),
        )
    ).scalars().all()

    linked = 0
    ambiguous = 0
    unmatched = 0
    actions_created = 0
    for sale in sales:
        if not sale_counts_as_sold(sale):
            continue
        extra = dict(sale.extra or {})
        external_item_id = str(extra.get("item_external_id") or "").strip() or None
        item = resolve_sale_item(
            session,
            sale,
            external_item_id=external_item_id,
        )
        if item is None:
            kind, _candidates = _sale_match_kind(session, sale)
            if kind == "ambiguous":
                ambiguous += 1
            else:
                unmatched += 1
            continue
        session.flush()
        created = reconcile_sale_state(
            session,
            sale,
            external_item_id=external_item_id,
        )
        linked += 1
        actions_created += len(created)

    return {
        "linked": linked,
        "remaining": ambiguous + unmatched,
        "ambiguous": ambiguous,
        "unmatched": unmatched,
        "actions_created": actions_created,
    }


def unlinked_sale_reconciliation(
    session: Session,
    workspace_id: uuid.UUID,
) -> dict[str, Any]:
    """Classify remaining historical sales for the Reconcile UI.

    Only ambiguous exact-title matches require user input. Sales for which no
    retained stock record exists are counted as historical unmatched records
    but are not presented as mandatory reconciliation work.
    """
    sales = session.execute(
        select(models.Sale).where(
            models.Sale.workspace_id == workspace_id,
            models.Sale.direction == "sell",
            models.Sale.inventory_item_id.is_(None),
        )
    ).scalars().all()
    review: list[dict[str, Any]] = []
    historical_unmatched = 0
    for sale in sales:
        if not sale_counts_as_sold(sale):
            continue
        kind, candidates = _sale_match_kind(session, sale)
        if kind == "ambiguous":
            review.append(
                {
                    "id": str(sale.id),
                    "channel": sale.channel,
                    "external_order_id": sale.external_order_id,
                    "title": sale.title,
                    "status": sale.status,
                    "lifecycle_status": sale.lifecycle_status,
                    "occurred_at": sale.occurred_at.isoformat() if sale.occurred_at else None,
                    "candidate_item_ids": [str(item.id) for item in candidates],
                }
            )
        elif kind == "unmatched":
            historical_unmatched += 1
    review.sort(key=lambda row: row["occurred_at"] or "", reverse=True)
    return {
        "review": review,
        "review_count": len(review),
        "historical_unmatched_count": historical_unmatched,
    }


def plan_sale_reconciliation(
    session: Session,
    sale: models.Sale,
    *,
    external_item_id: str | None = None,
) -> list[CrossChannelAction]:
    if sale.direction != "sell" or not sale_counts_as_sold(sale):
        return []

    item = resolve_sale_item(session, sale, external_item_id=external_item_id)
    if item is None:
        return []

    if is_physical(item):
        recompute_inventory_item(session, item)
        still_available = item.quantity > 0
    else:
        still_available = item_has_remaining_stock_on_sale_channel(session, item, sale)
        if still_available:
            recompute_inventory_item(session, item)

    if still_available:
        pending = session.execute(
            select(CrossChannelAction).where(
                CrossChannelAction.trigger_sale_id == sale.id,
                CrossChannelAction.status.in_(["queued", "running", "attention", "error"]),
            )
        ).scalars().all()
        for action in pending:
            action.status = "cancelled"
            action.completed_at = utcnow()
            action.last_error = None
        return []

    item.quantity = 0
    item.status = ItemStatus.SOLD

    listings = session.execute(
        select(models.ChannelListing).where(
            models.ChannelListing.workspace_id == sale.workspace_id,
            models.ChannelListing.inventory_item_id == item.id,
            models.ChannelListing.channel != sale.channel,
            models.ChannelListing.status.in_(OPEN_LISTING_STATUSES),
        )
    ).scalars().all()

    created: list[CrossChannelAction] = []
    for listing in listings:
        existing = session.execute(
            select(CrossChannelAction).where(
                CrossChannelAction.trigger_sale_id == sale.id,
                CrossChannelAction.channel_listing_id == listing.id,
                CrossChannelAction.action_type == ACTION_TYPE,
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        mode, status = _action_mode(listing.channel)
        action = CrossChannelAction(
            workspace_id=sale.workspace_id,
            inventory_item_id=item.id,
            trigger_sale_id=sale.id,
            channel_listing_id=listing.id,
            channel=listing.channel,
            action_type=ACTION_TYPE,
            mode=mode,
            status=status,
            detail={
                "sale_channel": sale.channel,
                "sale_external_order_id": sale.external_order_id,
                "listing_external_id": listing.external_id,
            },
        )
        session.add(action)
        session.flush()
        if mode == "remote":
            _enqueue_action_job(session, action)
        created.append(action)
    return created


def reconcile_sale_state(
    session: Session,
    sale: models.Sale,
    *,
    external_item_id: str | None = None,
) -> list[CrossChannelAction]:
    """Apply the current sale lifecycle to stock and action planning.

    A later cancellation/refund cancels still-pending close actions and lets
    master stock be recomputed from marketplace listings. Jobs already in the
    queue are harmless because the worker treats a cancelled action as
    terminal.
    """
    if sale.direction != "sell":
        return []
    if sale_counts_as_sold(sale):
        return plan_sale_reconciliation(
            session,
            sale,
            external_item_id=external_item_id,
        )

    pending = session.execute(
        select(CrossChannelAction).where(
            CrossChannelAction.trigger_sale_id == sale.id,
            CrossChannelAction.status.in_(["queued", "running", "attention", "error"]),
        )
    ).scalars().all()
    for action in pending:
        action.status = "cancelled"
        action.completed_at = utcnow()
        action.last_error = None

    item = resolve_sale_item(session, sale, external_item_id=external_item_id)
    if item is not None:
        from app.connectors.workspace_sync import recompute_inventory_item

        recompute_inventory_item(session, item)
    return []


def _get_action(session: Session, action_id: uuid.UUID) -> CrossChannelAction:
    action = session.get(CrossChannelAction, action_id)
    if action is None:
        raise ValueError("Cross-channel action not found")
    return action


def execute_action(action_id: uuid.UUID) -> dict[str, Any]:
    with db.session_scope() as session:
        action = _get_action(session, action_id)
        if action.status in {"success", "acknowledged", "cancelled"}:
            return {"ok": True, "already_complete": True}
        if action.mode != "remote":
            raise RuntimeError("Manual action cannot be executed by the worker")
        listing = session.get(models.ChannelListing, action.channel_listing_id)
        if listing is None:
            action.status = "success"
            action.completed_at = utcnow()
            return {"ok": True, "listing_missing": True}
        workspace_id = action.workspace_id
        external_id = listing.external_id
        channel = action.channel
        action.status = "running"
        action.attempts += 1
        action.last_error = None

    if channel == Channel.EBAY:
        from app.connectors.hosted import close_ebay_workspace_listing

        detail = close_ebay_workspace_listing(workspace_id, external_id)
        terminal_status = ListingStatus.ENDED
    elif channel == Channel.BIBLIO:
        from app.connectors.hosted import close_biblio_workspace_listing

        detail = close_biblio_workspace_listing(workspace_id, action.channel_listing_id)
        terminal_status = ListingStatus.SOLD
    else:
        raise RuntimeError(f"Unsupported remote close channel: {channel}")

    with db.session_scope() as session:
        action = _get_action(session, action_id)
        listing = session.get(models.ChannelListing, action.channel_listing_id)
        completed_at = utcnow()
        if listing is not None:
            listing.status = terminal_status
            listing.quantity = 0
            if channel == Channel.BIBLIO and detail.get("inventory_signature"):
                # Commit the SOLD state and the exact remote delete signature
                # together. If the process dies before this transaction, the
                # close action can safely retry; it cannot leave an ACTIVE
                # local row carrying a SOLD remote signature that would be
                # re-added by the next incremental sync.
                extra = dict(listing.extra or {})
                extra["inventory_sync_signature"] = str(detail["inventory_signature"])
                extra["inventory_synced_at"] = completed_at.isoformat()
                extra["publish_state"] = "ftp_uploaded"
                extra["publish_completed_at"] = completed_at.isoformat()
                listing.extra = extra
        action.status = "success"
        action.completed_at = completed_at
        action.last_error = None
        action.detail = {**dict(action.detail or {}), **dict(detail or {})}
    return {"ok": True, **dict(detail or {})}


def record_action_failure(
    action_id: uuid.UUID,
    error: str,
    *,
    will_retry: bool,
) -> None:
    with db.session_scope() as session:
        action = _get_action(session, action_id)
        action.status = "queued" if will_retry else "error"
        action.last_error = str(error)[:2000]
        if not will_retry:
            action.completed_at = utcnow()


def acknowledge_manual_action(
    session: Session,
    workspace_id: uuid.UUID,
    action_id: uuid.UUID,
) -> CrossChannelAction:
    action = _get_action(session, action_id)
    if action.workspace_id != workspace_id:
        raise ValueError("Cross-channel action not found")
    if action.mode != "manual":
        raise ValueError("Only manual actions can be acknowledged")
    if action.status not in {"attention", "error"}:
        raise ValueError("Action is not awaiting manual attention")
    action.status = "acknowledged"
    action.completed_at = utcnow()
    action.last_error = None
    return action


def retry_action(
    session: Session,
    workspace_id: uuid.UUID,
    action_id: uuid.UUID,
) -> CrossChannelAction:
    action = _get_action(session, action_id)
    if action.workspace_id != workspace_id:
        raise ValueError("Cross-channel action not found")
    if action.mode != "remote" or action.status != "error":
        raise ValueError("Only failed remote actions can be retried")
    action.status = "queued"
    action.completed_at = None
    action.last_error = None
    _enqueue_action_job(session, action)
    return action


def serialize_actions(
    session: Session,
    workspace_id: uuid.UUID,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    rows = session.execute(
        select(CrossChannelAction)
        .where(CrossChannelAction.workspace_id == workspace_id)
        .order_by(CrossChannelAction.created_at.desc())
        .limit(limit)
    ).scalars().all()
    result: list[dict[str, Any]] = []
    for action in rows:
        listing = session.get(models.ChannelListing, action.channel_listing_id)
        sale = session.get(models.Sale, action.trigger_sale_id)
        item = session.get(models.InventoryItem, action.inventory_item_id) if action.inventory_item_id else None
        result.append(
            {
                "id": str(action.id),
                "status": action.status,
                "mode": action.mode,
                "channel": action.channel,
                "action_type": action.action_type,
                "attempts": action.attempts,
                "last_error": action.last_error,
                "created_at": action.created_at.isoformat() if action.created_at else None,
                "completed_at": action.completed_at.isoformat() if action.completed_at else None,
                "listing": {
                    "id": str(listing.id),
                    "external_id": listing.external_id,
                    "title": listing.title,
                    "status": listing.status,
                    "url": listing.url,
                } if listing else None,
                "sale": {
                    "id": str(sale.id),
                    "channel": sale.channel,
                    "external_order_id": sale.external_order_id,
                    "title": sale.title,
                } if sale else None,
                "item": {
                    "id": str(item.id),
                    "sku": item.sku,
                    "title": item.title,
                } if item else None,
            }
        )
    return result


def unlinked_sell_count(session: Session, workspace_id: uuid.UUID) -> int:
    return int(unlinked_sale_reconciliation(session, workspace_id)["review_count"])
