"""Historical acquisition costs stored on a sale independent from stock links."""
from datetime import datetime, timezone
from typing import Any


def sale_acquisition_cost(sale: Any, item: Any | None = None) -> tuple[int | None, str | None]:
    extra = dict(sale.extra or {})
    override = extra.get("acquisition_cost_cents")
    if type(override) is int and override >= 0:
        return override, str(extra.get("acquisition_cost_source") or "manual_sale")
    if item is not None and item.cost_cents is not None:
        if str(item.currency or "EUR").upper() == str(sale.currency or "EUR").upper():
            attributes = dict(item.attributes or {})
            return int(item.cost_cents), str(attributes.get("cost_source") or "inventory")
    return None, None


def record_sale_acquisition_cost(sale: Any, cents: int | None, source: str, actor: str) -> None:
    extra = dict(sale.extra or {})
    history = list(extra.get("acquisition_cost_history") or [])[-19:]
    history.append({
        "previous_cents": extra.get("acquisition_cost_cents"),
        "previous_source": extra.get("acquisition_cost_source"),
        "new_cents": cents,
        "new_source": source if cents is not None else None,
        "changed_at": datetime.now(timezone.utc).isoformat(),
        "changed_by": actor,
    })
    extra["acquisition_cost_history"] = history
    if cents is None:
        extra.pop("acquisition_cost_cents", None)
        extra.pop("acquisition_cost_source", None)
    else:
        extra["acquisition_cost_cents"] = cents
        extra["acquisition_cost_source"] = source
    sale.extra = extra
