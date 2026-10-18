"""Acquisition costs per historical sale and book quantities.

Manual cost overrides are the total for an order; bulk estimates are per book.
The Vinted order count is not always available, so unknown is never hidden.
"""
import re
from datetime import datetime, timezone
from typing import Any


def _positive_count(value: Any) -> int | None:
    # Reject booleans, fractions, malformed strings, and impossible quantities.
    if type(value) is int and 1 <= value <= 1000:
        return value
    if isinstance(value, str) and value.isdecimal():
        number = int(value)
        if 1 <= number <= 1000:
            return number
    return None


_BUNDLE_TITLE = re.compile(
    r"^(?:bundle|bundel)\s+(?:of\s+)?(\d{1,4})\s+(?:items?|books?|artikelen)\b",
    re.IGNORECASE,
)
_GENERIC_ORDER_TITLES = {"vinted order", "order", "untitled", "unknown"}


def _title_quantity(sale: Any) -> tuple[int | None, str]:
    """Estimate historical Vinted counts using the sale title, without resync.

    A named non-bundle order is assumed to contain one item. Missing titles
    and bundles with an unparseable count remain unknown.
    """
    if str(getattr(sale, "channel", "") or "").lower() != "vinted":
        return None, "unknown"
    title = str(getattr(sale, "title", "") or "").strip()
    if not title or title.lower() in _GENERIC_ORDER_TITLES:
        return None, "unknown"
    bundle = _BUNDLE_TITLE.match(title)
    if bundle:
        quantity = _positive_count(bundle.group(1))
        return (quantity, "title_bundle") if quantity is not None else (None, "unknown")
    if re.search(r"\b(?:bundle|bundel)\b", title, re.IGNORECASE):
        return None, "unknown"
    return 1, "title_single"


def sale_book_quantity(sale: Any) -> tuple[int | None, str]:
    """Prefer manual and marketplace counts to title-derived estimates."""
    extra = sale.extra or {}
    manual = _positive_count(extra.get("book_quantity_override"))
    if manual is not None:
        return manual, "manual"
    collected = _positive_count(extra.get("quantity"))
    if collected is not None:
        return collected, "marketplace"
    return _title_quantity(sale)


def sale_acquisition_cost(sale: Any, item: Any | None = None) -> tuple[int | None, str | None]:
    extra = dict(sale.extra or {})
    source = str(extra.get("acquisition_cost_source") or "")
    count, _ = sale_book_quantity(sale)
    unit_cost = extra.get("acquisition_unit_cost_cents")
    # Every estimated_bulk value represents a per-book rate. Legacy bulk
    # overrides predate this field and stored that rate under cost_cents.
    if source == "estimated_bulk":
        if type(unit_cost) is not int:
            unit_cost = extra.get("acquisition_cost_cents")
        if type(unit_cost) is int and unit_cost >= 0:
            return unit_cost * (count or 1), source
    override = extra.get("acquisition_cost_cents")
    if type(override) is int and override >= 0:
        return override, source or "manual_sale"
    if item is not None and item.cost_cents is not None:
        if str(item.currency or "EUR").upper() == str(sale.currency or "EUR").upper():
            attrs = dict(item.attributes or {})
            return int(item.cost_cents) * (count or 1), str(attrs.get("cost_source") or "inventory")
    return None, None


def _history(sale: Any, kind: str, before: Any, after: Any, actor: str) -> dict:
    extra = dict(sale.extra or {})
    log = list(extra.get("acquisition_cost_history") or [])[-19:]
    log.append({
        "kind": kind,
        "before": before,
        "after": after,
        "changed_at": datetime.now(timezone.utc).isoformat(),
        "changed_by": actor,
    })
    extra["acquisition_cost_history"] = log
    return extra


def record_sale_book_quantity(sale: Any, quantity: int | None, actor: str) -> None:
    old_quantity, old_source = sale_book_quantity(sale)
    extra = _history(sale, "quantity", {"quantity": old_quantity, "source": old_source}, quantity, actor)
    if quantity is None:
        extra.pop("book_quantity_override", None)
    else:
        extra["book_quantity_override"] = quantity
    sale.extra = extra


def record_sale_acquisition_cost(sale: Any, cents: int | None, source: str, actor: str) -> None:
    extra = dict(sale.extra or {})
    previous = {
        "cents": extra.get("acquisition_cost_cents"),
        "unit_cents": extra.get("acquisition_unit_cost_cents"),
        "source": extra.get("acquisition_cost_source"),
    }
    extra = _history(sale, "cost", previous, {"cents": cents, "source": source}, actor)
    if cents is None:
        extra.pop("acquisition_cost_cents", None)
        extra.pop("acquisition_unit_cost_cents", None)
        extra.pop("acquisition_cost_source", None)
    elif source == "estimated_bulk":
        # Preserve the per-book rate so later quantity corrections reprice
        # the order automatically; never rewrite a real/manual order total.
        extra["acquisition_unit_cost_cents"] = cents
        extra["acquisition_cost_cents"] = cents
        extra["acquisition_cost_source"] = source
    else:
        extra["acquisition_cost_cents"] = cents
        extra.pop("acquisition_unit_cost_cents", None)
        extra["acquisition_cost_source"] = source
    sale.extra = extra
