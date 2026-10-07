"""Cross-listing helpers for publishing master inventory to BIBLIO.

Vinted is treated as a source listing when present.  The BIBLIO listing remains
linked to the same physical InventoryItem so sales/reconciliation can close the
other channel later.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, stock_intake
from app.constants import Channel, ItemCategory, ListingStatus
from app.workspace_bootstrap import clean_isbn, get_or_create_channel_account


def _isbn_from_book_barcode(value: Any) -> str | None:
    raw = re.sub(r"[^0-9Xx]", "", str(value or ""))
    if not (
        (len(raw) == 13 and raw.startswith(("978", "979")))
        or len(raw) == 10
    ):
        return None
    return clean_isbn(raw)


def _value(*choices: tuple[Any, str]) -> tuple[Any, str | None]:
    for raw, source in choices:
        if raw not in (None, ""):
            if isinstance(raw, str):
                raw = raw.strip()
                if not raw:
                    continue
            return raw, source
    return None, None


def _optional_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in {"yes", "y", "true", "1", "present"}:
        return True
    if text in {"no", "n", "false", "0", "absent"}:
        return False
    raise ValueError(f"Invalid BIBLIO boolean value: {value}")


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


def is_biblio_book_candidate(
    item: models.InventoryItem,
    source_listing: models.ChannelListing | None = None,
) -> bool:
    """Return whether a master item has enough book evidence for BIBLIO.

    The master category is not authoritative for older Vinted imports: many
    historical books were created as GENERAL before rich Vinted metadata was
    available. Source-listing ISBN/author/category evidence therefore counts
    too, matching the actual publish preflight.
    """
    if item.category == ItemCategory.BOOK:
        return True
    attrs = dict(item.attributes or {})
    metadata: dict[str, Any] = {}
    if source_listing is not None:
        extra = dict(source_listing.extra or {})
        raw = extra.get("metadata")
        if isinstance(raw, dict):
            metadata = raw
    if attrs.get("isbn") or attrs.get("author") or metadata.get("isbn") or metadata.get("author"):
        return True
    category = str(metadata.get("category") or attrs.get("vinted_category") or "").casefold()
    return any(
        token in category
        for token in (
            "book", "books", "livro", "livros", "livre", "livres",
            "libro", "libros", "buch", "bücher", "ksiaz", "książ",
            "fiction", "ficção", "ficcao", "non-fiction", "nonfiction",
            "literature", "literatura", "novel", "novels", "romance",
            "crime", "thriller", "fantasy", "biography", "biografia",
            "memoir", "poetry", "poesia", "textbook", "comic", "comics",
            "manga", "banda desenhada",
        )
    )


def _existing_biblio(
    session: Session,
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
) -> models.ChannelListing | None:
    """Return the best existing BIBLIO row for a physical item.

    Older/reconciled workspaces can contain more than one historical BIBLIO
    row linked to the same master item. That is a data-cleanup issue, but it
    must not turn a publish preflight into a 500 via scalar_one_or_none().
    Prefer an active row, then the most recently observed row.
    """
    rows = session.execute(
        select(models.ChannelListing).where(
            models.ChannelListing.workspace_id == workspace_id,
            models.ChannelListing.inventory_item_id == item_id,
            models.ChannelListing.channel == Channel.BIBLIO,
        )
    ).scalars().all()
    if not rows:
        return None
    rows.sort(
        key=lambda row: (
            row.status == ListingStatus.ACTIVE,
            row.last_seen_at or row.first_seen_at,
            row.first_seen_at,
        ),
        reverse=True,
    )
    return rows[0]


def _source_image_urls(
    item: models.InventoryItem,
    source_listing: models.ChannelListing | None,
    *,
    include_master_fallbacks: bool = True,
) -> list[str]:
    urls: list[str] = []
    if source_listing is not None:
        extra = dict(source_listing.extra or {})
        raw_urls = extra.get("image_urls")
        if isinstance(raw_urls, list):
            urls.extend(
                str(value).strip()
                for value in raw_urls
                if str(value or "").strip()
            )
        elif extra.get("image_url"):
            urls.append(str(extra["image_url"]).strip())
    if include_master_fallbacks and not urls:
        attrs = dict(item.attributes or {})
        raw_urls = attrs.get("image_urls")
        if isinstance(raw_urls, list):
            urls.extend(
                str(value).strip()
                for value in raw_urls
                if str(value or "").strip()
            )
        for key in ("image_url", "cover_url", "cover_image_url"):
            value = str(attrs.get(key) or "").strip()
            if value:
                urls.append(value)
    return list(dict.fromkeys(urls))[:12]


def _book_id_conflict(
    session: Session,
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    book_id: str | None,
) -> models.ChannelListing | None:
    value = str(book_id or "").strip()
    if not value:
        return None
    return session.execute(
        select(models.ChannelListing).where(
            models.ChannelListing.workspace_id == workspace_id,
            models.ChannelListing.channel == Channel.BIBLIO,
            models.ChannelListing.external_id == value,
            models.ChannelListing.inventory_item_id != item_id,
        )
    ).scalar_one_or_none()


def _biblio_photo_book_id_warning(book_id: str | None, photo_count: int) -> str | None:
    if int(photo_count or 0) <= 0:
        return None
    value = str(book_id or "").strip()
    if not value:
        return None
    if any(char in value for char in ("/", "\\", "'", "\x00")):
        return (
            "This Book ID cannot be used as a BIBLIO photo filename. "
            "Use a Book ID without slashes, backslashes or apostrophes if you want photos uploaded."
        )
    return None


def validate_biblio_candidate(
    session: Session,
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    fields = dict(candidate.get("fields") or {})
    missing: list[str] = []
    for key, label in (
        ("book_id", "Book ID"),
        ("author", "author"),
        ("title", "title"),
        ("description", "description"),
        ("price_cents", "price"),
    ):
        if fields.get(key) in (None, ""):
            missing.append(label)
    if int(fields.get("quantity") or 0) <= 0:
        missing.append("available stock")

    conflict = _book_id_conflict(
        session,
        workspace_id,
        item_id,
        fields.get("book_id"),
    )
    suggestion = candidate.get("book_id_suggestion")
    if conflict is not None:
        base = str(fields.get("book_id") or candidate.get("book_id_suggestion") or "BOOK").strip() or "BOOK"
        suggestion = None
        for number in range(2, 1000):
            proposed = f"{base}-{number}"
            if _book_id_conflict(session, workspace_id, item_id, proposed) is None:
                suggestion = proposed
                break
        fields["book_id"] = None
        if "unique BIBLIO Book ID" not in missing:
            missing.append("unique BIBLIO Book ID")

    photo_warning = _biblio_photo_book_id_warning(
        fields.get("book_id"),
        int(dict(candidate.get("source") or {}).get("photo_count") or 0),
    )
    return {
        **candidate,
        "fields": fields,
        "book_id_suggestion": suggestion,
        "missing": missing,
        "ready": not missing,
        "photo_warning": photo_warning,
    }


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
    existing = _existing_biblio(session, workspace_id, item.id)
    existing_extra = dict(existing.extra or {}) if existing is not None else {}
    existing_field_sources = (
        dict(existing_extra.get("field_sources") or {})
        if isinstance(existing_extra.get("field_sources"), dict)
        else {}
    )
    existing_bibliographic = (
        dict(existing_extra.get("bibliographic_enrichment") or {})
        if isinstance(existing_extra.get("bibliographic_enrichment"), dict)
        else {}
    )
    existing_bibliographic_sources = (
        dict(existing_extra.get("bibliographic_sources") or {})
        if isinstance(existing_extra.get("bibliographic_sources"), dict)
        else {}
    )

    def reviewed_bibliographic(
        key: str,
        *choices: tuple[Any, str],
    ) -> tuple[Any, str | None]:
        if existing_bibliographic_sources.get(key) == "review":
            return existing_bibliographic.get(key), "review"
        return _value(*choices)

    if not is_biblio_book_candidate(item, vinted):
        raise ValueError("Only book inventory can be published to BIBLIO")

    if existing_field_sources.get("isbn") == "review":
        reviewed_isbn = existing_extra.get("isbn")
        isbn = clean_isbn(reviewed_isbn) if reviewed_isbn not in (None, "") else None
        isbn_source = "review"
    else:
        isbn, isbn_source = _value(
            (clean_isbn(vmeta.get("isbn")), "vinted"),
            (clean_isbn(attrs.get("isbn")), "master"),
            (_isbn_from_book_barcode(vmeta.get("barcode")), "vinted_barcode"),
            (_isbn_from_book_barcode(attrs.get("barcode")), "master_barcode"),
        )

    enrichment: dict[str, Any] | None = None
    enrichment_warning: str | None = None
    if enrich_isbn and isbn:
        try:
            enrichment = stock_intake.lookup_isbn(str(isbn))
        except (ValueError, RuntimeError) as exc:
            enrichment_warning = str(exc)

    isbn_title = None
    if enrichment and enrichment.get("title"):
        isbn_title = str(enrichment["title"]).strip()
        subtitle = str(enrichment.get("subtitle") or "").strip()
        if subtitle and subtitle.casefold() not in isbn_title.casefold():
            isbn_title = f"{isbn_title}: {subtitle}"

    if existing_field_sources.get("title") == "review" and existing is not None:
        title, title_source = existing.title, "review"
    else:
        title, title_source = _value(
            (isbn_title, "isbn"),
            (attrs.get("listing_title"), "master"),
            (vinted.title if vinted else None, "vinted"),
            (item.title, "master"),
        )
    if existing_field_sources.get("author") == "review":
        author, author_source = existing_extra.get("author"), "review"
    else:
        author, author_source = _value(
            (
                str(enrichment.get("author") or "").strip() if enrichment else None,
                "isbn",
            ),
            (vmeta.get("author"), "vinted"),
            (attrs.get("author"), "master"),
        )
    if existing_field_sources.get("description") == "review":
        description, description_source = existing_extra.get("description"), "review"
    else:
        description, description_source = _value(
            (vmeta.get("description"), "vinted"),
            (attrs.get("vinted_description"), "vinted"),
            (attrs.get("listing_description"), "master"),
            (attrs.get("description"), "master"),
            (item.notes, "master"),
        )
    if existing_field_sources.get("price_cents") == "review" and existing is not None:
        price_cents, price_source = existing.price_cents, "review"
    else:
        price_cents, price_source = _value(
            (vinted.price_cents if vinted else None, "vinted"),
            (attrs.get("default_price_cents"), "master"),
        )
    currency, currency_source = _value(
        (vinted.currency if vinted else None, "vinted"),
        (item.currency, "master"),
        ("EUR", "default"),
    )

    publisher, publisher_source = reviewed_bibliographic(
        "publisher",
        (vmeta.get("publisher"), "vinted"),
        (attrs.get("publisher"), "master"),
        (
            str(enrichment.get("publisher") or "").strip() if enrichment else None,
            "isbn",
        ),
    )
    edition, edition_source = reviewed_bibliographic(
        "edition",
        (attrs.get("edition"), "master"),
        (
            str(enrichment.get("edition") or "").strip() if enrichment else None,
            "isbn",
        ),
    )
    publish_date, publish_date_source = reviewed_bibliographic(
        "publish_date",
        (attrs.get("publish_date"), "master"),
        (attrs.get("publication_date"), "master"),
        (attrs.get("publication_year"), "master"),
        (
            str(enrichment.get("publish_date") or "").strip() if enrichment else None,
            "isbn",
        ),
    )
    subtitle, subtitle_source = reviewed_bibliographic(
        "subtitle",
        (attrs.get("subtitle"), "master"),
        (
            str(enrichment.get("subtitle") or "").strip() if enrichment else None,
            "isbn",
        ),
    )
    binding, binding_source = reviewed_bibliographic(
        "binding",
        (attrs.get("binding"), "master"),
        (attrs.get("physical_format"), "master"),
        (
            str(enrichment.get("physical_format") or "").strip() if enrichment else None,
            "isbn",
        ),
    )
    language, language_source = reviewed_bibliographic(
        "language",
        (vmeta.get("language"), "vinted"),
        (attrs.get("language"), "master"),
    )
    pages, pages_source = reviewed_bibliographic(
        "pages",
        (attrs.get("pages"), "master"),
        (attrs.get("number_of_pages"), "master"),
        ((enrichment or {}).get("number_of_pages"), "isbn"),
    )
    condition, condition_source = reviewed_bibliographic(
        "condition",
        (vmeta.get("condition"), "vinted"),
        (item.condition, "master"),
    )
    publication_place, publication_place_source = reviewed_bibliographic(
        "publication_place",
        (attrs.get("publication_place"), "master"),
        (attrs.get("place_of_publication"), "master"),
    )
    first_edition, first_edition_source = reviewed_bibliographic(
        "first_edition",
        (attrs.get("first_edition"), "master"),
    )
    signed, signed_source = reviewed_bibliographic(
        "signed",
        (attrs.get("signed"), "master"),
    )
    dust_jacket_present, dust_jacket_present_source = reviewed_bibliographic(
        "dust_jacket_present",
        (attrs.get("dust_jacket_present"), "master"),
        (attrs.get("dj_present"), "master"),
    )
    dust_jacket_condition, dust_jacket_condition_source = reviewed_bibliographic(
        "dust_jacket_condition",
        (attrs.get("dust_jacket_condition"), "master"),
        (attrs.get("dj_condition"), "master"),
    )
    dust_jacket_description, dust_jacket_description_source = reviewed_bibliographic(
        "dust_jacket_description",
        (attrs.get("dust_jacket_description"), "master"),
        (attrs.get("dj_description"), "master"),
    )
    illustrator, illustrator_source = reviewed_bibliographic(
        "illustrator",
        (attrs.get("illustrator"), "master"),
    )
    keywords, keywords_source = reviewed_bibliographic(
        "keywords",
        (attrs.get("keywords"), "master"),
    )
    catalog_values: dict[str, Any] = {}
    catalog_sources: dict[str, str | None] = {}
    for index in range(1, 9):
        key = f"catalog_{index}"
        value, source_name = reviewed_bibliographic(key, (attrs.get(key), "master"))
        catalog_values[key] = value
        catalog_sources[key] = source_name

    enrichment_fields = {
        "subtitle": subtitle,
        "publisher": publisher,
        "edition": edition,
        "binding": binding,
        "language": language,
        "pages": pages,
        "publish_date": publish_date,
        "condition": condition,
        "publication_place": publication_place,
        "first_edition": _optional_bool(first_edition),
        "signed": _optional_bool(signed),
        "dust_jacket_present": _optional_bool(dust_jacket_present),
        "dust_jacket_condition": dust_jacket_condition,
        "dust_jacket_description": dust_jacket_description,
        "illustrator": illustrator,
        "keywords": keywords,
        **catalog_values,
    }
    bibliographic_sources = {
        "subtitle": subtitle_source,
        "publisher": publisher_source,
        "edition": edition_source,
        "binding": binding_source,
        "language": language_source,
        "pages": pages_source,
        "publish_date": publish_date_source,
        "condition": condition_source,
        "publication_place": publication_place_source,
        "first_edition": first_edition_source,
        "signed": signed_source,
        "dust_jacket_present": dust_jacket_present_source,
        "dust_jacket_condition": dust_jacket_condition_source,
        "dust_jacket_description": dust_jacket_description_source,
        "illustrator": illustrator_source,
        "keywords": keywords_source,
        **catalog_sources,
    }

    book_id = existing.external_id if existing else item.sku
    image_urls = _source_image_urls(
        item,
        vinted,
        include_master_fallbacks=False,
    )

    fields = {
        "sku": item.sku,
        "book_id": book_id,
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
        "book_id": "biblio" if existing else "master",
        "title": title_source,
        "author": author_source,
        "description": description_source,
        "isbn": isbn_source,
        "price_cents": price_source,
        "currency": currency_source,
        "quantity": "master",
    }

    candidate = {
        "item_id": str(item.id),
        "item_title": item.title,
        "category": item.category,
        "fields": fields,
        "field_sources": sources,
        "missing": [],
        "ready": False,
        "existing_biblio_listing_id": str(existing.id) if existing else None,
        "already_listed": bool(existing and existing.status == ListingStatus.ACTIVE),
        "book_id_locked": bool(
            existing
            and (
                existing_extra.get("inventory_synced_at")
                or existing_extra.get("inventory_sync_signature")
            )
        ),
        "source": {
            "channel": Channel.VINTED if vinted else "master",
            "listing_id": str(vinted.id) if vinted else None,
            "external_id": vinted.external_id if vinted else None,
            "url": vinted.url if vinted else None,
            "image_urls": image_urls,
            "photo_count": len(image_urls),
        },
        "book_id_suggestion": book_id,
        "enrichment_warning": enrichment_warning,
        "bibliographic_enrichment": enrichment_fields,
        "bibliographic_sources": bibliographic_sources,
    }
    return validate_biblio_candidate(
        session,
        workspace_id,
        item.id,
        candidate,
    )


def apply_biblio_overrides(
    candidate: dict[str, Any],
    overrides: dict[str, Any],
) -> dict[str, Any]:
    fields = dict(candidate.get("fields") or {})
    sources = dict(candidate.get("field_sources") or {})
    for key in ("title", "author", "description", "isbn", "book_id"):
        if key not in overrides or overrides[key] is None:
            continue
        value = str(overrides[key]).strip()
        if key == "isbn":
            fields[key] = clean_isbn(value) if value else None
            sources[key] = "review"
        elif value:
            fields[key] = value
            sources[key] = "review"
    if overrides.get("price_cents") is not None:
        price = int(overrides["price_cents"])
        if price < 0:
            raise ValueError("BIBLIO price cannot be negative")
        fields["price_cents"] = price
        sources["price_cents"] = "review"

    bibliographic = dict(candidate.get("bibliographic_enrichment") or {})
    bibliographic_sources = dict(candidate.get("bibliographic_sources") or {})
    text_bibliographic_fields = (
        "subtitle", "publisher", "edition", "binding",
        "language", "publish_date", "condition", "publication_place",
        "dust_jacket_condition", "dust_jacket_description",
        "illustrator", "keywords",
        "catalog_1", "catalog_2", "catalog_3", "catalog_4",
        "catalog_5", "catalog_6", "catalog_7", "catalog_8",
    )
    for key in text_bibliographic_fields:
        if key not in overrides or overrides[key] is None:
            continue
        bibliographic[key] = str(overrides[key]).strip() or None
        bibliographic_sources[key] = "review"

    if "pages" in overrides and overrides["pages"] is not None:
        value = str(overrides["pages"]).strip()
        bibliographic["pages"] = int(value) if value else None
        bibliographic_sources["pages"] = "review"

    for key in ("first_edition", "signed", "dust_jacket_present"):
        if key not in overrides:
            continue
        bibliographic[key] = _optional_bool(overrides[key])
        bibliographic_sources[key] = "review"

    if not fields.get("book_id") and fields.get("sku"):
        fields["book_id"] = fields["sku"]
        sources.setdefault("book_id", sources.get("sku") or "master")

    missing: list[str] = []
    for key, label in (
        ("book_id", "Book ID"),
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
        "bibliographic_enrichment": bibliographic,
        "bibliographic_sources": bibliographic_sources,
        "missing": missing,
        "ready": not missing,
        "photo_warning": _biblio_photo_book_id_warning(
            fields.get("book_id"),
            int(dict(candidate.get("source") or {}).get("photo_count") or 0),
        ),
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
    fields = dict(candidate["fields"])
    external_id = str(fields.get("book_id") or "").strip()
    if not external_id:
        raise ValueError("BIBLIO Book ID is required")
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
    source = dict(candidate.get("source") or {})
    previous_external_id = existing.external_id if existing is not None else None
    if (
        existing is not None
        and str(previous_external_id or "") != external_id
    ):
        remote_state = dict(existing.extra or {})
        if (
            remote_state.get("inventory_synced_at")
            or remote_state.get("inventory_sync_signature")
        ):
            raise ValueError(
                "BIBLIO Book ID cannot be changed after its first successful inventory upload; "
                "changing it would risk leaving the old remote listing active"
            )

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
    existing.external_id = external_id
    existing.external_sku = external_id
    existing.title = str(fields["title"])
    existing.price_cents = int(fields["price_cents"])
    existing.currency = str(fields.get("currency") or item.currency or "EUR")
    existing.status = ListingStatus.ACTIVE
    existing.quantity = max(1, int(fields.get("quantity") or 1))
    existing.last_seen_at = now
    previous_extra = dict(existing.extra or {})
    image_urls = list(source.get("image_urls") or [])[:12]
    previous_images = list(previous_extra.get("image_urls") or [])
    if image_urls:
        photo_sync_state = (
            "queued"
            if (
                image_urls != previous_images
                or not previous_extra.get("photo_sync_signature")
                or (
                    previous_external_id is not None
                    and str(previous_external_id) != external_id
                )
                or (
                    previous_extra.get("photo_book_id")
                    and str(previous_extra.get("photo_book_id")) != external_id
                )
            )
            else previous_extra.get("photo_sync_state") or "ftp_uploaded"
        )
    else:
        photo_sync_state = "none"

    had_remote_verification = bool(
        previous_extra.get("remote_verified")
        or previous_extra.get("remote_verified_at")
    )
    existing.extra = {
        **previous_extra,
        "remote_verified": False if had_remote_verification else previous_extra.get("remote_verified", False),
        "remote_matches_local": None if had_remote_verification else previous_extra.get("remote_matches_local"),
        "remote_mismatch_fields": [] if had_remote_verification else list(previous_extra.get("remote_mismatch_fields") or []),
        "remote_verification_stale": True if had_remote_verification else bool(previous_extra.get("remote_verification_stale")),
        "remote_stale_since": now.isoformat() if had_remote_verification else previous_extra.get("remote_stale_since"),
        "author": fields.get("author"),
        "description": fields.get("description"),
        "isbn": fields.get("isbn"),
        "source_channel": source.get("channel"),
        "source_listing_id": source.get("listing_id"),
        "source_listing_external_id": source.get("external_id"),
        "field_sources": dict(candidate.get("field_sources") or {}),
        "image_urls": image_urls,
        "image_source": source.get("channel") if image_urls else None,
        "bibliographic_enrichment": dict(candidate.get("bibliographic_enrichment") or {}),
        "bibliographic_sources": dict(candidate.get("bibliographic_sources") or {}),
        "cross_listed_at": now.isoformat(),
        "publish_state": "queued",
        "publish_queued_at": now.isoformat(),
        "photo_sync_state": photo_sync_state,
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
    bibliographic = dict(candidate.get("bibliographic_enrichment") or {})
    bibliographic_sources = dict(candidate.get("bibliographic_sources") or {})
    supported_bibliographic = {
        "subtitle", "publisher", "edition", "binding",
        "language", "pages", "publish_date", "condition",
        "publication_place", "first_edition", "signed",
        "dust_jacket_present", "dust_jacket_condition",
        "dust_jacket_description", "illustrator", "keywords",
        "catalog_1", "catalog_2", "catalog_3", "catalog_4",
        "catalog_5", "catalog_6", "catalog_7", "catalog_8",
    }
    for key, value in bibliographic.items():
        if key not in supported_bibliographic:
            continue
        source_name = bibliographic_sources.get(key) or "unknown"
        if source_name == "review":
            if value in (None, ""):
                attrs.pop(key, None)
                attrs.pop(f"{key}_source", None)
            else:
                attrs[key] = value
                attrs[f"{key}_source"] = source_name
        elif value not in (None, "") and key not in attrs:
            attrs[key] = value
            attrs[f"{key}_source"] = source_name
    if bibliographic_sources.get("condition") == "review":
        item.condition = (
            str(bibliographic.get("condition")).strip()
            if bibliographic.get("condition") not in (None, "")
            else None
        )
    item.attributes = attrs
    session.flush()
    return existing
