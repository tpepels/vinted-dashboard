"""Workspace-scoped ingestion for the paired Chrome bridge.

Every incoming row is written directly to the ORM schema under the paired
credential's workspace. The workspace database is the only runtime source of
truth for Vinted inventory, history and sales.
"""

from __future__ import annotations

import re
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app import db, models
from app.connectors.workspace_sync import recompute_inventory_item
from app.cross_channel import auto_link_unlinked_sales, reconcile_sale_state
from app.constants import (
    Channel,
    ChannelAccountStatus,
    ItemCategory,
    ItemStatus,
    ListingStatus,
    SyncRunStatus,
)
from app.workspace_bootstrap import (
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


def _exact_vinted_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            number = float(value)
            if number > 1e11:
                number /= 1000
            parsed = datetime.fromtimestamp(number, tz=timezone.utc)
        else:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            parsed = parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return parsed.isoformat()


def _int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _relative_vinted_age_seconds(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        seconds = int(float(value))
    except (TypeError, ValueError):
        return None
    if seconds < 0 or seconds > int(365.25 * 86400 * 100):
        return None
    return seconds


def _version_at_least(value: str | None, minimum: tuple[int, ...]) -> bool:
    if not value:
        return False
    try:
        parts = tuple(int(part) for part in str(value).split("."))
    except ValueError:
        return False
    padded = parts + (0,) * max(0, len(minimum) - len(parts))
    target = minimum + (0,) * max(0, len(parts) - len(minimum))
    return padded >= target


_VINTED_METADATA_KEYS = {
    "condition",
    "category",
    "brand",
    "size",
    "color",
    "material",
    "description",
    "isbn",
    "author",
    "publisher",
    "language",
}


def _clean_vinted_metadata(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key in _VINTED_METADATA_KEYS:
        raw = value.get(key)
        if raw in (None, ""):
            continue
        text = str(raw).strip()
        if not text:
            continue
        cleaned[key] = text[:5000 if key == "description" else 500]
    return cleaned


_VINTED_CATEGORY_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        ItemCategory.BOOK,
        (
            "book", "books", "livro", "livros", "livre", "livres",
            "libro", "libros", "buch", "bücher", "ksiaz", "książ",
            "fiction", "ficção", "ficcao", "non-fiction", "nonfiction",
            "literature", "literatura", "novel", "novels", "romance",
            "crime", "thriller", "fantasy", "biography", "biografia",
            "memoir", "poetry", "poesia", "textbook", "comic", "comics",
            "manga", "banda desenhada",
        ),
    ),
    (
        ItemCategory.ELECTRONICS,
        (
            "electronics", "electronic", "eletrónica", "eletronica",
            "électronique", "electronique", "elettronica", "elektronik",
            "phone", "phones", "smartphone", "tablet", "computer",
            "computing", "audio", "headphone", "camera", "gaming console",
        ),
    ),
    (
        ItemCategory.CLOTHING,
        (
            "clothing", "clothes", "roupa", "vestuário", "vestuario",
            "vêtement", "vetement", "abbigliamento", "kleidung",
            "dress", "dresses", "shirt", "shirts", "trouser", "trousers",
            "jeans", "jacket", "jackets", "coat", "coats", "skirt", "skirts",
            "shoe", "shoes", "calçado", "calcado", "chaussure", "scarpe",
            "accessories", "acessórios", "acessorios", "fashion",
        ),
    ),
    (
        ItemCategory.TOYS_GAMES,
        (
            "toy", "toys", "game", "games", "brinquedo", "brinquedos",
            "jogo", "jogos", "jouet", "jouets", "jeu", "jeux",
            "giocattoli", "spielzeug", "board game", "puzzle",
        ),
    ),
    (
        ItemCategory.SPORTS,
        (
            "sport", "sports", "desporto", "desportos", "sporting",
            "fitness", "cycling", "ciclismo", "running", "football",
        ),
    ),
    (
        ItemCategory.BEAUTY,
        (
            "beauty", "beleza", "beauté", "beaute", "bellezza",
            "kosmetik", "cosmetic", "cosmetics", "skincare", "make-up",
            "makeup", "perfume", "fragrance",
        ),
    ),
    (
        ItemCategory.ART_CRAFTS,
        (
            "art", "arts", "craft", "crafts", "arte", "artes", "artesanato",
            "artisanat", "hobby", "hobbies", "sewing", "knitting",
        ),
    ),
    (
        ItemCategory.MEDIA,
        (
            "music", "música", "musica", "film", "films", "movie", "movies",
            "dvd", "blu-ray", "bluray", "vinyl", "record", "records",
            "cds", "video game", "video games",
        ),
    ),
    (
        ItemCategory.COLLECTIBLES,
        (
            "collectible", "collectibles", "collectable", "collectables",
            "colecionável", "colecionaveis", "collection", "memorabilia",
            "antique", "antiques", "vintage collectible",
        ),
    ),
    (
        ItemCategory.HOME,
        (
            "home", "casa", "maison", "casa e jardim", "homeware",
            "furniture", "móvel", "moveis", "móveis", "decoration", "decor",
            "kitchen", "cozinha", "garden", "jardim", "household",
        ),
    ),
)


def _normalize_vinted_category(value: str | None) -> tuple[str, list[str]]:
    raw = unicodedata.normalize("NFKD", str(value or "").casefold())
    raw = "".join(char for char in raw if not unicodedata.combining(char))
    segments = [
        " ".join(re.sub(r"[^a-z0-9]+", " ", part).split())
        for part in re.split(r"[>\\/|›»]+", raw)
    ]
    segments = [part for part in segments if part]
    return " ".join(segments), segments


def _vinted_category_token_matches(
    text: str,
    segments: list[str],
    token: str,
) -> bool:
    token_text, _ = _normalize_vinted_category(token)
    if not token_text:
        return False
    # "art"/"arts" are too ambiguous to match as arbitrary path words
    # (for example "martial arts"). Accept them only as a category segment.
    if token_text in {"art", "arts"}:
        return token_text in segments
    return re.search(
        rf"(?<![a-z0-9]){re.escape(token_text)}(?![a-z0-9])",
        text,
    ) is not None


def classify_vinted_category(value: str | None) -> str | None:
    text, segments = _normalize_vinted_category(value)
    if not text:
        return None

    best: tuple[int, int, str] | None = None
    for category, tokens in _VINTED_CATEGORY_TOKENS:
        for token in tokens:
            token_text, _ = _normalize_vinted_category(token)
            if not _vinted_category_token_matches(text, segments, token):
                continue
            # Prefer the most specific matching phrase across all broad
            # categories. This makes "video games" beat the generic "games".
            score = (len(token_text.split()), len(token_text), category)
            if best is None or score[:2] > best[:2]:
                best = score
    return best[2] if best is not None else None


def _looks_like_book_category(value: str | None) -> bool:
    return classify_vinted_category(value) == ItemCategory.BOOK


def _apply_vinted_metadata(
    item: models.InventoryItem,
    metadata: dict[str, str],
    captured_at: datetime,
) -> None:
    if not metadata:
        return
    if not item.condition and metadata.get("condition"):
        item.condition = metadata["condition"]
    if item.category == ItemCategory.GENERAL:
        inferred_category = classify_vinted_category(metadata.get("category"))
        if inferred_category:
            item.category = inferred_category
        elif metadata.get("isbn") or metadata.get("author"):
            item.category = ItemCategory.BOOK

    attributes = dict(item.attributes or {})
    generic_keys = ("brand", "size", "color", "material", "description", "isbn", "author", "publisher", "language")
    for key in generic_keys:
        if metadata.get(key) and not attributes.get(key):
            attributes[key] = metadata[key]
    if metadata.get("category"):
        attributes["vinted_category"] = metadata["category"]
    if metadata.get("description"):
        attributes["vinted_description"] = metadata["description"]
    attributes["vinted_metadata_synced_at"] = captured_at.isoformat()
    item.attributes = attributes


def _listing_item(
    session,
    workspace,
    account,
    row: dict[str, Any],
    captured_at: datetime,
    extension_version: str | None = None,
):
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
    incoming_listed_at = _exact_vinted_iso(row.get("listed_at"))
    existing_listed_at = _exact_vinted_iso(existing_extra.get("listed_at"))
    page_age_bridge = _version_at_least(extension_version, (2, 7, 0))
    listed_at = (
        incoming_listed_at
        if page_age_bridge
        else (incoming_listed_at or existing_listed_at)
    )
    listed_at_source = (
        str(row.get("listed_at_source") or "vinted")
        if incoming_listed_at
        else (
            str(existing_extra.get("listed_at_source") or "vinted")
            if existing_listed_at and not page_age_bridge
            else None
        )
    )
    incoming_age_seconds = _relative_vinted_age_seconds(row.get("listed_age_seconds"))
    incoming_age_source = str(row.get("listed_age_source") or "").strip() or None
    if page_age_bridge and not (
        incoming_age_source
        and incoming_age_source.startswith("vinted_page")
    ):
        incoming_age_seconds = None
        incoming_age_source = None
    existing_age_seconds = _relative_vinted_age_seconds(existing_extra.get("listed_age_seconds"))
    existing_age_source = str(existing_extra.get("listed_age_source") or "").strip() or None
    keep_existing_age = (
        not page_age_bridge
        or bool(existing_age_source and existing_age_source.startswith("vinted_page"))
    )
    listed_age_seconds = (
        incoming_age_seconds
        if incoming_age_seconds is not None
        else (existing_age_seconds if keep_existing_age else None)
    )
    listed_age_source = (
        incoming_age_source or "vinted_relative"
        if incoming_age_seconds is not None
        else (existing_age_source if keep_existing_age and existing_age_seconds is not None else None)
    )
    listed_age_text = (
        str(row.get("listed_age_text") or "").strip()[:200]
        if incoming_age_seconds is not None
        else (
            str(existing_extra.get("listed_age_text") or "").strip()[:200] or None
            if keep_existing_age and existing_age_seconds is not None
            else None
        )
    )
    listed_age_observed_at = (
        captured_at.isoformat()
        if incoming_age_seconds is not None
        else (
            existing_extra.get("listed_age_observed_at")
            if keep_existing_age and existing_age_seconds is not None
            else None
        )
    )
    incoming_metadata = _clean_vinted_metadata(row.get("metadata"))
    existing_metadata = _clean_vinted_metadata(existing_extra.get("metadata"))
    metadata = {**existing_metadata, **incoming_metadata}
    image_urls = [
        str(value).strip()
        for value in (row.get("image_urls") or [])
        if str(value or "").strip()
    ][:20]
    if not image_urls and row.get("image_url"):
        image_urls = [str(row.get("image_url")).strip()]
    if not image_urls:
        image_urls = [
            str(value).strip()
            for value in (existing_extra.get("image_urls") or [])
            if str(value or "").strip()
        ][:20]
    listing.extra = {
        **existing_extra,
        "listed_at": listed_at,
        "listed_at_source": listed_at_source,
        "listed_age_seconds": listed_age_seconds,
        "listed_age_source": listed_age_source,
        "listed_age_text": listed_age_text,
        "listed_age_observed_at": listed_age_observed_at,
        "metadata": metadata,
        "image_url": image_urls[0] if image_urls else existing_extra.get("image_url"),
        "image_urls": image_urls,
    }
    _apply_vinted_metadata(item, metadata, captured_at)
    if image_urls:
        attrs = dict(item.attributes or {})
        if not attrs.get("image_urls"):
            attrs["image_urls"] = image_urls
            attrs["image_source"] = "vinted"
        if not attrs.get("image_url"):
            attrs["image_url"] = image_urls[0]
        item.attributes = attrs
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
                    "listed_at": incoming_listed_at,
                    "listed_at_source": listed_at_source,
                    "listed_age_seconds": incoming_age_seconds,
                    "listed_age_source": listed_age_source,
                    "listed_age_text": listed_age_text,
                    "url": row.get("vinted_url") or row.get("url"),
                    "metadata": metadata,
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
            listing = _listing_item(
                session,
                workspace,
                account,
                row,
                captured_at,
                extension_version=extension_version,
            )
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

        auto_link_result = auto_link_unlinked_sales(session, workspace.id)

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
        "sales_auto_linked": auto_link_result["linked"],
        "sales_remaining_unlinked": auto_link_result["remaining"],
        "cross_channel_actions_created": auto_link_result["actions_created"],
    }
