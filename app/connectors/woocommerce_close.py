"""Check-first WooCommerce sold-out unpublishing.

Only an explicitly linked simple product can be unpublished. This adapter
does not delete products, close variations, change stock or change prices.
A readback must confirm draft status; uncertain PUT results are not retried.
"""
from __future__ import annotations

import uuid
from typing import Any

from curl_cffi import requests

from app.constants import Channel
from app.connectors import hosted
from app.listing_content import fingerprint


def _inspect(values: dict[str, str], external_id: str, expected_sku: str) -> dict[str, Any]:
    if not str(expected_sku or "").strip():
        raise ValueError("A confirmed WooCommerce SKU is required")
    if ":" in str(external_id):
        raise ValueError("WooCommerce variations must be closed manually")
    path, product, _parent_status, variation = hosted._woocommerce_stock_record(
        values, external_id, expected_sku,
    )
    if variation or str(product.get("type") or "").lower() != "simple":
        raise ValueError("Only linked WooCommerce simple products can be unpublished")
    status = str(product.get("status") or "").lower()
    if status not in {"publish", "draft", "private", "pending"}:
        raise ValueError("WooCommerce returned an unrecognized publication status")
    identity = {
        "external_id": str(external_id), "sku": expected_sku,
        "path": path, "type": "simple", "status": status,
        "date_modified_gmt": str(product.get("date_modified_gmt") or ""),
        "name": str(product.get("name") or ""),
        "regular_price": str(product.get("regular_price") or ""),
        "stock_quantity": product.get("stock_quantity"),
    }
    return {
        "external_id": str(external_id), "sku": expected_sku,
        "status": status, "fingerprint": fingerprint(identity),
        "can_unpublish": status == "publish",
    }


def read_woocommerce_workspace_publication(
    workspace_id: uuid.UUID, *, external_id: str, expected_sku: str,
) -> dict[str, Any]:
    hosted._woocommerce_stock_path(external_id)
    values = hosted._credentials(workspace_id, Channel.WOOCOMMERCE)
    return _inspect(values, external_id, expected_sku)


def unpublish_woocommerce_workspace_product(
    workspace_id: uuid.UUID, *, external_id: str, expected_sku: str,
    expected_fingerprint: str,
) -> dict[str, Any]:
    """One explicit status-only PUT, preceded and followed by a remote GET."""
    hosted._woocommerce_stock_path(external_id)
    values = hosted._credentials(workspace_id, Channel.WOOCOMMERCE)
    before = _inspect(values, external_id, expected_sku)
    if before["fingerprint"] != expected_fingerprint:
        raise ValueError("WooCommerce listing changed since inspection; check again")
    if before["status"] != "publish":
        raise ValueError("Only published WooCommerce products can be unpublished")
    response = requests.put(
        hosted._woocommerce_base(values) + "/wp-json/wc/v3/products/" + str(external_id),
        headers={**hosted._woocommerce_headers(values), "Content-Type": "application/json"},
        json={"status": "draft"},
        timeout=45,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"WooCommerce unpublish failed ({response.status_code})")
    after = _inspect(values, external_id, expected_sku)
    if after["status"] != "draft":
        raise RuntimeError("WooCommerce product is not draft after unpublishing")
    return {
        "remote_verified": True, "external_id": str(external_id),
        "status": "draft", "already_complete": False,
    }
