"""Explicit Shopify base-price updates for one linked ProductVariant.

The check reads shop currency, variant identity, SKU, product identity, price
and compare-at price. The mutation changes only the variant price, then an
independent readback confirms the result. Nothing retries an uncertain write.
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal, InvalidOperation

from app.constants import Channel
from app.connectors import hosted
def _money(cents: int) -> str:
    if type(cents) is not int or not 0 < cents <= 2000000:
        raise ValueError("Price must be between 0.01 and 20000.00")
    return f"{cents // 100}.{cents % 100:02d}"


def _cents(value: object) -> int:
    raw = str(value if value is not None else "").strip()
    if not raw:
        raise ValueError("Shopify variant has no base price")
    try:
        number = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("Shopify returned an invalid base price") from exc
    if (not number.is_finite() or not 0 < number <= 20000
            or number.as_tuple().exponent < -2):
        raise ValueError("Shopify base price is outside the supported two-decimal range")
    return int(number * 100)


SHOPIFY_PRICE_QUERY = """
query ResellerVariantPrice($variantId: ID!) {
  shop { currencyCode }
  productVariant(id: $variantId) {
    id
    sku
    price
    compareAtPrice
    product { id status }
  }
}
"""

SHOPIFY_PRICE_MUTATION = """
mutation ResellerVariantBasePrice($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    product { id }
    productVariants { id price compareAtPrice }
    userErrors { field message }
  }
}
"""


def _snapshot(
    values: dict[str, str], external_id: str, expected_sku: str,
    expected_currency: str,
) -> dict:
    if not re.fullmatch(r"gid://shopify/ProductVariant/[1-9][0-9]*", str(external_id or "")):
        raise ValueError("A linked Shopify ProductVariant ID is required")
    if not str(expected_sku or "").strip():
        raise ValueError("A confirmed Shopify variant SKU is required")
    if not re.fullmatch(r"[A-Z]{3}", str(expected_currency or "")):
        raise ValueError("Set the physical item's three-letter currency first")
    data = hosted._shopify_graphql(
        values, SHOPIFY_PRICE_QUERY, variables={"variantId": external_id},
    )
    variant = data.get("productVariant")
    if not isinstance(variant, dict) or str(variant.get("id") or "") != external_id:
        raise ValueError("The linked Shopify product variant was not found")
    if str(variant.get("sku") or "").strip() != str(expected_sku).strip():
        raise ValueError("Shopify variant SKU differs from the linked listing")
    shop_currency = str((data.get("shop") or {}).get("currencyCode") or "").upper()
    if shop_currency != expected_currency:
        raise ValueError("Shopify base currency differs from the physical item's currency")
    configured_currency = str(values.get("currency") or "").upper()
    if configured_currency and configured_currency != expected_currency:
        raise ValueError("Configured Shopify currency differs from the physical item's currency")
    product = variant.get("product") or {}
    product_id = str(product.get("id") or "")
    if not re.fullmatch(r"gid://shopify/Product/[1-9][0-9]*", product_id):
        raise ValueError("Shopify did not return a valid parent product ID")
    if str(product.get("status") or "").upper() != "ACTIVE":
        raise ValueError("Shopify product is not active; review its price in Shopify")
    # Any compare-at price can encode a promotion. Refuse to overwrite only
    # the base price while leaving such a promotion inconsistent.
    if variant.get("compareAtPrice") is not None:
        raise ValueError("Shopify variant has a compare-at price; review promotion in Shopify")
    price = _cents(variant.get("price"))
    if price <= 0:
        raise ValueError("Shopify variant price must be positive")
    return {
        "external_id": external_id,
        "product_id": product_id,
        "regular_price_cents": price,
        "price_cents": price,
        "currency": shop_currency,
    }


def read_shopify_workspace_price(
    workspace_id: uuid.UUID, *,
    external_id: str, expected_sku: str, expected_currency: str,
) -> dict:
    """Read the default Shopify shop-currency variant price, not market overrides."""
    if not re.fullmatch(r"gid://shopify/ProductVariant/[1-9][0-9]*", str(external_id or "")):
        raise ValueError("A linked Shopify ProductVariant ID is required")
    values = hosted._credentials(workspace_id, Channel.SHOPIFY)
    return _snapshot(values, external_id, expected_sku, expected_currency)


def update_shopify_workspace_price(
    workspace_id: uuid.UUID, *, external_id: str, expected_sku: str,
    expected_currency: str, old_price_cents: int, new_price_cents: int,
) -> dict:
    """Preflight and verify a price-only mutation. Never retry failed mutations.

    Shopify's productVariantsBulkUpdate is not a compare-and-set operation;
    a remote concurrent price edit between read and write remains possible.
    """
    _money(old_price_cents)
    desired = _money(new_price_cents)
    values = hosted._credentials(workspace_id, Channel.SHOPIFY)
    before = _snapshot(values, external_id, expected_sku, expected_currency)
    if before["regular_price_cents"] != old_price_cents:
        raise ValueError("Shopify price changed since the last check; check again")
    if old_price_cents == new_price_cents:
        return {
            "remote_verified": True, "already_complete": True,
            "price_cents": new_price_cents, "currency": expected_currency,
            "external_id": external_id,
        }
    data = hosted._shopify_graphql(
        values, SHOPIFY_PRICE_MUTATION,
        variables={
            "productId": before["product_id"],
            "variants": [{"id": external_id, "price": desired}],
        },
    )
    result = data.get("productVariantsBulkUpdate") or {}
    if result.get("userErrors"):
        # Do not expose arbitrary API validation payloads; they may contain
        # supplier or product information, secrets or operational details.
        raise RuntimeError("Shopify rejected the variant price update")
    product = result.get("product") or {}
    variants = result.get("productVariants") or []
    if (product.get("id") != before["product_id"]
            or len(variants) != 1
            or not isinstance(variants[0], dict)
            or variants[0].get("id") != external_id):
        raise RuntimeError("Shopify price update response did not confirm exact variant identity")
    after = _snapshot(values, external_id, expected_sku, expected_currency)
    if (after["product_id"] != before["product_id"]
            or after["regular_price_cents"] != new_price_cents):
        raise RuntimeError("Shopify base price differs after the write; check the store")
    return {
        "remote_verified": True, "already_complete": False,
        "price_cents": new_price_cents, "currency": expected_currency,
        "external_id": external_id,
    }
