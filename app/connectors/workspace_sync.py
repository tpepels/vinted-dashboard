"""Live dual-write: keeps the workspace/inventory ORM schema (``app.models``)
continuously up to date as each real connector sync runs, so it is never
solely dependent on the one-time legacy batch backfill
(:mod:`app.legacy_migration`).

:func:`record_channel_snapshot` is called from
:func:`app.channels.upsert_channel_snapshot` - the single chokepoint all
three inbound connectors (Vinted browser-sync, BIBLIO file import, eBay
``GetMyeBaySelling`` pull) already funnel through - and mirrors its
``channel_items``/``channel_sync_runs`` writes into
``InventoryItem``/``ChannelListing``/``ConnectorSyncRun``.

:func:`record_biblio_ftp_run` is called from
:func:`app.channels._record_biblio_ftp_run` - the outbound BIBLIO FTP
test/sync bookkeeping hook - and mirrors its ``biblio_ftp_runs`` writes into
``ConnectorSyncRun``.

Both entry points match the matching/field conventions of
:mod:`app.legacy_migration` (synthesized ``CHANNEL-external_id`` SKUs for
items with no real SKU, ``ftp_{action}`` run types, etc.) so a workspace
populated by live syncs and one populated by the batch backfill end up with
equivalent data. Unlike the batch backfill, live matching only merges
items by exact SKU (never by ISBN) - see the module docstring note below.

Both entry points are best-effort: any failure is logged and swallowed so a
dual-write bug can never break the legacy sync path that remains the
authoritative data store for this phase.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db, models
from app.constants import Channel, ItemCategory, ItemStatus, ListingStatus, SyncRunStatus
from app.stock_policy import sale_counts_as_sold
from app.workspace_bootstrap import (
    clean_isbn,
    get_or_create_bootstrap_workspace,
    get_or_create_channel_account,
    normalize_sku,
)

logger = logging.getLogger(__name__)


def _effective_sku(channel: str, external_id: str, raw_sku: Any) -> str:
    """The real SKU if the item has one, otherwise a synthesized
    per-listing SKU - identical convention to the one
    :mod:`app.legacy_migration` uses for ``channel_items`` rows with no SKU
    and no ISBN, so both paths treat a no-SKU item the same way."""
    sku = normalize_sku(raw_sku)
    return sku if sku is not None else f"{channel.upper()}-{external_id}"


def _is_book(listings: list[models.ChannelListing], attributes: dict[str, Any]) -> bool:
    return (
        any(listing.channel == Channel.BIBLIO for listing in listings)
        or bool(attributes.get("author"))
        or bool(attributes.get("isbn"))
    )


def _apply_item(
    session: Session,
    workspace: models.Workspace,
    account: models.ChannelAccount,
    channel: str,
    external_id: str,
    item: dict[str, Any],
    *,
    seen_at: datetime,
) -> None:
    sku = _effective_sku(channel, external_id, item.get("sku"))
    title = str(item.get("title") or "Untitled").strip() or "Untitled"
    status = str(item.get("status") or ListingStatus.ACTIVE).lower()
    quantity = item.get("quantity")
    quantity = int(quantity) if quantity is not None else None

    extra: dict[str, Any] = {}
    if item.get("author"):
        extra["author"] = item["author"]
    if item.get("description"):
        extra["description"] = item["description"]
    if item.get("listed_at"):
        extra["listed_at"] = item["listed_at"]
    isbn = clean_isbn(item.get("isbn"))
    if isbn:
        extra["isbn"] = isbn

    listing = session.execute(
        select(models.ChannelListing).where(
            models.ChannelListing.workspace_id == workspace.id,
            models.ChannelListing.channel == channel,
            models.ChannelListing.external_id == external_id,
        )
    ).scalar_one_or_none()

    # Preserve an explicit reconciliation. Once a marketplace listing has
    # been linked to a master item, later connector snapshots must update that
    # listing in place instead of silently moving it back to a SKU-derived
    # item.
    inventory_item = None
    if listing is not None and listing.inventory_item_id is not None:
        inventory_item = session.get(models.InventoryItem, listing.inventory_item_id)
    if inventory_item is None:
        inventory_item = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == workspace.id,
                models.InventoryItem.sku == sku,
            )
        ).scalar_one_or_none()
    if inventory_item is None:
        inventory_item = models.InventoryItem(
            workspace_id=workspace.id,
            sku=sku,
            title=title,
            category=ItemCategory.GENERAL,
            quantity=0,
            status=ItemStatus.ARCHIVED,
            attributes={},
        )
        session.add(inventory_item)
        session.flush()

    if listing is None:
        listing = models.ChannelListing(
            workspace_id=workspace.id,
            inventory_item_id=inventory_item.id,
            channel_account_id=account.id,
            channel=channel,
            external_id=external_id,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
        )
        session.add(listing)

    listing.inventory_item_id = inventory_item.id
    listing.channel_account_id = account.id
    listing.external_sku = normalize_sku(item.get("sku"))
    listing.title = title
    listing.price_cents = item.get("price_cents")
    listing.currency = item.get("currency")
    listing.status = status
    listing.url = item.get("url")
    listing.quantity = quantity
    listing.last_seen_at = seen_at
    listing.extra = {**(listing.extra or {}), **extra}
    _apply_generic_metadata(inventory_item, listing, item)
    session.flush()

    recompute_inventory_item(session, inventory_item)


def _category_hint(value: Any) -> str | None:
    text = str(value or "").casefold()
    if not text:
        return None
    rules = (
        (ItemCategory.BOOK, ("book", "books", "livro", "livros", "libro", "libros")),
        (ItemCategory.CLOTHING, ("clothing", "clothes", "apparel", "fashion", "shirt", "dress", "jacket", "trouser", "pants", "jeans", "shoe", "footwear")),
        (ItemCategory.ELECTRONICS, ("electronics", "computer", "phone", "camera", "audio", "video game console")),
        (ItemCategory.HOME, ("home", "furniture", "kitchen", "decor", "garden", "houseware")),
        (ItemCategory.COLLECTIBLES, ("collectible", "collectibles", "memorabilia", "antique", "vintage collectible")),
        (ItemCategory.TOYS_GAMES, ("toy", "toys", "board game", "games", "puzzle")),
        (ItemCategory.MEDIA, ("music", "movie", "movies", "dvd", "blu-ray", "vinyl", "cd", "media")),
        (ItemCategory.SPORTS, ("sport", "sports", "fitness", "cycling", "outdoor gear")),
        (ItemCategory.BEAUTY, ("beauty", "cosmetic", "skincare", "fragrance", "perfume")),
        (ItemCategory.ART_CRAFTS, ("art", "craft", "crafts", "handmade", "artwork", "supplies")),
    )
    for category, tokens in rules:
        if any(token in text for token in tokens):
            return category
    return None


def _apply_generic_metadata(
    inventory_item: models.InventoryItem,
    listing: models.ChannelListing,
    item: dict[str, Any],
) -> None:
    remote: dict[str, Any] = {}
    for key in (
        "category", "condition", "brand", "size", "color", "colour", "material",
        "description", "image_url", "tags", "attributes", "product_type", "taxonomy_id",
    ):
        value = item.get(key)
        if value not in (None, "", [], {}):
            remote[key] = value
    if remote:
        listing.extra = {**(listing.extra or {}), "remote_metadata": remote}

    if not inventory_item.condition and item.get("condition"):
        inventory_item.condition = str(item["condition"]).strip() or None

    attrs = dict(inventory_item.attributes or {})
    for key in ("brand", "size", "color", "colour", "material"):
        value = item.get(key)
        if value not in (None, "") and key not in attrs:
            attrs[key] = value
    if item.get("category") not in (None, "") and "marketplace_category" not in attrs:
        attrs["marketplace_category"] = item["category"]
    if item.get("image_url") not in (None, "") and "image_url" not in attrs:
        attrs["image_url"] = item["image_url"]
    if item.get("tags") not in (None, [], "") and "tags" not in attrs:
        attrs["tags"] = item["tags"]
    if item.get("attributes") not in (None, {}, "") and "remote_attributes" not in attrs:
        attrs["remote_attributes"] = item["attributes"]
    inventory_item.attributes = attrs

    hinted = _category_hint(item.get("category"))
    if hinted and inventory_item.category == ItemCategory.GENERAL:
        inventory_item.category = hinted


def item_has_remaining_stock_on_sale_channel(
    session: Session,
    item: models.InventoryItem,
    sale: models.Sale,
) -> bool:
    """Return whether a quantity-aware selling channel still reports stock.

    Vinted and the established one-off connector semantics remain exhaustive:
    a seller-side sale consumes the physical item even if a stale active
    listing is still present. Store/catalog connectors can represent multi-unit
    stock, so an active same-channel listing with remaining quantity keeps the
    master item active.
    """
    if sale.channel not in {
        Channel.ETSY,
        Channel.WOOCOMMERCE,
        Channel.SHOPIFY,
        Channel.BIGCOMMERCE,
        Channel.SQUARESPACE,
        Channel.WIX,
        Channel.DEPOP,
    }:
        return False
    listings = session.execute(
        select(models.ChannelListing).where(
            models.ChannelListing.workspace_id == item.workspace_id,
            models.ChannelListing.inventory_item_id == item.id,
            models.ChannelListing.channel == sale.channel,
            models.ChannelListing.status == ListingStatus.ACTIVE,
        )
    ).scalars().all()
    return any(
        listing.quantity is None or int(listing.quantity or 0) > 0
        for listing in listings
    )

def recompute_inventory_item(session: Session, item: models.InventoryItem) -> None:
    """Re-derives an ``InventoryItem``'s aggregate quantity/status/category
    from *all* of its current listings (not just the one just touched),
    since items with a real SKU can be shared across channels. Mirrors
    ``app.legacy_migration``'s ``_group_quantity``/``_group_status``/
    ``_summarize_group`` semantics."""
    listings = (
        session.execute(
            select(models.ChannelListing)
            .where(models.ChannelListing.inventory_item_id == item.id)
            .order_by(models.ChannelListing.first_seen_at)
        )
        .scalars()
        .all()
    )
    if not listings:
        return

    consuming_sales = session.execute(
        select(models.Sale).where(
            models.Sale.workspace_id == item.workspace_id,
            models.Sale.inventory_item_id == item.id,
            models.Sale.direction == "sell",
        )
    ).scalars().all()
    consuming = [sale for sale in consuming_sales if sale_counts_as_sold(sale)]
    has_remaining_sale_channel_stock = any(
        item_has_remaining_stock_on_sale_channel(session, item, sale)
        for sale in consuming
    )
    statuses = {listing.status for listing in listings}
    if consuming and not has_remaining_sale_channel_stock:
        item.status = ItemStatus.SOLD
        item.quantity = 0
    elif ListingStatus.ACTIVE in statuses:
        item.status = ItemStatus.ACTIVE
        item.quantity = max(
            (listing.quantity or 0)
            for listing in listings
            if listing.status == ListingStatus.ACTIVE
        )
    elif statuses and statuses <= {ListingStatus.SOLD}:
        item.status = ItemStatus.SOLD
        item.quantity = 0
    else:
        item.status = ItemStatus.ARCHIVED
        item.quantity = 0

    attributes = dict(item.attributes)
    for listing in listings:
        if listing.extra.get("author") and "author" not in attributes:
            attributes["author"] = listing.extra["author"]
        if listing.extra.get("isbn") and "isbn" not in attributes:
            attributes["isbn"] = listing.extra["isbn"]
    item.attributes = attributes
    if _is_book(listings, attributes):
        item.category = ItemCategory.BOOK
    elif item.category == ItemCategory.GENERAL:
        hinted = next(
            (
                _category_hint((listing.extra or {}).get("remote_metadata", {}).get("category"))
                for listing in listings
                if _category_hint((listing.extra or {}).get("remote_metadata", {}).get("category"))
            ),
            None,
        )
        if hinted:
            item.category = hinted

    if item.title == "Untitled":
        better_title = next((listing.title for listing in listings if listing.title != "Untitled"), None)
        if better_title:
            item.title = better_title


def _deactivate_missing_listings(
    session: Session,
    workspace: models.Workspace,
    channel: str,
    seen_external_ids: set[str],
    seen_at: datetime,
) -> None:
    """Mirrors ``upsert_channel_snapshot``'s full-snapshot behavior: any
    listing for this channel that was active but is absent from this sync
    is now inactive (sold/removed/delisted elsewhere)."""
    query = select(models.ChannelListing).where(
        models.ChannelListing.workspace_id == workspace.id,
        models.ChannelListing.channel == channel,
        models.ChannelListing.status == ListingStatus.ACTIVE,
    )
    if seen_external_ids:
        query = query.where(models.ChannelListing.external_id.not_in(seen_external_ids))
    missing = session.execute(query).scalars().all()
    for listing in missing:
        listing.status = ListingStatus.INACTIVE
        listing.quantity = 0
        listing.last_seen_at = seen_at
    session.flush()

    touched_items: dict[Any, models.InventoryItem] = {}
    for listing in missing:
        if listing.inventory_item_id is not None:
            item = touched_items.get(listing.inventory_item_id) or session.get(
                models.InventoryItem, listing.inventory_item_id
            )
            if item is not None:
                touched_items[listing.inventory_item_id] = item
    for item in touched_items.values():
        recompute_inventory_item(session, item)


def _upsert_connector_sync_run(
    session: Session,
    workspace: models.Workspace,
    account: models.ChannelAccount,
    *,
    channel: str,
    run_type: str,
    status: str,
    started_at: datetime,
    item_count: Optional[int] = None,
    active_count: Optional[int] = None,
    delete_count: Optional[int] = None,
    detail: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    """Inserts a ``ConnectorSyncRun`` audit row, guarding against the table's
    ``(workspace_id, channel, run_type, started_at)`` unique constraint so a
    retried call with an identical timestamp is a safe no-op instead of
    failing the whole dual-write transaction."""
    exists = session.execute(
        select(models.ConnectorSyncRun.id).where(
            models.ConnectorSyncRun.workspace_id == workspace.id,
            models.ConnectorSyncRun.channel == channel,
            models.ConnectorSyncRun.run_type == run_type,
            models.ConnectorSyncRun.started_at == started_at,
        )
    ).scalar_one_or_none()
    if exists is not None:
        return
    session.add(
        models.ConnectorSyncRun(
            workspace_id=workspace.id,
            channel_account_id=account.id,
            channel=channel,
            run_type=run_type,
            status=status,
            started_at=started_at,
            completed_at=started_at,
            item_count=item_count,
            active_count=active_count,
            delete_count=delete_count,
            detail=detail or {},
            error=error,
        )
    )
    account.last_synced_at = started_at


def record_channel_snapshot(
    channel: str,
    items: list[dict[str, Any]],
    *,
    synced_at: datetime,
    full_snapshot: bool,
    active_count: int,
    note: Optional[str] = None,
) -> None:
    """Best-effort dual-write for an inbound connector snapshot (Vinted
    browser-sync, BIBLIO file import, or eBay pull) - call after the legacy
    ``channel_items``/``channel_sync_runs`` write already succeeded, mirroring
    the same data into the workspace/inventory ORM schema. Never raises."""
    try:
        with db.session_scope() as session:
            workspace = get_or_create_bootstrap_workspace(session)
            account, _created = get_or_create_channel_account(session, workspace, channel, {})

            seen_external_ids: set[str] = set()
            for item in items:
                external_id = str(item.get("source_id") or item.get("id") or "").strip()
                if not external_id:
                    continue
                seen_external_ids.add(external_id)
                _apply_item(session, workspace, account, channel, external_id, item, seen_at=synced_at)

            if full_snapshot:
                _deactivate_missing_listings(session, workspace, channel, seen_external_ids, synced_at)

            _upsert_connector_sync_run(
                session,
                workspace,
                account,
                channel=channel,
                run_type="snapshot",
                status=SyncRunStatus.SUCCESS,
                started_at=synced_at,
                item_count=len(seen_external_ids),
                active_count=active_count,
                detail={"note": note} if note else {},
            )
    except Exception:
        logger.exception("workspace_sync: failed to record %s channel snapshot", channel)


def record_workspace_channel_snapshot(
    workspace_id,
    channel: str,
    items: list[dict[str, Any]],
    *,
    synced_at: datetime,
    full_snapshot: bool,
    note: Optional[str] = None,
) -> dict[str, int]:
    """Strict workspace-scoped snapshot writer for hosted connector adapters.

    Unlike :func:`record_channel_snapshot`, this does not fall back to the
    bootstrap workspace and does not swallow errors. Hosted jobs need failures
    to propagate so the background queue and connector status can report them.
    """
    with db.session_scope() as session:
        workspace = session.get(models.Workspace, workspace_id)
        if workspace is None:
            raise ValueError("Workspace does not exist")
        account, _created = get_or_create_channel_account(session, workspace, channel, {})
        account.status = "connected"

        seen_external_ids: set[str] = set()
        active_count = 0
        for item in items:
            external_id = str(item.get("source_id") or item.get("id") or "").strip()
            if not external_id:
                continue
            seen_external_ids.add(external_id)
            if str(item.get("status") or ListingStatus.ACTIVE).lower() == ListingStatus.ACTIVE:
                active_count += 1
            _apply_item(session, workspace, account, channel, external_id, item, seen_at=synced_at)

        if full_snapshot:
            _deactivate_missing_listings(session, workspace, channel, seen_external_ids, synced_at)

        _upsert_connector_sync_run(
            session,
            workspace,
            account,
            channel=channel,
            run_type="snapshot",
            status=SyncRunStatus.SUCCESS,
            started_at=synced_at,
            item_count=len(seen_external_ids),
            active_count=active_count,
            detail={"note": note} if note else {},
        )
    return {"items": len(seen_external_ids), "active": active_count}


def record_workspace_channel_orders(
    workspace_id,
    channel: str,
    orders: list[dict[str, Any]],
    *,
    synced_at: datetime,
) -> dict[str, int]:
    """Upsert connector order lines into the shared Sale ledger.

    One row represents one marketplace order line so a multi-item order can
    reconcile to multiple physical inventory items. Existing explicit links
    are preserved. New rows match by exact channel listing identity first,
    then by exact SKU.
    """
    with db.session_scope() as session:
        workspace = session.get(models.Workspace, workspace_id)
        if workspace is None:
            raise ValueError("Workspace does not exist")
        account, _created = get_or_create_channel_account(session, workspace, channel, {})
        account.status = "connected"
        account.last_synced_at = synced_at

        touched: dict[Any, models.InventoryItem] = {}
        written = 0
        linked = 0
        for raw in orders:
            external_order_id = str(raw.get("external_order_id") or raw.get("id") or "").strip()
            if not external_order_id:
                continue
            sale = session.execute(
                select(models.Sale).where(
                    models.Sale.workspace_id == workspace.id,
                    models.Sale.channel == channel,
                    models.Sale.direction == "sell",
                    models.Sale.external_order_id == external_order_id,
                )
            ).scalar_one_or_none()

            inventory_item = None
            if sale is not None and sale.inventory_item_id is not None:
                inventory_item = session.get(models.InventoryItem, sale.inventory_item_id)
            listing_external_id = str(raw.get("listing_external_id") or "").strip()
            if inventory_item is None and listing_external_id:
                listing = session.execute(
                    select(models.ChannelListing).where(
                        models.ChannelListing.workspace_id == workspace.id,
                        models.ChannelListing.channel == channel,
                        models.ChannelListing.external_id == listing_external_id,
                    )
                ).scalar_one_or_none()
                if listing is not None and listing.inventory_item_id is not None:
                    inventory_item = session.get(models.InventoryItem, listing.inventory_item_id)
            sku = normalize_sku(raw.get("sku"))
            if inventory_item is None and sku:
                inventory_item = session.execute(
                    select(models.InventoryItem).where(
                        models.InventoryItem.workspace_id == workspace.id,
                        models.InventoryItem.sku == sku,
                    )
                ).scalar_one_or_none()

            occurred_at = raw.get("occurred_at")
            if isinstance(occurred_at, str):
                try:
                    occurred_at = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
                except ValueError:
                    occurred_at = None

            if sale is None:
                sale = models.Sale(
                    workspace_id=workspace.id,
                    channel_account_id=account.id,
                    inventory_item_id=inventory_item.id if inventory_item else None,
                    channel=channel,
                    external_order_id=external_order_id,
                    direction="sell",
                    first_seen_at=synced_at,
                    last_seen_at=synced_at,
                    extra={},
                )
                session.add(sale)
            elif sale.inventory_item_id is None and inventory_item is not None:
                sale.inventory_item_id = inventory_item.id

            sale.channel_account_id = account.id
            sale.title = str(raw.get("title") or sale.title or "Marketplace sale")[:500]
            sale.counterparty = (
                str(raw.get("counterparty"))[:200]
                if raw.get("counterparty") not in (None, "")
                else sale.counterparty
            )
            sale.total_cents = raw.get("total_cents")
            sale.currency = raw.get("currency") or sale.currency
            sale.status = str(raw.get("status") or sale.status or "open")
            sale.lifecycle_status = str(
                raw.get("lifecycle_status") or sale.lifecycle_status or sale.status
            )
            sale.is_closed = bool(raw.get("is_closed"))
            sale.occurred_at = occurred_at or sale.occurred_at
            sale.last_seen_at = synced_at
            sale.extra = {
                **(sale.extra or {}),
                **dict(raw.get("extra") or {}),
                "sku": sku,
                "listing_external_id": listing_external_id or None,
                "quantity": raw.get("quantity"),
            }
            written += 1
            if sale.inventory_item_id is not None:
                linked += 1
                item = inventory_item or session.get(models.InventoryItem, sale.inventory_item_id)
                if item is not None:
                    touched[item.id] = item

        session.flush()
        from app.cross_channel import reconcile_sale_state

        written_ids = [
            str(raw.get("external_order_id") or raw.get("id") or "").strip()
            for raw in orders
            if str(raw.get("external_order_id") or raw.get("id") or "").strip()
        ]
        if written_ids:
            for sale in session.execute(
                select(models.Sale).where(
                    models.Sale.workspace_id == workspace.id,
                    models.Sale.channel == channel,
                    models.Sale.direction == "sell",
                    models.Sale.external_order_id.in_(written_ids),
                )
            ).scalars().all():
                reconcile_sale_state(
                    session,
                    sale,
                    external_item_id=str(
                        (sale.extra or {}).get("listing_external_id") or ""
                    ).strip() or None,
                )
        for item in touched.values():
            recompute_inventory_item(session, item)
    return {"orders": written, "linked": linked}


def record_biblio_ftp_run(
    action: str,
    status: str,
    *,
    attempted_at: datetime,
    inventory_filename: Optional[str] = None,
    deletes_filename: Optional[str] = None,
    active_count: int = 0,
    delete_count: int = 0,
    detail: Optional[str] = None,
) -> None:
    """Best-effort dual-write for a BIBLIO FTP test/sync run - call after the
    legacy ``biblio_ftp_runs`` write already succeeded. Never raises."""
    try:
        run_status = SyncRunStatus.SUCCESS if status == "success" else SyncRunStatus.ERROR
        with db.session_scope() as session:
            workspace = get_or_create_bootstrap_workspace(session)
            account, _created = get_or_create_channel_account(session, workspace, Channel.BIBLIO, {})
            _upsert_connector_sync_run(
                session,
                workspace,
                account,
                channel=Channel.BIBLIO,
                run_type=f"ftp_{action}",
                status=run_status,
                started_at=attempted_at,
                active_count=active_count,
                delete_count=delete_count,
                detail={
                    "inventory_filename": inventory_filename,
                    "deletes_filename": deletes_filename,
                },
                error=detail if run_status == SyncRunStatus.ERROR else None,
            )
    except Exception:
        logger.exception("workspace_sync: failed to record BIBLIO ftp_%s run", action)
