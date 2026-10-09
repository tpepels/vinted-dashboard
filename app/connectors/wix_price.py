"""Explicit Wix Stores Catalog V3 pricing for one default product variant.

Wix's product PATCH replaces variant arrays. Restrict writes to a product with
one default variant and no options/modifiers; preserve identity and use the
latest product revision, then read back. Never touch inventory or promotions.
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from curl_cffi import requests

from app.constants import Channel
from app.connectors import hosted
from app.connectors.wix_stock import _identity


def _money(cents: int) -> str:
    if type(cents) is not int or not 0 < cents <= 2000000:
        raise ValueError("Price must be between 0.01 and 20000.00")
    return f"{cents // 100}.{cents % 100:02d}"


def _cents(value: Any) -> int:
    raw = str(value if value is not None else "").strip()
    if not raw:
        raise ValueError("Wix variant has no actual price")
    try:
        number = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("Wix returned an invalid actual price") from exc
    if not number.is_finite() or not 0 < number <= 20000 or number.as_tuple().exponent < -2:
        raise ValueError("Wix price is outside the supported two-decimal range")
    return int(number * 100)


def _snapshot(
    values: dict[str, str], product_id: str, variant_id: str,
    expected_sku: str, expected_currency: str,
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Z]{3}", str(expected_currency or "")):
        raise ValueError("A three-letter item currency is required")
    configured = str(values.get("currency") or "").strip().upper()
    # Wix often omits currency from individual catalog objects, so never
    # infer the currency from the amount when both sources are missing.
    if not configured:
        raise ValueError("Configure Wix store currency before changing prices")
    if configured != expected_currency:
        raise ValueError("Configured Wix currency differs from the item currency")
    response = requests.get(
        hosted.WIX_API_BASE + "/stores/v3/products/" + product_id,
        headers=hosted._wix_headers(values),
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Wix product read failed ({response.status_code})")
    payload = response.json() or {}
    product = payload.get("product") if isinstance(payload, dict) else None
    if not isinstance(product, dict) or str(product.get("id") or "").lower() != product_id:
        raise ValueError("Wix returned another product identity")
    revision = str(product.get("revision") or "")
    if not re.fullmatch(r"[0-9]+", revision):
        raise ValueError("Wix product revision is missing")
    live_currency = str(product.get("currency") or "").strip().upper()
    if live_currency and live_currency != expected_currency:
        raise ValueError("Wix product currency differs from item currency")
    options = product.get("options") or []
    modifiers = product.get("modifiers") or []
    if not isinstance(options, list) or options or not isinstance(modifiers, list) or modifiers:
        raise ValueError("Wix price updates currently support default variants without options or modifiers")
    variants = (product.get("variantsInfo") or {}).get("variants")
    if not isinstance(variants, list) or len(variants) != 1:
        raise ValueError("Wix price updates require exactly one default variant")
    summary = (product.get("variantSummary") or {}).get("variantCount")
    if summary is not None and summary != 1:
        raise ValueError("Wix variant count disagrees with product data")
    variant = variants[0]
    if not isinstance(variant, dict) or str(variant.get("id") or "").lower() != variant_id:
        raise ValueError("Wix default variant ID does not match linked inventory")
    if variant.get("choices") not in (None, []):
        raise ValueError("Wix default variant unexpectedly has option choices")
    if str(variant.get("sku") or "").strip() != expected_sku:
        raise ValueError("Wix SKU no longer matches the linked listing")
    price = variant.get("price") or {}
    if not isinstance(price, dict):
        raise ValueError("Wix variant price is malformed")
    if price.get("compareAtPrice") is not None:
        raise ValueError("Wix variant has a compare-at promotional price; review manually")
    actual = price.get("actualPrice") or {}
    if not isinstance(actual, dict):
        raise ValueError("Wix actual price is malformed")
    amount = _cents(actual.get("amount"))
    return {
        "external_id": f"{product_id}:{variant_id}",
        "regular_price_cents": amount,
        "price_cents": amount,
        "currency": expected_currency,
        "product_id": product_id,
        "variant_id": variant_id,
        "revision": revision,
    }


def read_wix_workspace_price(
    workspace_id: uuid.UUID, *, external_id: str,
    expected_sku: str, expected_currency: str,
) -> dict[str, Any]:
    product_id, variant_id, sku = _identity(external_id, expected_sku)
    return _snapshot(
        hosted._credentials(workspace_id, Channel.WIX),
        product_id, variant_id, sku, expected_currency,
    )


def update_wix_workspace_price(
    workspace_id: uuid.UUID, *, external_id: str,
    expected_sku: str, expected_currency: str,
    old_price_cents: int, new_price_cents: int,
) -> dict[str, Any]:
    product_id, variant_id, sku = _identity(external_id, expected_sku)
    _money(old_price_cents)
    desired = _money(new_price_cents)
    values = hosted._credentials(workspace_id, Channel.WIX)
    before = _snapshot(values, product_id, variant_id, sku, expected_currency)
    if before["regular_price_cents"] != old_price_cents:
        raise ValueError("Wix price changed since the last check; check again")
    if new_price_cents == old_price_cents:
        return {
            "remote_verified": True, "already_complete": True,
            "price_cents": new_price_cents, "currency": expected_currency,
            "external_id": external_id,
        }
    # Catalog V3 requires the complete variants array when setting a variant
    # price, so only allow one default variant, retaining its identity, SKU
    # and empty options and choices. The revision protects concurrent edits.
    response = requests.patch(
        hosted.WIX_API_BASE + "/stores/v3/products/" + product_id,
        headers=hosted._wix_headers(values),
        json={
            "product": {
                "id": product_id,
                "revision": before["revision"],
                "options": [],
                "variantsInfo": {
                    "variants": [{
                        "id": variant_id,
                        "sku": sku,
                        "choices": [],
                        "price": {"actualPrice": {"amount": desired}},
                    }],
                },
            },
        },
        timeout=45,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Wix price update failed ({response.status_code})")
    after = _snapshot(values, product_id, variant_id, sku, expected_currency)
    if (
        after["regular_price_cents"] != new_price_cents
        or after["revision"] == before["revision"]
        or after["external_id"] != before["external_id"]
    ):
        raise RuntimeError("Wix price readback differs; inspect the store before another write")
    return {
        "remote_verified": True, "already_complete": False,
        "price_cents": new_price_cents, "currency": expected_currency,
        "external_id": external_id,
    }
