"""Workspace-native connector persistence services.

This module is the strict write boundary from marketplace adapters into the
shared workspace data model. Connector snapshots update InventoryItem,
ChannelListing and ConnectorSyncRun rows; connector orders update the shared
Sale ledger.

Runtime connector writes are workspace-scoped and failures propagate to the
caller so job/connector health reflects real persistence failures. Legacy
SQLite migration is handled separately by :mod:`app.legacy_migration`.

Matching is conservative: live connector snapshots merge physical inventory by
exact SKU. A listing without a SKU receives a stable synthesized
`CHANNEL-external_id` SKU. Explicit reconciliation links are preserved across
later syncs.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db, models
from app.constants import Channel, ItemCategory, ItemStatus, ListingStatus, SyncRunStatus
from app.stock_policy import sale_counts_as_sold
from app.workspace_bootstrap import (
    clean_isbn,
    get_or_create_channel_account,
    normalize_sku,
)


def _effective_sku(channel: str, external_id: str, raw_sku: Any) -> str:
    """Return a real SKU or a stable per-listing synthesized SKU."""
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


def _remote_attribute_map(item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get("attributes")
    if not isinstance(raw, dict):
        return {}
    return {
        str(key).strip().casefold().replace("-", "_").replace(" ", "_"): value
        for key, value in raw.items()
        if str(key or "").strip()
    }


def _remote_value(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return value
    attributes = _remote_attribute_map(item)
    for key in keys:
        normalized = key.casefold().replace("-", "_").replace(" ", "_")
        value = attributes.get(normalized)
        if value not in (None, "", [], {}):
            return value
    return None


def _apply_generic_metadata(
    inventory_item: models.InventoryItem,
    listing: models.ChannelListing,
    item: dict[str, Any],
) -> None:
    isbn = clean_isbn(_remote_value(item, "isbn", "isbn13", "isbn_13", "isbn10", "isbn_10"))
    barcode = _remote_value(item, "barcode", "ean", "upc", "gtin", "global_unique_id")
    if isbn:
        barcode = isbn
    elif barcode not in (None, ""):
        normalized_barcode = clean_isbn(barcode)
        category_text = str(
            item.get("category") or item.get("product_type") or ""
        ).casefold()
        existing_category = inventory_item.category if inventory_item is not None else ItemCategory.GENERAL
        book_evidence = (
            existing_category == ItemCategory.BOOK
            or any(token in category_text for token in ("book", "books", "livro", "libro"))
            or bool(_remote_value(item, "author", "publisher"))
        )
        raw_barcode = re.sub(r"[^0-9Xx]", "", str(barcode))
        isbn_shaped = (
            (len(raw_barcode) == 13 and raw_barcode.startswith(("978", "979")))
            or len(raw_barcode) == 10
        )
        if book_evidence and normalized_barcode and isbn_shaped:
            isbn = normalized_barcode

    normalized = {
        "author": _remote_value(item, "author", "authors", "creator"),
        "publisher": _remote_value(item, "publisher", "publishing_house"),
        "edition": _remote_value(item, "edition", "edition_name"),
        "publication_year": _remote_value(item, "publication_year", "year"),
        "publish_date": _remote_value(item, "publish_date", "publication_date"),
        "language": _remote_value(item, "language", "lang"),
        "binding": _remote_value(item, "binding", "format", "physical_format"),
        "pages": _remote_value(item, "pages", "page_count", "number_of_pages"),
        "subtitle": _remote_value(item, "subtitle"),
        "brand": _remote_value(item, "brand", "brand_name", "vendor"),
        "size": _remote_value(item, "size"),
        "colour": _remote_value(item, "colour", "color"),
        "material": _remote_value(item, "material", "materials"),
        "condition": _remote_value(item, "condition"),
        "description": _remote_value(item, "description", "plain_description"),
        "isbn": isbn,
        "barcode": barcode,
    }

    remote: dict[str, Any] = {}
    for key in (
        "category", "condition", "brand", "size", "color", "colour", "material",
        "description", "image_url", "tags", "attributes", "product_type", "taxonomy_id",
        "author", "publisher", "edition", "publication_year", "publish_date",
        "language", "binding", "pages", "subtitle",
        "isbn", "barcode", "global_unique_id",
    ):
        value = item.get(key)
        if value not in (None, "", [], {}):
            remote[key] = value
    for key, value in normalized.items():
        if value not in (None, "", [], {}) and key not in remote:
            remote[key] = value
    if remote:
        listing.extra = {**(listing.extra or {}), "remote_metadata": remote}

    if not inventory_item.condition and normalized["condition"]:
        inventory_item.condition = str(normalized["condition"]).strip() or None

    attrs = dict(inventory_item.attributes or {})
    for key in (
        "author", "publisher", "edition", "publication_year", "publish_date",
        "language", "binding", "pages", "subtitle",
        "brand", "size", "colour", "material", "description",
        "isbn", "barcode",
    ):
        value = normalized.get(key)
        if value not in (None, "", [], {}) and key not in attrs:
            if key in {"publication_year", "pages"}:
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    value = str(value).strip()
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
    elif (
        inventory_item.category == ItemCategory.GENERAL
        and (attrs.get("isbn") or attrs.get("author"))
    ):
        inventory_item.category = ItemCategory.BOOK

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
    """Re-derive aggregate item state from all current marketplace listings."""
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
    """Deactivate active channel listings omitted from a full snapshot."""
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
    """Insert one connector audit row, idempotently for an identical timestamp."""
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


def record_workspace_channel_snapshot(
    workspace_id,
    channel: str,
    items: list[dict[str, Any]],
    *,
    synced_at: datetime,
    full_snapshot: bool,
    note: Optional[str] = None,
) -> dict[str, int]:
    """Persist one connector inventory snapshot for a workspace.

    Failures intentionally propagate so the job queue and connector status can
    report the real sync outcome.
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
