"""Explicit WooCommerce regular-price check and update.

Only a confirmed single linked product or variation can be updated. Promotional
prices, scheduled sales and ambiguous currency/price data require manual review.
The write touches only regular_price, never stock, visibility or sale fields.
"""
from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from curl_cffi import requests

from app.constants import Channel
from app.connectors import hosted


def _cents(value: object) -> int:
    raw = str(value if value is not None else "").strip()
    if not raw:
        raise ValueError("WooCommerce has no regular price; edit it in the store")
    try:
        number = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("WooCommerce returned an invalid price") from exc
    if (not number.is_finite() or number < 0 or number > 20000
            or number.as_tuple().exponent < -2):
        raise ValueError("WooCommerce price is outside the supported two-decimal range")
    return int(number * 100)


def _money(cents: int) -> str:
    if type(cents) is not int or not 0 <= cents <= 2000000:
        raise ValueError("Price must be a non-negative amount up to 20000.00")
    return f"{cents // 100}.{cents % 100:02d}"


def _snapshot(
    values: dict[str, str], external_id: str, expected_sku: str,
    expected_currency: str,
) -> dict:
    if not str(expected_sku or "").strip():
        raise ValueError("WooCommerce price changes require a confirmed linked SKU")
    currency = str(values.get("currency") or "").strip().upper()
    if not currency or currency != expected_currency:
        raise ValueError("Item currency and configured WooCommerce store currency differ")
    path, remote, _parent_status, _variation = hosted._woocommerce_stock_record(
        values, external_id, expected_sku,
    )
    if (remote.get("on_sale") is True
            or str(remote.get("sale_price") or "").strip()
            or any(remote.get(key) for key in (
                "date_on_sale_from", "date_on_sale_from_gmt",
                "date_on_sale_to", "date_on_sale_to_gmt",
            ))):
        raise ValueError("WooCommerce has a sale price or promotion scheduled; review it in the store")
    regular = _cents(remote.get("regular_price"))
    price = _cents(remote.get("price"))
    if price != regular:
        raise ValueError("WooCommerce effective price differs from its regular price")
    return {
        "external_id": external_id,
        "sku": expected_sku,
        "currency": currency,
        "regular_price_cents": regular,
        "price_cents": price,
        "path": path,
    }


def read_woocommerce_workspace_price(
    workspace_id: uuid.UUID, *,
    external_id: str, expected_sku: str, expected_currency: str,
) -> dict:
    hosted._woocommerce_stock_path(external_id)
    if not str(expected_currency or "").strip():
        raise ValueError("Set the inventory item's currency before checking store prices")
    values = hosted._credentials(workspace_id, Channel.WOOCOMMERCE)
    return _snapshot(values, external_id, expected_sku, expected_currency)


def update_woocommerce_workspace_price(
    workspace_id: uuid.UUID, *, external_id: str, expected_sku: str,
    expected_currency: str, old_price_cents: int, new_price_cents: int,
) -> dict:
    """Preflight exact price; PUT only the regular-price field; verify by GET.

    WooCommerce REST has no native compare-and-set for product prices. We
    detect preflight drift but cannot eliminate a concurrent edit after GET.
    The operation must not be replayed after an ambiguous response.
    """
    hosted._woocommerce_stock_path(external_id)
    _money(old_price_cents)
    desired = _money(new_price_cents)
    values = hosted._credentials(workspace_id, Channel.WOOCOMMERCE)
    before = _snapshot(values, external_id, expected_sku, expected_currency)
    if before["regular_price_cents"] != old_price_cents:
        raise ValueError("WooCommerce price changed after the last check; check again")
    if old_price_cents == new_price_cents:
        return {
            "remote_verified": True, "already_complete": True,
            "price_cents": new_price_cents, "currency": expected_currency,
            "external_id": external_id,
        }
    response = requests.put(
        hosted._woocommerce_base(values) + "/wp-json/wc/v3/" + before["path"],
        headers={**hosted._woocommerce_headers(values), "Content-Type": "application/json"},
        json={"regular_price": desired},
        timeout=45,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"WooCommerce price update failed ({response.status_code})")
    after = _snapshot(values, external_id, expected_sku, expected_currency)
    if after["regular_price_cents"] != new_price_cents or after["path"] != before["path"]:
        raise RuntimeError("WooCommerce price differs after update; review the store before another write")
    return {
        "remote_verified": True, "already_complete": False,
        "price_cents": new_price_cents, "currency": expected_currency,
        "external_id": external_id,
    }
