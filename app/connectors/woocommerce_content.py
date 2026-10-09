"""WooCommerce live listing comparison and narrow, verified content update.

Only name/description of a linked simple product, or description of a linked
variation, may be changed. Attributes, identifiers, price, stock, visibility,
media and taxonomy are read-only to avoid destructive array replacement.
"""
from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from curl_cffi import requests

from app.constants import Channel
from app.connectors import hosted
from app.listing_content import fingerprint


def _string(value: Any) -> str | None:
    return str(value) if isinstance(value, str) else None


def _cents(value: Any) -> str | None:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
        return None
    return str(int(amount * 100))


def _inspect(values: dict[str, str], external_id: str, sku: str) -> dict[str, Any]:
    if not str(sku or "").strip():
        raise ValueError("A confirmed linked SKU is required to check WooCommerce content")
    path, remote, _status, variation = hosted._woocommerce_stock_record(
        values, external_id, sku,
    )
    if remote.get("description") is None:
        raise ValueError("WooCommerce did not return a description field")
    if not variation and remote.get("name") is None:
        raise ValueError("WooCommerce did not return a product title")
    writable = ["description"] if variation else ["title", "description"]
    attributes: dict[str, str | None] = {}
    if not variation:
        for row in remote.get("attributes") or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip().lower()
            options = row.get("options") or []
            if (name in {"condition", "author", "publisher", "edition", "language"}
                    and isinstance(options, list) and len(options) == 1
                    and isinstance(options[0], str)):
                attributes[name] = options[0]
    else:
        # Variant-specific attributes represent options, not a book's other
        # descriptive attributes. Do not guess them from the parent.
        pass
    fields = {
        "title": _string(remote.get("name")) if not variation else None,
        "description": _string(remote.get("description")),
        "price": _cents(remote.get("regular_price")),
        "condition": attributes.get("condition"),
        "isbn": _string(remote.get("global_unique_id")),
        "author": attributes.get("author"),
        "publisher": attributes.get("publisher"),
        "edition": attributes.get("edition"),
        "language": attributes.get("language"),
    }
    revision = str(remote.get("date_modified_gmt") or "")
    snapshot = {
        "external_id": external_id, "sku": sku, "path": path,
        "variation": variation, "fields": fields,
        "revision": revision,
    }
    return {
        **snapshot, "writable_fields": writable,
        "fingerprint": fingerprint(snapshot),
    }


def read_woocommerce_workspace_content(
    workspace_id: uuid.UUID, *, external_id: str, expected_sku: str,
) -> dict[str, Any]:
    hosted._woocommerce_stock_path(external_id)
    return _inspect(
        hosted._credentials(workspace_id, Channel.WOOCOMMERCE),
        external_id, expected_sku,
    )


def update_woocommerce_workspace_content(
    workspace_id: uuid.UUID, *, external_id: str, expected_sku: str,
    expected_fingerprint: str, changes: dict[str, str],
) -> dict[str, Any]:
    """Perform one non-retriable WooCommerce PUT after a fresh exact read."""
    hosted._woocommerce_stock_path(external_id)
    if (not changes or not isinstance(changes, dict)
            or any(k not in {"title", "description"} for k in changes)):
        raise ValueError("Only title and description are supported for WooCommerce")
    for key, value in changes.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must have a non-empty master value")
        if len(value) > (500 if key == "title" else 10000):
            raise ValueError(f"{key} exceeds the supported length")
    values = hosted._credentials(workspace_id, Channel.WOOCOMMERCE)
    before = _inspect(values, external_id, expected_sku)
    if before["fingerprint"] != expected_fingerprint:
        raise ValueError("WooCommerce content changed since the last check; compare again")
    if not set(changes).issubset(set(before["writable_fields"])):
        raise ValueError("This WooCommerce variant does not support these fields")
    if all(before["fields"].get(key) == value for key, value in changes.items()):
        return {"remote_verified": True, "already_complete": True,
                "external_id": external_id, "fields": sorted(changes)}
    body = {("name" if key == "title" else key): value for key, value in changes.items()}
    response = requests.put(
        hosted._woocommerce_base(values) + "/wp-json/wc/v3/" + before["path"],
        headers={**hosted._woocommerce_headers(values), "Content-Type": "application/json"},
        json=body, timeout=45,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"WooCommerce content update failed ({response.status_code})")
    after = _inspect(values, external_id, expected_sku)
    if (after["path"] != before["path"] or
            any(after["fields"].get(key) != value for key, value in changes.items())):
        raise RuntimeError("WooCommerce content differs after update; inspect the listing")
    return {
        "remote_verified": True, "already_complete": False,
        "external_id": external_id, "fields": sorted(changes),
    }
