"""Explicit, readback-verified Shopify sold-out closure.

A variant identifies the physical copy, but Shopify publication is a PRODUCT
status. Refuse multi-variant products rather than unpublishing unrelated SKUs.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from app.constants import Channel
from app.connectors import hosted
from app.listing_content import fingerprint


SHOPIFY_CLOSE_QUERY = """
query ResellerSoldOutProduct($variantId: ID!) {
  productVariant(id: $variantId) {
    id
    sku
    product {
      id
      title
      status
      updatedAt
      variants(first: 2) {
        nodes { id sku }
        pageInfo { hasNextPage }
      }
    }
  }
}
"""

SHOPIFY_CLOSE_MUTATION = """
mutation ResellerUnpublishSoldOutProduct($product: ProductUpdateInput!) {
  productUpdate(product: $product) {
    product { id status }
    userErrors { field message }
  }
}
"""

VARIANT_RE = re.compile(r"gid://shopify/ProductVariant/[1-9][0-9]*")
PRODUCT_RE = re.compile(r"gid://shopify/Product/[1-9][0-9]*")


def _snapshot(values: dict[str, str], external_id: str, expected_sku: str) -> dict[str, Any]:
    if not VARIANT_RE.fullmatch(str(external_id or "")):
        raise ValueError("A confirmed Shopify ProductVariant ID is required")
    if not str(expected_sku or "").strip():
        raise ValueError("A confirmed Shopify variant SKU is required")
    data = hosted._shopify_graphql(
        values, SHOPIFY_CLOSE_QUERY, variables={"variantId": external_id},
    )
    variant = data.get("productVariant")
    if not isinstance(variant, dict) or variant.get("id") != external_id:
        raise ValueError("The linked Shopify variant was not found")
    if str(variant.get("sku") or "").strip() != str(expected_sku).strip():
        raise ValueError("Shopify SKU differs from the linked inventory listing")
    product = variant.get("product")
    if not isinstance(product, dict) or not PRODUCT_RE.fullmatch(str(product.get("id") or "")):
        raise ValueError("Shopify did not return a valid parent product")
    variants = product.get("variants")
    if not isinstance(variants, dict):
        raise ValueError("Shopify did not confirm the product's variant count")
    nodes = variants.get("nodes")
    if (not isinstance(nodes, list) or len(nodes) != 1
            or not isinstance(nodes[0], dict)
            or nodes[0].get("id") != external_id
            or str(nodes[0].get("sku") or "").strip() != str(expected_sku).strip()
            or not isinstance(variants.get("pageInfo"), dict)
            or variants["pageInfo"].get("hasNextPage") is not False):
        raise ValueError("Shopify product has multiple or unverified variants; close it manually")
    status = str(product.get("status") or "").upper()
    if status not in {"ACTIVE", "DRAFT", "ARCHIVED", "UNLISTED"}:
        raise ValueError("Shopify returned an unsupported product status")
    identity = {
        "variant_id": external_id, "sku": expected_sku,
        "product_id": product["id"], "status": status,
        "title": str(product.get("title") or ""),
        "updated_at": str(product.get("updatedAt") or ""),
        "variant_ids": [node["id"] for node in nodes],
    }
    return {
        "external_id": external_id, "sku": expected_sku,
        "product_id": product["id"],
        "status": status.lower(),
        "fingerprint": fingerprint(identity),
        "can_unpublish": status == "ACTIVE",
    }


def read_shopify_workspace_publication(
    workspace_id: uuid.UUID, *, external_id: str, expected_sku: str,
) -> dict[str, Any]:
    if not VARIANT_RE.fullmatch(str(external_id or "")):
        raise ValueError("A linked Shopify ProductVariant ID is required")
    return _snapshot(
        hosted._credentials(workspace_id, Channel.SHOPIFY), external_id, expected_sku,
    )


def unpublish_shopify_workspace_product(
    workspace_id: uuid.UUID, *, external_id: str, expected_sku: str,
    expected_fingerprint: str,
) -> dict[str, Any]:
    """One status-only productUpdate, with exact single-variant preflight and readback."""
    if not VARIANT_RE.fullmatch(str(external_id or "")):
        raise ValueError("A linked Shopify ProductVariant ID is required")
    values = hosted._credentials(workspace_id, Channel.SHOPIFY)
    before = _snapshot(values, external_id, expected_sku)
    if before["fingerprint"] != expected_fingerprint:
        raise ValueError("Shopify product changed since inspection; check again")
    if before["status"] != "active":
        raise ValueError("Only active Shopify products can be unpublished")
    data = hosted._shopify_graphql(
        values, SHOPIFY_CLOSE_MUTATION,
        variables={"product": {"id": before["product_id"], "status": "DRAFT"}},
    )
    result = data.get("productUpdate")
    if not isinstance(result, dict) or result.get("userErrors"):
        raise RuntimeError("Shopify did not accept the product status change")
    product = result.get("product")
    if (not isinstance(product, dict)
            or product.get("id") != before["product_id"]
            or str(product.get("status") or "").upper() != "DRAFT"):
        raise RuntimeError("Shopify response did not confirm the expected draft product")
    after = _snapshot(values, external_id, expected_sku)
    if after["product_id"] != before["product_id"] or after["status"] != "draft":
        raise RuntimeError("Shopify product is not a draft after unpublishing")
    return {
        "remote_verified": True, "external_id": external_id,
        "status": "draft", "already_complete": False,
    }
