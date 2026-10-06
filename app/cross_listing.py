"""Shared cross-listing candidate and publish orchestration.

A Vinted listing can act as the source copy for title, description, price and
photos, while the physical InventoryItem remains the stock authority.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db, models, publishing, stock_intake
from app.constants import Channel, ItemCategory, ListingStatus
from app.connectors import hosted
from app.connectors.workspace_sync import record_workspace_channel_snapshot
from app.workspace_bootstrap import clean_isbn


DIRECT_CREATE_CHANNELS = {
    Channel.WOOCOMMERCE,
    Channel.SHOPIFY,
    Channel.WIX,
}


def _value(*choices: tuple[Any, str]) -> tuple[Any, str | None]:
    for raw, source in choices:
        if raw not in (None, ""):
            if isinstance(raw, str):
                raw = raw.strip()
                if not raw:
                    continue
            return raw, source
    return None, None


def existing_channel_listing(
    session: Session,
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    channel: str,
) -> models.ChannelListing | None:
    rows = session.execute(
        select(models.ChannelListing).where(
            models.ChannelListing.workspace_id == workspace_id,
            models.ChannelListing.inventory_item_id == item_id,
            models.ChannelListing.channel == channel,
        )
    ).scalars().all()
    if not rows:
        return None
    rows.sort(
        key=lambda row: (
            row.status == ListingStatus.ACTIVE,
            row.last_seen_at or row.first_seen_at,
        ),
        reverse=True,
    )
    return rows[0]


def build_candidate(
    session: Session,
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    *,
    source_listing_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    item = session.get(models.InventoryItem, item_id)
    if item is None or item.workspace_id != workspace_id:
        raise ValueError("Inventory item not found")

    source = publishing._vinted_source(
        session,
        workspace_id,
        item.id,
        source_listing_id,
    )
    if source_listing_id is not None and source is None:
        raise ValueError("The selected Vinted source listing is not linked to this item")

    attrs = dict(item.attributes or {})
    extra = dict(source.extra or {}) if source else {}
    metadata = (
        dict(extra.get("metadata") or {})
        if isinstance(extra.get("metadata"), dict)
        else {}
    )

    isbn, isbn_source = _value(
        (clean_isbn(metadata.get("isbn")), "vinted"),
        (clean_isbn(attrs.get("isbn")), "master"),
    )
    barcode, barcode_source = _value(
        (metadata.get("barcode"), "vinted"),
        (attrs.get("barcode"), "master"),
        (isbn, isbn_source or "derived"),
    )

    enrichment: dict[str, Any] | None = None
    enrichment_warning: str | None = None
    existing_bibliographic = {
        "subtitle": attrs.get("subtitle"),
        "publisher": vmeta.get("publisher") or attrs.get("publisher"),
        "edition": attrs.get("edition"),
        "publish_date": (
            attrs.get("publish_date")
            or attrs.get("publication_date")
            or attrs.get("publication_year")
        ),
        "binding": attrs.get("binding") or attrs.get("physical_format"),
        "pages": attrs.get("pages") or attrs.get("number_of_pages"),
    }
    needs_isbn_enrichment = any(
        value in (None, "", [], {})
        for value in existing_bibliographic.values()
    )
    if isbn and item.category == ItemCategory.BOOK and needs_isbn_enrichment:
        try:
            enrichment = stock_intake.lookup_isbn(str(isbn))
        except (ValueError, RuntimeError) as exc:
            enrichment_warning = str(exc)

    title, title_source = _value(
        (source.title if source else None, "vinted"),
        (attrs.get("listing_title"), "master"),
        ((enrichment or {}).get("title"), "isbn"),
        (item.title, "master"),
    )
    description, description_source = _value(
        (metadata.get("description"), "vinted"),
        (attrs.get("vinted_description"), "vinted"),
        (attrs.get("listing_description"), "master"),
        (attrs.get("description"), "master"),
        (item.notes, "master"),
    )
    price_cents, price_source = _value(
        (source.price_cents if source else None, "vinted"),
        (attrs.get("default_price_cents"), "master"),
    )
    currency, currency_source = _value(
        (source.currency if source else None, "vinted"),
        (item.currency, "master"),
        ("EUR", "default"),
    )
    condition, condition_source = _value(
        (metadata.get("condition"), "vinted"),
        (item.condition, "master"),
    )
    author, author_source = _value(
        (metadata.get("author"), "vinted"),
        (attrs.get("author"), "master"),
        ((enrichment or {}).get("author"), "isbn"),
    )
    publisher, publisher_source = _value(
        (metadata.get("publisher"), "vinted"),
        (attrs.get("publisher"), "master"),
        ((enrichment or {}).get("publisher"), "isbn"),
    )
    edition, edition_source = _value(
        (attrs.get("edition"), "master"),
        ((enrichment or {}).get("edition"), "isbn"),
    )
    publish_date, publish_date_source = _value(
        (attrs.get("publish_date"), "master"),
        (attrs.get("publication_date"), "master"),
        ((enrichment or {}).get("publish_date"), "isbn"),
    )
    publication_year, publication_year_source = _value(
        (attrs.get("publication_year"), "master"),
        ((enrichment or {}).get("publication_year"), "isbn"),
    )
    language, language_source = _value(
        (metadata.get("language"), "vinted"),
        (attrs.get("language"), "master"),
    )
    binding, binding_source = _value(
        (attrs.get("binding"), "master"),
        (attrs.get("physical_format"), "master"),
        ((enrichment or {}).get("physical_format"), "isbn"),
    )
    pages, pages_source = _value(
        (attrs.get("pages"), "master"),
        (attrs.get("number_of_pages"), "master"),
        ((enrichment or {}).get("number_of_pages"), "isbn"),
    )
    subtitle, subtitle_source = _value(
        (attrs.get("subtitle"), "master"),
        ((enrichment or {}).get("subtitle"), "isbn"),
    )
    brand, brand_source = _value(
        (metadata.get("brand"), "vinted"),
        (attrs.get("brand"), "master"),
    )
    size, size_source = _value(
        (metadata.get("size"), "vinted"),
        (attrs.get("size"), "master"),
    )
    colour, colour_source = _value(
        (metadata.get("color") or metadata.get("colour"), "vinted"),
        (attrs.get("colour") or attrs.get("color"), "master"),
    )
    material, material_source = _value(
        (metadata.get("material"), "vinted"),
        (attrs.get("material"), "master"),
    )
    tags, tags_source = _value(
        (attrs.get("tags"), "master"),
        (metadata.get("tags"), "vinted"),
    )

    fields = {
        "sku": item.sku,
        "title": title,
        "subtitle": subtitle,
        "description": description or "",
        "price_cents": int(price_cents) if price_cents not in (None, "") else None,
        "currency": str(currency or "EUR").upper(),
        "quantity": max(0, int(item.quantity or 0)),
        "category": item.category,
        "condition": condition,
        "author": author,
        "isbn": isbn,
        "barcode": barcode,
        "publisher": publisher,
        "edition": edition,
        "publish_date": publish_date,
        "publication_year": publication_year,
        "language": language,
        "binding": binding,
        "pages": pages,
        "brand": brand,
        "size": size,
        "colour": colour,
        "material": material,
        "tags": tags,
    }
    sources = {
        "sku": "master",
        "title": title_source,
        "subtitle": subtitle_source,
        "description": description_source,
        "price_cents": price_source,
        "currency": currency_source,
        "quantity": "master",
        "category": "master",
        "condition": condition_source,
        "author": author_source,
        "isbn": isbn_source,
        "barcode": barcode_source,
        "publisher": publisher_source,
        "edition": edition_source,
        "publish_date": publish_date_source,
        "publication_year": publication_year_source,
        "language": language_source,
        "binding": binding_source,
        "pages": pages_source,
        "brand": brand_source,
        "size": size_source,
        "colour": colour_source,
        "material": material_source,
        "tags": tags_source,
    }
    missing: list[str] = []
    if not fields["title"]:
        missing.append("title")
    if fields["price_cents"] is None or int(fields["price_cents"]) <= 0:
        missing.append("price")
    if fields["quantity"] <= 0:
        missing.append("available stock")

    images = publishing._source_image_urls(item, source)
    return {
        "item_id": str(item.id),
        "item_title": item.title,
        "fields": fields,
        "field_sources": sources,
        "missing": missing,
        "ready": not missing,
        "enrichment_warning": enrichment_warning,
        "source": {
            "channel": Channel.VINTED if source else "master",
            "listing_id": str(source.id) if source else None,
            "external_id": source.external_id if source else None,
            "url": source.url if source else None,
            "image_urls": images,
            "photo_count": len(images),
        },
    }

def apply_overrides(candidate: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    fields = dict(candidate.get("fields") or {})
    sources = dict(candidate.get("field_sources") or {})
    for key in ("title", "description"):
        if overrides.get(key) is not None:
            fields[key] = str(overrides[key]).strip()
            sources[key] = "review"
    if overrides.get("price_cents") is not None:
        fields["price_cents"] = int(overrides["price_cents"])
        sources["price_cents"] = "review"

    missing: list[str] = []
    if not fields.get("title"):
        missing.append("title")
    if fields.get("price_cents") is None or int(fields["price_cents"]) <= 0:
        missing.append("price")
    if int(fields.get("quantity") or 0) <= 0:
        missing.append("available stock")
    return {
        **candidate,
        "fields": fields,
        "field_sources": sources,
        "missing": missing,
        "ready": not missing,
    }


def _adapter(channel: str):
    return {
        Channel.WOOCOMMERCE: hosted.create_woocommerce_workspace_listing,
        Channel.SHOPIFY: hosted.create_shopify_workspace_listing,
        Channel.WIX: hosted.create_wix_workspace_listing,
    }.get(channel)


def publish(
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    channel: str,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    if channel not in DIRECT_CREATE_CHANNELS:
        raise ValueError(f"{channel} direct publishing is not implemented")
    if candidate.get("missing"):
        raise ValueError(
            "Listing is missing: " + ", ".join(str(value) for value in candidate["missing"])
        )

    with db.session_scope() as session:
        existing = existing_channel_listing(session, workspace_id, item_id, channel)
        if existing is not None:
            raise ValueError(
                f"This physical item already has a {channel} listing. "
                "Use the existing listing instead of creating a duplicate."
            )

    adapter = _adapter(channel)
    if adapter is None:
        raise ValueError(f"{channel} direct publishing is not implemented")
    remote = adapter(workspace_id, candidate)
    external_id = str(remote.get("source_id") or "").strip()
    if not external_id:
        raise RuntimeError(f"{channel} did not return a remote listing ID")

    now = datetime.now(timezone.utc)
    record_workspace_channel_snapshot(
        workspace_id,
        channel,
        [remote],
        synced_at=now,
        full_snapshot=False,
        note=f"Cross-listed from {candidate.get('source', {}).get('channel') or 'master'}",
    )

    with db.session_scope() as session:
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.channel == channel,
                models.ChannelListing.external_id == external_id,
            )
        ).scalar_one_or_none()
        item = session.get(models.InventoryItem, item_id)
        if listing is None or item is None:
            raise RuntimeError("Remote listing was created but local linkage could not be recorded")
        listing.inventory_item_id = item.id
        listing.extra = {
            **dict(listing.extra or {}),
            "cross_listed_at": now.isoformat(),
            "source_channel": (candidate.get("source") or {}).get("channel"),
            "source_listing_id": (candidate.get("source") or {}).get("listing_id"),
            "source_listing_external_id": (candidate.get("source") or {}).get("external_id"),
            "source_image_urls": list((candidate.get("source") or {}).get("image_urls") or [])[:5],
        }
        listing_id = str(listing.id)

    return {
        "ok": True,
        "channel": channel,
        "listing_id": listing_id,
        "external_id": external_id,
        "url": remote.get("url"),
        "title": remote.get("title"),
    }
