"""Physical-stock policy shared by ingestion and reconciliation."""

from __future__ import annotations

from typing import Any


_NON_SALE_TOKENS = ("cancel", "refund", "fail")


def sale_counts_as_sold(sale_or_status: Any, lifecycle_status: str | None = None) -> bool:
    """Return whether a seller-side order consumes physical stock.

    A row from the marketplace's sold-order feed counts unless its current
    lifecycle explicitly says the transaction was cancelled, refunded or
    failed. Pending shipment/payment states still reserve the physical item.
    """
    if hasattr(sale_or_status, "direction"):
        sale = sale_or_status
        if str(getattr(sale, "direction", "")) != "sell":
            return False
        status = str(getattr(sale, "status", "") or "")
        lifecycle = str(getattr(sale, "lifecycle_status", "") or "")
    else:
        status = str(sale_or_status or "")
        lifecycle = str(lifecycle_status or "")
    state = f"{status} {lifecycle}".strip().casefold()
    if not state:
        # A historical/imported order with no lifecycle evidence is not
        # enough to consume physical stock. Current marketplace orders carry
        # a concrete status/lifecycle state.
        return False
    return not any(token in state for token in _NON_SALE_TOKENS)
