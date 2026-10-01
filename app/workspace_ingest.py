"""Workspace-scoped ingestion for the paired Chrome bridge.

Unlike the legacy personal-dashboard snapshot writer, this module never
assumes a single user.  Every row is written directly to the ORM schema under
the credential's workspace.  The bootstrap workspace can optionally keep the
legacy sqlite history in sync for backwards-compatible personal use.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app import db, models
from app.connectors.workspace_sync import recompute_inventory_item
from app.cross_channel import reconcile_sale_state
from app.constants import (
    Channel,
    ChannelAccountStatus,
    ItemCategory,
    ItemStatus,
    ListingStatus,
    SyncRunStatus,
)
from app.workspace_bootstrap import (
    BOOTSTRAP_WORKSPACE_SLUG,
    get_or_create_channel_account,
    normalize_sku,
)


def _dt(value: Any, fallback: datetime) -> datetime:
    if value in (None, ""):
        return fallback
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return fallback


def _int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _listing_item(session, workspace, account, row: dict[str, Any], captured_at: datetime):
    external_id = str(row.get("id") or row.get("source_id") or "").strip()
    if not external_id:
        return None
    listing = session.execute(
        select(models.ChannelListing).where(
            models.ChannelListing.workspace_id == workspace.id,
            models.ChannelListing.channel == Channel.VINTED,
            models.ChannelListing.external_id == external_id,
        )
    ).scalar_one_or_none()

    item = None
    if listing is not None and listing.inventory_item_id:
        item = session.get(models.InventoryItem, listing.inventory_item_id)
    external_sku = normalize_sku(row.get("sku"))
    if item is None and external_sku:
        item = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == workspace.id,
                models.InventoryItem.sku == external_sku,
            )
        ).scalar_one_or_none()
    if item is None:
        sku = external_sku or f"VINTED-{external_id}"
        item = models.InventoryItem(
            workspace_id=workspace.id,
            sku=sku,
            title=str(row.get("title") or "Untitled"),
            category=ItemCategory.GENERAL,
            quantity=1,
            status=ItemStatus.ACTIVE,
            currency=row.get("currency") or "EUR",
            attributes={},
        )
        session.add(item)
        session.flush()

    status = str(row.get("status") or ListingStatus.ACTIVE).lower()
    quantity = 0 if status in {ListingStatus.SOLD, ListingStatus.ENDED, ListingStatus.INACTIVE} else 1
    if listing is None:
        listing = models.ChannelListing(
            workspace_id=workspace.id,
            inventory_item_id=item.id,
            channel_account_id=account.id,
            channel=Channel.VINTED,
            external_id=external_id,
            title=str(row.get("title") or "Untitled"),
            first_seen_at=captured_at,
            last_seen_at=captured_at,
        )
        session.add(listing)

    listing.inventory_item_id = item.id
    listing.channel_account_id = account.id
    listing.external_sku = external_sku
    listing.title = str(row.get("title") or "Untitled")
    listing.price_cents = _int(row.get("price_cents"))
    listing.currency = row.get("currency") or "EUR"
    listing.status = status
    listing.url = row.get("vinted_url") or row.get("url")
    listing.quantity = quantity
    listing.last_seen_at = captured_at
    existing_extra = dict(listing.extra or {})
    listed_at = row.get("listed_at") or existing_extra.get("listed_at")
    listing.extra = {
        **existing_extra,
        "listed_at": listed_at,
    }
    session.flush()

    snap_exists = session.execute(
        select(models.ListingSnapshot.id).where(
            models.ListingSnapshot.channel_listing_id == listing.id,
            models.ListingSnapshot.captured_at == captured_at,
        )
    ).scalar_one_or_none()
    if snap_exists is None:
        session.add(
            models.ListingSnapshot(
                channel_listing_id=listing.id,
                captured_at=captured_at,
                price_cents=_int(row.get("price_cents")),
                status=status,
                views=_int(row.get("views")),
                favourites=_int(row.get("favourites")),
                raw={
                    "listed_at": row.get("listed_at"),
                    "url": row.get("vinted_url") or row.get("url"),
                },
            )
        )

    item.title = listing.title
    item.currency = listing.currency or item.currency
    recompute_inventory_item(session, item)
    return listing


def record_workspace_snapshot(
    workspace_id: uuid.UUID,
    snapshot: dict[str, Any],
    *,
    extension_version: str | None = None,
) -> dict[str, int]:
    collected = float(snapshot.get("collected_at") or time.time())
    captured_at = datetime.fromtimestamp(collected, tz=timezone.utc)
    listings = list(snapshot.get("listings") or [])
    notifications = list(snapshot.get("notifications") or [])
    orders = list(snapshot.get("orders") or [])

    with db.session_scope() as session:
        workspace = session.get(models.Workspace, workspace_id)
        if workspace is None:
            raise ValueError("Workspace does not exist")
        account, _ = get_or_create_channel_account(session, workspace, Channel.VINTED, {})
        account.status = ChannelAccountStatus.CONNECTED
        account.last_synced_at = captured_at
        config = dict(account.config or {})
        if extension_version:
            config["extension_version"] = extension_version
        account.config = config

        seen: set[str] = set()
        listing_by_external: dict[str, models.ChannelListing] = {}
        for row in listings:
            listing = _listing_item(session, workspace, account, row, captured_at)
            if listing is not None:
                seen.add(listing.external_id)
                listing_by_external[listing.external_id] = listing

        active_missing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace.id,
                models.ChannelListing.channel == Channel.VINTED,
                models.ChannelListing.status == ListingStatus.ACTIVE,
            )
        ).scalars().all()
        for listing in active_missing:
            if listing.external_id not in seen:
                listing.status = ListingStatus.INACTIVE
                listing.quantity = 0
                listing.last_seen_at = captured_at
                if listing.inventory_item_id:
                    item = session.get(models.InventoryItem, listing.inventory_item_id)
                    if item is not None:
                        recompute_inventory_item(session, item)

        current = snapshot.get("current_user") or {}
        profile_exists = session.execute(
            select(models.ProfileObservation.id).where(
                models.ProfileObservation.channel_account_id == account.id,
                models.ProfileObservation.captured_at == captured_at,
            )
        ).scalar_one_or_none()
        if profile_exists is None and (
            current.get("followers_count") is not None or current.get("following_count") is not None
        ):
            session.add(
                models.ProfileObservation(
                    channel_account_id=account.id,
                    captured_at=captured_at,
                    followers=_int(current.get("followers_count")),
                    following=_int(current.get("following_count")),
                )
            )

        favorites = 0
        for notification in notifications:
            if notification.get("category") != "favorite":
                continue
            raw_id = str(notification.get("id") or "")
            if not raw_id:
                continue
            notification_id = f"{workspace.id}:{raw_id}"
            if session.get(models.FavoriteEvent, notification_id) is not None:
                continue
            item_external = str(notification.get("item_id") or "") or None
            listing = listing_by_external.get(item_external or "")
            session.add(
                models.FavoriteEvent(
                    notification_id=notification_id,
                    workspace_id=workspace.id,
                    channel_listing_id=listing.id if listing else None,
                    item_external_id=item_external,
                    item_title=notification.get("item_title"),
                    actor=notification.get("actor"),
                    occurred_at=_dt(notification.get("occurred_at"), captured_at),
                    first_seen_at=captured_at,
                )
            )
            favorites += 1

        sales = 0
        for order in orders:
            direction = str(order.get("direction") or "")
            external_id = str(order.get("id") or order.get("thread_id") or "")
            if direction not in {"sell", "buy"} or not external_id:
                continue
            sale = session.execute(
                select(models.Sale).where(
                    models.Sale.workspace_id == workspace.id,
                    models.Sale.channel == Channel.VINTED,
                    models.Sale.direction == direction,
                    models.Sale.external_order_id == external_id,
                )
            ).scalar_one_or_none()
            if sale is None:
                sale = models.Sale(
                    workspace_id=workspace.id,
                    channel_account_id=account.id,
                    channel=Channel.VINTED,
                    external_order_id=external_id,
                    direction=direction,
                    first_seen_at=captured_at,
                    last_seen_at=captured_at,
                )
                session.add(sale)
                sales += 1
            sale.channel_account_id = account.id
            sale.title = order.get("title")
            sale.counterparty = order.get("counterparty")
            sale.total_cents = _int(order.get("total_cents"))
            sale.currency = order.get("currency") or "EUR"
            sale.status = order.get("status")
            sale.lifecycle_status = order.get("lifecycle_status")
            sale.is_closed = bool(order.get("is_closed"))
            sale.occurred_at = _dt(order.get("updated_at"), captured_at)
            sale.last_seen_at = captured_at
            item_external_id = str(order.get("item_id") or "").strip() or None
            sale.extra = {
                "url": order.get("vinted_url"),
                "item_external_id": item_external_id,
            }
            session.flush()
            reconcile_sale_state(
                session,
                sale,
                external_item_id=item_external_id,
            )

        run_exists = session.execute(
            select(models.ConnectorSyncRun.id).where(
                models.ConnectorSyncRun.workspace_id == workspace.id,
                models.ConnectorSyncRun.channel == Channel.VINTED,
                models.ConnectorSyncRun.run_type == "browser_snapshot",
                models.ConnectorSyncRun.started_at == captured_at,
            )
        ).scalar_one_or_none()
        if run_exists is None:
            session.add(
                models.ConnectorSyncRun(
                    workspace_id=workspace.id,
                    channel_account_id=account.id,
                    channel=Channel.VINTED,
                    run_type="browser_snapshot",
                    status=SyncRunStatus.SUCCESS,
                    started_at=captured_at,
                    completed_at=captured_at,
                    item_count=len(seen),
                    active_count=sum(
                        1 for row in listings
                        if str(row.get("status") or "active").lower() == ListingStatus.ACTIVE
                    ),
                    detail={"extension_version": extension_version} if extension_version else {},
                )
            )

    return {
        "listings": len(seen),
        "favorites_added": favorites,
        "orders_added": sales,
    }


def maybe_record_legacy_snapshot(workspace_slug: str, snapshot: dict[str, Any]) -> None:
    enabled = os.getenv("LEGACY_COMPAT_SYNC", "false").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled or workspace_slug != BOOTSTRAP_WORKSPACE_SLUG:
        return
    from app.intelligence import record_snapshot
    from app.channels import record_vinted_items

    record_snapshot(snapshot)
    record_vinted_items(
        list(snapshot.get("listings") or []),
        float(snapshot.get("collected_at") or time.time()),
    )
