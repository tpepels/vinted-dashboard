"""Explicit Wix V3 stock reconciliation for a single verified variant/location.

Wix Catalog V3 PATCH uses inventory item ID and revision. We do not create
inventory items, change tracking mode, or push stock automatically.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

import requests

from app.constants import Channel, ListingStatus
from app.connectors import hosted


def _guid(value: Any) -> str:
    raw = str(value or "").strip()
    try:
        parsed = uuid.UUID(raw)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Wix stock requires a valid product and variant ID") from exc
    if str(parsed) != raw.lower():
        raise ValueError("Wix stock requires canonical product and variant IDs")
    return str(parsed)


def _identity(external_id: str, expected_sku: str | None) -> tuple[str, str, str]:
    parts = str(external_id or "").split(":")
    if len(parts) != 2:
        raise ValueError("Wix stock requires a linked product:variant identity")
    product_id, variant_id = (_guid(value) for value in parts)
    sku = str(expected_sku or "").strip()
    if not sku:
        raise ValueError("Wix stock requires a confirmed linked SKU")
    return product_id, variant_id, sku


def _read(values: dict[str, str], product_id: str, variant_id: str, sku: str) -> dict[str, Any]:
    # The existing Wix V3 variant reader contains productId and sku. Read it
    # rather than trusting the local marketplace snapshot alone.
    candidates = [
        row for row in hosted._wix_query_variants(values)
        if str(row.get("variantId") or "").lower() == variant_id
    ]
    if len(candidates) != 1:
        raise ValueError("Wix variant not found uniquely; reconcile its link")
    variant = candidates[0]
    product = variant.get("productData") or {}
    if str(product.get("productId") or "").lower() != product_id:
        raise ValueError("Wix product and variant no longer match the link")
    if str(variant.get("sku") or "").strip() != sku:
        raise ValueError("Wix SKU no longer matches the linked listing")

    # Filter by exact variant; never update a full catalog or multiple locations.
    payload = hosted._wix_post(
        values, "stores/v3/inventory-items/query",
        body={"query": {
            "filter": {"variantId": {"$eq": variant_id}},
            "cursorPaging": {"limit": 1000},
        }},
    )
    if hosted._wix_next_cursor(payload):
        raise ValueError("Wix has multiple stock pages for this variant")
    rows = payload.get("inventoryItems")
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError("Wix stock requires exactly one location for this variant")
    item = rows[0]
    if not isinstance(item, dict):
        raise ValueError("Wix stock item is malformed")
    if (str(item.get("productId") or "").lower() != product_id
            or str(item.get("variantId") or "").lower() != variant_id):
        raise ValueError("Wix returned stock for a different product or variant")
    inventory_id = _guid(item.get("id"))
    location_id = _guid(item.get("locationId"))
    revision = str(item.get("revision") or "")
    if not re.fullmatch(r"[0-9]+", revision):
        raise ValueError("Wix did not return a usable inventory revision")
    if item.get("trackQuantity") is not True:
        raise ValueError("Wix quantity tracking is disabled for this variant")
    if (item.get("preorderInfo") or {}).get("enabled"):
        raise ValueError("Wix preorders are enabled; review the listing manually")
    quantity = item.get("quantity")
    if type(quantity) is not int or quantity < 0:
        raise ValueError("Wix did not return a safe available quantity")
    return {
        "external_id": f"{product_id}:{variant_id}",
        "inventory_item_id": inventory_id,
        "location_id": location_id,
        "revision": revision,
        "quantity": quantity,
        "manage_stock": True,
        "stock_status": "instock" if quantity > 0 else "outofstock",
        "status": (ListingStatus.ACTIVE
                   if quantity > 0 and product.get("visible", True) and variant.get("visible", True)
                   else ListingStatus.INACTIVE),
    }


def read_wix_workspace_stock(
    workspace_id: uuid.UUID, *, external_id: str, expected_sku: str | None,
) -> dict[str, Any]:
    product_id, variant_id, sku = _identity(external_id, expected_sku)
    return _read(hosted._credentials(workspace_id, Channel.WIX), product_id, variant_id, sku)


def update_wix_workspace_stock(
    workspace_id: uuid.UUID, *, external_id: str, expected_sku: str | None,
    quantity: int,
) -> dict[str, Any]:
    product_id, variant_id, sku = _identity(external_id, expected_sku)
    if type(quantity) is not int or not 0 <= quantity <= 99999:
        raise ValueError("Wix stock must be a whole number between 0 and 99999")
    values = hosted._credentials(workspace_id, Channel.WIX)
    before = _read(values, product_id, variant_id, sku)
    if before["quantity"] == quantity:
        return {
            "remote_verified": True, "quantity": quantity,
            "external_id": str(external_id),
            "status": before["status"], "already_complete": True,
        }
    response = requests.patch(
        hosted.WIX_API_BASE + "/stores/v3/inventory-items/" + before["inventory_item_id"],
        headers=hosted._wix_headers(values),
        json={
            "inventoryItem": {
                "id": before["inventory_item_id"],
                "revision": before["revision"],
                "quantity": quantity,
            },
            "reason": "MANUAL",
        },
        timeout=45,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Wix inventory update failed ({response.status_code})")
    after = _read(values, product_id, variant_id, sku)
    if (after["inventory_item_id"] != before["inventory_item_id"]
            or after["location_id"] != before["location_id"]
            or after["quantity"] != quantity
            or after["revision"] == before["revision"]):
        raise RuntimeError("Wix inventory readback differs; review the remote item")
    return {
        "remote_verified": True, "quantity": quantity,
        "external_id": str(external_id),
        "status": after["status"], "already_complete": False,
    }
