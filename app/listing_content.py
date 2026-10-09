"""Field-level listing comparison with explicit data provenance.

Imported listing values are snapshots, never proof of current publication.
Only a live verified connector read can approve remote content writes.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app import models


FIELDS = (
    ("title", "Title"),
    ("description", "Description"),
    ("condition", "Condition"),
    ("isbn", "ISBN"),
    ("author", "Author"),
    ("publisher", "Publisher"),
    ("edition", "Edition"),
    ("language", "Language"),
)
MAX_TEXT = {"title": 500, "description": 10000}


def master_values(item: models.InventoryItem) -> dict[str, str | None]:
    attributes = dict(item.attributes or {})
    return {
        "title": str(item.title or "").strip() or None,
        "description": _value(attributes.get("description")),
        "condition": _value(item.condition),
        **{key: _value(attributes.get(key)) for key in
           ("isbn", "author", "publisher", "edition", "language")},
    }


def _value(value: Any) -> str | None:
    if value is None or not isinstance(value, (str, int)):
        return None
    result = str(value).strip()
    return result or None


def saved_listing_values(listing: models.ChannelListing) -> dict[str, str | None]:
    extra = dict(listing.extra or {})
    # This is deliberately a conservative snapshot: absent data stays unknown,
    # not an empty string that the system could mistake for a difference.
    values = {"title": _value(listing.title)}
    for key in ("description", "condition", "isbn", "author", "publisher",
                "edition", "language"):
        values[key] = _value(extra.get(key))
    return values


def rows(
    master: dict[str, str | None],
    remote: dict[str, str | None],
    *,
    source: str,
    writable: set[str] | frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    result = []
    for key, label in FIELDS:
        local = master.get(key)
        value = remote.get(key)
        status = ("missing_master" if local is None
                  else "unknown" if value is None
                  else "match" if local == value else "differs")
        result.append({
            "key": key, "label": label,
            "master": local, "marketplace": value,
            "status": status, "source": source,
            "writable": key in writable and status == "differs",
        })
    return result


def fingerprint(data: dict[str, Any]) -> str:
    """Bind a saved check to exact master and remote fields, IDs and SKU."""
    packed = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()
