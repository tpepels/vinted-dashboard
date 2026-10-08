"""Explicit physical-stock reconciliation.

Connector ingestion may create separate InventoryItem rows when marketplaces do
not share a real SKU.  This module finds conservative, explainable candidate
matches but never changes linkage while generating suggestions.

Only exact normalized identifiers/metadata are used:
- real SKU / marketplace SKU;
- ISBN;
- exact title + author;
- exact title + book publisher/year;
- exact title + clothing brand/size.

There is deliberately no fuzzy title matching and no automatic merge.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.connectors.workspace_sync import recompute_inventory_item
from app.stock_relations import is_physical, is_provisional
from app.workspace_bootstrap import clean_isbn, normalize_sku


def _norm(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _synthetic_sku(item: models.InventoryItem, listings: list[models.ChannelListing]) -> bool:
    value = normalize_sku(item.sku)
    if not value:
        return True
    upper = value.upper()
    return any(
        upper == f"{listing.channel.upper()}-{listing.external_id}".upper()
        for listing in listings
    )


def _profile(item: models.InventoryItem, listings: list[models.ChannelListing]) -> dict[str, Any]:
    attrs = dict(item.attributes or {})
    channels = sorted({row.channel for row in listings})

    skus: set[str] = {
        sku.upper()
        for row in listings
        if (sku := normalize_sku(row.external_sku))
    }
    if not _synthetic_sku(item, listings):
        item_sku = normalize_sku(item.sku)
        if item_sku:
            skus.add(item_sku.upper())

    isbns: set[str] = set()
    isbn = clean_isbn(attrs.get("isbn"))
    if isbn:
        isbns.add(isbn)
    for row in listings:
        isbn = clean_isbn((row.extra or {}).get("isbn"))
        if isbn:
            isbns.add(isbn)

    author = _norm(attrs.get("author"))
    publisher = _norm(attrs.get("publisher"))
    publication_year = _norm(attrs.get("publication_year"))
    brand = _norm(attrs.get("brand"))
    size = _norm(attrs.get("size"))

    for row in listings:
        extra = dict(row.extra or {})
        if not author:
            author = _norm(extra.get("author"))
        if not publisher:
            publisher = _norm(extra.get("publisher"))
        if not publication_year:
            publication_year = _norm(extra.get("publication_year"))
        if not brand:
            brand = _norm(extra.get("brand"))
        if not size:
            size = _norm(extra.get("size"))

    return {
        "item": item,
        "listings": listings,
        "channels": channels,
        "skus": skus,
        "isbns": isbns,
        "title": _norm(item.title),
        "author": author,
        "publisher": publisher,
        "publication_year": publication_year,
        "brand": brand,
        "size": size,
        "synthetic_sku": _synthetic_sku(item, listings),
    }


def _serialize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    item = profile["item"]
    return {
        "id": str(item.id),
        "sku": item.sku,
        "title": item.title,
        "category": item.category,
        "quantity": item.quantity,
        "status": item.status,
        "attributes": dict(item.attributes or {}),
        "channels": profile["channels"],
        "listing_count": len(profile["listings"]),
        "synthetic_sku": profile["synthetic_sku"],
    }


def _evidence(a: dict[str, Any], b: dict[str, Any]) -> tuple[str | None, list[str]]:
    shared_skus = sorted(a["skus"] & b["skus"])
    if shared_skus:
        return "high", [f"Exact SKU: {shared_skus[0]}"]

    shared_isbns = sorted(a["isbns"] & b["isbns"])
    if shared_isbns:
        return "high", [f"Exact ISBN: {shared_isbns[0]}"]

    if a["title"] and a["title"] == b["title"]:
        if a["author"] and a["author"] == b["author"]:
            return "medium", ["Exact title", "Exact author"]
        if (
            a["publisher"]
            and a["publisher"] == b["publisher"]
            and a["publication_year"]
            and a["publication_year"] == b["publication_year"]
        ):
            return "medium", ["Exact title", "Exact publisher", "Exact publication year"]
        if a["brand"] and a["brand"] == b["brand"] and a["size"] and a["size"] == b["size"]:
            return "medium", ["Exact title", "Exact brand", "Exact size"]
    return None, []


def _target_rank(profile: dict[str, Any]) -> tuple[int, int, int, str]:
    item = profile["item"]
    attrs = dict(item.attributes or {})
    populated = sum(value not in (None, "", [], {}) for value in attrs.values())
    return (
        0 if profile["synthetic_sku"] else 1,
        len(profile["listings"]),
        populated,
        str(item.id),
    )


def reconciliation_suggestions(
    session: Session,
    workspace_id: uuid.UUID,
) -> list[dict[str, Any]]:
    items = session.execute(
        select(models.InventoryItem)
        .where(models.InventoryItem.workspace_id == workspace_id)
        .order_by(models.InventoryItem.created_at, models.InventoryItem.id)
    ).scalars().all()
    listings = session.execute(
        select(models.ChannelListing).where(models.ChannelListing.workspace_id == workspace_id)
    ).scalars().all()
    by_item: dict[uuid.UUID, list[models.ChannelListing]] = defaultdict(list)
    for listing in listings:
        if listing.inventory_item_id:
            by_item[listing.inventory_item_id].append(listing)

    profiles = [_profile(item, by_item.get(item.id, [])) for item in items]
    result: list[dict[str, Any]] = []
    for index, left in enumerate(profiles):
        for right in profiles[index + 1 :]:
            left_channels = set(left["channels"])
            right_channels = set(right["channels"])
            if left_channels and right_channels and left_channels & right_channels:
                continue
            confidence, reasons = _evidence(left, right)
            if confidence is None:
                continue

            if _target_rank(left) >= _target_rank(right):
                target, source = left, right
            else:
                target, source = right, left
            pair_ids = sorted([str(left["item"].id), str(right["item"].id)])
            result.append(
                {
                    "id": ":".join(pair_ids),
                    "confidence": confidence,
                    "reasons": reasons,
                    "item_a": _serialize_profile(left),
                    "item_b": _serialize_profile(right),
                    "recommended_target_id": str(target["item"].id),
                    "recommended_source_id": str(source["item"].id),
                }
            )

    confidence_order = {"high": 0, "medium": 1}
    result.sort(
        key=lambda row: (
            confidence_order.get(row["confidence"], 9),
            row["item_a"]["title"].casefold(),
            row["id"],
        )
    )
    return result


def _fill_missing(target: models.InventoryItem, source: models.InventoryItem) -> list[str]:
    conflicts: list[str] = []
    for field in ("condition", "cost_cents", "currency", "location", "notes"):
        current = getattr(target, field)
        incoming = getattr(source, field)
        if current in (None, "") and incoming not in (None, ""):
            setattr(target, field, incoming)
        elif current not in (None, "") and incoming not in (None, "") and current != incoming:
            conflicts.append(field)

    if (not target.title or target.title == "Untitled") and source.title:
        target.title = source.title
    elif target.title and source.title and _norm(target.title) != _norm(source.title):
        conflicts.append("title")

    if target.category == "general" and source.category != "general":
        target.category = source.category
    elif target.category != source.category and source.category != "general":
        conflicts.append("category")

    attrs = dict(target.attributes or {})
    for key, value in dict(source.attributes or {}).items():
        if key.startswith("connector_import_") or key in {
            "stock_authority", "stock_base_quantity", "relationship_confirmed_at",
        }:
            continue
        if key not in attrs or attrs[key] in (None, ""):
            attrs[key] = value
        elif value not in (None, "") and _norm(attrs[key]) != _norm(value):
            conflicts.append(f"attributes.{key}")
    target.attributes = attrs
    return conflicts


def merge_inventory_items(
    session: Session,
    workspace_id: uuid.UUID,
    *,
    target_item_id: uuid.UUID,
    source_item_ids: list[uuid.UUID],
) -> dict[str, Any]:
    source_ids = list(dict.fromkeys(source_item_ids))
    if not source_ids:
        raise ValueError("At least one source item is required")
    if target_item_id in source_ids:
        raise ValueError("Target item cannot also be a source item")

    target = session.get(models.InventoryItem, target_item_id)
    if target is None or target.workspace_id != workspace_id:
        raise ValueError("Target inventory item not found")

    sources: list[models.InventoryItem] = []
    for source_id in source_ids:
        source = session.get(models.InventoryItem, source_id)
        if source is None or source.workspace_id != workspace_id:
            raise ValueError("Source inventory item not found")
        sources.append(source)

    conflicts: set[str] = set()
    moved_listings = 0
    moved_sales = 0
    fallback_quantity = max([int(target.quantity or 0)] + [int(row.quantity or 0) for row in sources])

    for source in sources:
        conflicts.update(_fill_missing(target, source))
        source_listings = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.inventory_item_id == source.id,
            )
        ).scalars().all()
        for listing in source_listings:
            listing.inventory_item_id = target.id
            moved_listings += 1

        source_sales = session.execute(
            select(models.Sale).where(
                models.Sale.workspace_id == workspace_id,
                models.Sale.inventory_item_id == source.id,
            )
        ).scalars().all()
        for sale in source_sales:
            sale.inventory_item_id = target.id
            moved_sales += 1

    # An explicit merge confirms these marketplace advertisements share one
    # physical stock record. Never add their advertised quantities together.
    attrs = dict(target.attributes or {})
    previous_baseline = (
        int(attrs.get("stock_base_quantity") or 0) if is_physical(target)
        else 0
    )
    attrs["stock_authority"] = "physical"
    attrs["stock_base_quantity"] = max(previous_baseline, fallback_quantity)
    attrs["relationship_confirmed_at"] = datetime.now(timezone.utc).isoformat()
    for key in ("connector_import_placeholder", "connector_import_channel",
                "connector_import_external_id", "connector_import_sku"):
        attrs.pop(key, None)
    target.attributes = attrs

    # Preserve pending action references when their previous master is merged.
    from app.product_models import CrossChannelAction
    for source in sources:
        actions = session.execute(
            select(CrossChannelAction).where(
                CrossChannelAction.workspace_id == workspace_id,
                CrossChannelAction.inventory_item_id == source.id,
            )
        ).scalars().all()
        for action in actions:
            action.inventory_item_id = target.id

    session.flush()
    recompute_inventory_item(session, target)

    for source in sources:
        session.delete(source)
    session.flush()

    return {
        "target_item_id": str(target.id),
        "merged_source_item_ids": [str(row.id) for row in sources],
        "moved_listings": moved_listings,
        "moved_sales": moved_sales,
        "conflicts_kept_from_target": sorted(conflicts),
    }


def apply_reconciliation_merges(
    session: Session,
    workspace_id: uuid.UUID,
    merges: list[tuple[uuid.UUID, uuid.UUID]],
) -> list[dict[str, Any]]:
    if not merges:
        raise ValueError("Select at least one reconciliation")

    source_ids = [source for target, source in merges]
    if len(set(source_ids)) != len(source_ids):
        raise ValueError("A source item can only be merged once")

    targets = {target for target, _source in merges}
    if targets & set(source_ids):
        raise ValueError("An item cannot be both a target and a source in one bulk reconciliation")

    grouped: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for target, source in merges:
        if target == source:
            raise ValueError("Target and source must be different items")
        grouped[target].append(source)

    return [
        merge_inventory_items(
            session,
            workspace_id,
            target_item_id=target,
            source_item_ids=sources,
        )
        for target, sources in grouped.items()
    ]
