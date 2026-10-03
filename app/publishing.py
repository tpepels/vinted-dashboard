"""Cross-listing helpers for publishing master inventory to BIBLIO.

Vinted is treated as a source listing when present.  The BIBLIO listing remains
linked to the same physical InventoryItem so sales/reconciliation can close the
other channel later.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, stock_intake
from app.constants import Channel, ItemCategory, ListingStatus
from app.workspace_bootstrap import clean_isbn, get_or_create_channel_account


def _value(*choices: tuple[Any, str]) -> tuple[Any, str | None]:
    for raw, source in choices:
        if raw not in (None, ""):
            if isinstance(raw, str):
                raw = raw.strip()
                if not raw:
                    continue
            return raw, source
    return None, None


def _vinted_source(
    session: Session,
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    source_listing_id: uuid.UUID | None = None,
) -> models.ChannelListing | None:
    query = select(models.ChannelListing).where(
        models.ChannelListing.workspace_id == workspace_id,
        models.ChannelListing.inventory_item_id == item_id,
        models.ChannelListing.channel == Channel.VINTED,
    )
    if source_listing_id is not None:
        query = query.where(models.ChannelListing.id == source_listing_id)
        return session.execute(query).scalar_one_or_none()

    rows = session.execute(query).scalars().all()
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


def _bookish(item: models.InventoryItem, metadata: dict[str, Any]) -> bool:
    if item.category == ItemCategory.BOOK:
        return True
    attrs = dict(item.attributes or {})
    if attrs.get("isbn") or attrs.get("author") or metadata.get("isbn") or metadata.get("author"):
        return True
    category = str(metadata.get("category") or attrs.get("vinted_category") or "").casefold()
    return any(token in category for token in ("book", "livro", "livre", "libro", "buch", "książ"))


def _existing_biblio(
    session: Session,
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
) -> models.ChannelListing | None:
    return session.execute(
        select(models.ChannelListing).where(
            models.ChannelListing.workspace_id == workspace_id,
            models.ChannelListing.inventory_item_id == item_id,
            models.ChannelListing.channel == Channel.BIBLIO,
        )
    ).scalar_one_or_none()


def build_biblio_candidate(
    session: Session,
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    *,
    source_listing_id: uuid.UUID | None = None,
    enrich_isbn: bool = True,
) -> dict[str, Any]:
    item = session.get(models.InventoryItem, item_id)
    if item is None or item.workspace_id != workspace_id:
        raise ValueError("Inventory item not found")

    vinted = _vinted_source(session, workspace_id, item.id, source_listing_id)
    if source_listing_id is not None and vinted is None:
        raise ValueError("The selected Vinted source listing is not linked to this item")

    attrs = dict(item.attributes or {})
    vextra = dict(vinted.extra or {}) if vinted else {}
    vmeta = dict(vextra.get("metadata") or {}) if isinstance(vextra.get("metadata"), dict) else {}
    if not _bookish(item, vmeta):
        raise ValueError("Only book inventory can be published to BIBLIO")

    title, title_source = _value(
        (vinted.title if vinted else None, "vinted"),
        (attrs.get("listing_title"), "master"),
        (item.title, "master"),
    )
    author, author_source = _value(
        (vmeta.get("author"), "vinted"),
        (attrs.get("author"), "master"),
    )
    description, description_source = _value(
        (vmeta.get("description"), "vinted"),
        (attrs.get("vinted_description"), "vinted"),
        (attrs.get("listing_description"), "master"),
        (attrs.get("description"), "master"),
        (item.notes, "master"),
    )
    isbn, isbn_source = _value(
        (clean_isbn(vmeta.get("isbn")), "vinted"),
        (clean_isbn(attrs.get("isbn")), "master"),
    )
    price_cents, price_source = _value(
        (vinted.price_cents if vinted else None, "vinted"),
        (attrs.get("default_price_cents"), "master"),
    )
    currency, currency_source = _value(
        (vinted.currency if vinted else None, "vinted"),
        (item.currency, "master"),
        ("EUR", "default"),
    )

    enrichment: dict[str, Any] | None = None
    enrichment_warning: str | None = None
    if enrich_isbn and isbn and (not author):
        try:
            enrichment = stock_intake.lookup_isbn(str(isbn))
        except (ValueError, RuntimeError) as exc:
            enrichment_warning = str(exc)
        if enrichment:
            if not author and enrichment.get("author"):
                author = str(enrichment["author"]).strip()
                author_source = "isbn"
            if enrichment.get("publisher") and not attrs.get("publisher"):
                attrs["publisher"] = enrichment["publisher"]
                attrs["publisher_source"] = "isbn"
            if enrichment.get("edition") and not attrs.get("edition"):
                attrs["edition"] = enrichment["edition"]
                attrs["edition_source"] = "isbn"

    fields = {
        "sku": item.sku,
        "title": title,
        "author": author,
        "description": description,
        "isbn": isbn,
        "price_cents": int(price_cents) if price_cents not in (None, "") else None,
        "currency": str(currency or "EUR").upper(),
        "quantity": max(0, int(item.quantity or 0)),
    }
    sources = {
        "sku": "master",
        "title": title_source,
        "author": author_source,
        "description": description_source,
        "isbn": isbn_source,
        "price_cents": price_source,
        "currency": currency_source,
        "quantity": "master",
    }

    missing: list[str] = []
    for key, label in (
        ("sku", "SKU"),
        ("author", "author"),
        ("title", "title"),
        ("description", "description"),
        ("price_cents", "price"),
    ):
        if fields.get(key) in (None, ""):
            missing.append(label)
    if fields["quantity"] <= 0:
        missing.append("available stock")

    existing = _existing_biblio(session, workspace_id, item.id)
    conflict = session.execute(
        select(models.ChannelListing).where(
            models.ChannelListing.workspace_id == workspace_id,
            models.ChannelListing.channel == Channel.BIBLIO,
            models.ChannelListing.external_id == item.sku,
            models.ChannelListing.inventory_item_id != item.id,
        )
    ).scalar_one_or_none()
    if conflict is not None:
        missing.append("unique BIBLIO Book ID")

    return {
        "item_id": str(item.id),
        "item_title": item.title,
        "category": item.category,
        "fields": fields,
        "field_sources": sources,
        "missing": missing,
        "ready": not missing,
        "existing_biblio_listing_id": str(existing.id) if existing else None,
        "already_listed": bool(existing and existing.status == ListingStatus.ACTIVE),
        "source": {
            "channel": Channel.VINTED if vinted else "master",
            "listing_id": str(vinted.id) if vinted else None,
            "external_id": vinted.external_id if vinted else None,
            "url": vinted.url if vinted else None,
        },
        "enrichment_warning": enrichment_warning,
    }


def apply_biblio_overrides(
    candidate: dict[str, Any],
    overrides: dict[str, Any],
) -> dict[str, Any]:
    fields = dict(candidate.get("fields") or {})
    sources = dict(candidate.get("field_sources") or {})
    for key in ("title", "author", "description", "isbn"):
        if key not in overrides or overrides[key] is None:
            continue
        value = str(overrides[key]).strip()
        if value:
            fields[key] = clean_isbn(value) if key == "isbn" else value
            sources[key] = "review"
    if overrides.get("price_cents") is not None:
        price = int(overrides["price_cents"])
        if price < 0:
            raise ValueError("BIBLIO price cannot be negative")
        fields["price_cents"] = price
        sources["price_cents"] = "review"

    missing: list[str] = []
    for key, label in (
        ("sku", "SKU"),
        ("author", "author"),
        ("title", "title"),
        ("description", "description"),
        ("price_cents", "price"),
    ):
        if fields.get(key) in (None, ""):
            missing.append(label)
    if int(fields.get("quantity") or 0) <= 0:
        missing.append("available stock")

    candidate = {
        **candidate,
        "fields": fields,
        "field_sources": sources,
        "missing": missing,
        "ready": not missing,
    }
    return candidate


def upsert_biblio_listing(
    session: Session,
    workspace: models.Workspace,
    item_id: uuid.UUID,
    candidate: dict[str, Any],
) -> models.ChannelListing:
    if candidate.get("missing"):
        raise ValueError(
            "BIBLIO listing is missing: " + ", ".join(str(value) for value in candidate["missing"])
        )
    item = session.get(models.InventoryItem, item_id)
    if item is None or item.workspace_id != workspace.id:
        raise ValueError("Inventory item not found")

    existing = _existing_biblio(session, workspace.id, item.id)
    external_id = existing.external_id if existing else item.sku
    collision = session.execute(
        select(models.ChannelListing).where(
            models.ChannelListing.workspace_id == workspace.id,
            models.ChannelListing.channel == Channel.BIBLIO,
            models.ChannelListing.external_id == external_id,
            models.ChannelListing.inventory_item_id != item.id,
        )
    ).scalar_one_or_none()
    if collision is not None:
        raise ValueError("BIBLIO Book ID is already linked to another physical item")

    account, _ = get_or_create_channel_account(session, workspace, Channel.BIBLIO, {})
    now = datetime.now(timezone.utc)
    fields = dict(candidate["fields"])
    source = dict(candidate.get("source") or {})

    if existing is None:
        existing = models.ChannelListing(
            workspace_id=workspace.id,
            inventory_item_id=item.id,
            channel_account_id=account.id,
            channel=Channel.BIBLIO,
            external_id=external_id,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(existing)

    existing.inventory_item_id = item.id
    existing.channel_account_id = account.id
    existing.external_sku = item.sku
    existing.title = str(fields["title"])
    existing.price_cents = int(fields["price_cents"])
    existing.currency = str(fields.get("currency") or item.currency or "EUR")
    existing.status = ListingStatus.ACTIVE
    existing.quantity = max(1, int(fields.get("quantity") or 1))
    existing.last_seen_at = now
    existing.extra = {
        **dict(existing.extra or {}),
        "author": fields.get("author"),
        "description": fields.get("description"),
        "isbn": fields.get("isbn"),
        "source_channel": source.get("channel"),
        "source_listing_id": source.get("listing_id"),
        "source_listing_external_id": source.get("external_id"),
        "field_sources": dict(candidate.get("field_sources") or {}),
        "cross_listed_at": now.isoformat(),
    }

    if item.category == ItemCategory.GENERAL and (fields.get("isbn") or fields.get("author")):
        item.category = ItemCategory.BOOK

    attrs = dict(item.attributes or {})
    for key in ("author", "isbn"):
        if fields.get(key) and not attrs.get(key):
            attrs[key] = fields[key]
    if fields.get("description") and not attrs.get("description"):
        attrs["description"] = fields["description"]
    if fields.get("price_cents") is not None and attrs.get("default_price_cents") is None:
        attrs["default_price_cents"] = int(fields["price_cents"])
    item.attributes = attrs
    session.flush()
    return existing
