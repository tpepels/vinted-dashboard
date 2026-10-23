"""Workspace default acquisition cost for books, without writing fake purchase records.

The default is a reporting estimate, not a receipt; explicit item costs and
historical order overrides always win. Workspaces can adjust the default.
"""
from __future__ import annotations

from typing import Any

BOOK_COST_SETTING = "default_book_cost_cents"


def default_book_cost_cents(settings: Any) -> int | None:
    """No implicit €1 (or any other price): the owner must set this value."""
    values = settings if isinstance(settings, dict) else getattr(settings, "settings", None)
    if not isinstance(values, dict):
        return None
    value = values.get(BOOK_COST_SETTING)
    if type(value) is int and 0 <= value <= 10_000_000:
        return value
    return None


def is_book_item(item: Any) -> bool:
    if item is None:
        return False
    if str(getattr(item, "category", "") or "").lower() == "book":
        return True
    attributes = dict(getattr(item, "attributes", None) or {})
    # Vinted imports may be provisional before their physical item has been
    # categorized. This workspace's Vinted inventory consists of books.
    return (attributes.get("connector_import_channel") == "vinted"
            and bool(attributes.get("connector_import_placeholder")))


def effective_item_cost(item: Any, settings: Any) -> tuple[int | None, str | None]:
    if item is None:
        return None, None
    explicit = getattr(item, "cost_cents", None)
    if explicit is not None:
        attributes = dict(getattr(item, "attributes", None) or {})
        return int(explicit), str(attributes.get("cost_source") or "inventory")
    if is_book_item(item) and str(getattr(item, "currency", None) or "EUR").upper() == "EUR":
        default = default_book_cost_cents(settings)
        if default is not None:
            return default, "default_book"
    return None, None
