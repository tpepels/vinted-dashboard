"""Workspace-native marketplace connector adapters.

The legacy personal installation still has env-backed helpers in app.channels.
This module is the hosted/multi-tenant equivalent: credentials are loaded from
the encrypted ConnectorCredential row for one workspace and all resulting
inventory is written directly to the workspace/master-inventory schema.
"""

from __future__ import annotations

import base64
import csv
import ftplib
import io
import ipaddress
import os
import re
import socket
import time
import uuid
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from curl_cffi import requests
from sqlalchemy import select

from app import db, models
from app.constants import Channel, ChannelAccountStatus, ListingStatus, SyncRunStatus
from app.crypto import decrypt_json
from app.product_models import ConnectorCredential
from app.workspace_bootstrap import get_or_create_channel_account
from app.connectors.workspace_sync import (
    record_workspace_channel_orders,
    record_workspace_channel_snapshot,
)


def _credentials(workspace_id: uuid.UUID, channel: str) -> dict[str, str]:
    with db.session_scope() as session:
        row = session.execute(
            select(ConnectorCredential).where(
                ConnectorCredential.workspace_id == workspace_id,
                ConnectorCredential.channel == channel,
            )
        ).scalar_one_or_none()
        if row is None:
            raise RuntimeError(f"{channel} credentials are not configured")
        values = decrypt_json(row.encrypted_payload)
    return {str(k): str(v) for k, v in values.items() if str(v).strip()}


def has_credentials(workspace_id: uuid.UUID, channel: str) -> bool:
    with db.session_scope() as session:
        return session.execute(
            select(ConnectorCredential.id).where(
                ConnectorCredential.workspace_id == workspace_id,
                ConnectorCredential.channel == channel,
            )
        ).scalar_one_or_none() is not None


def _money(value: Any) -> int | None:
    if value in (None, ""):
        return None
    match = re.search(r"([0-9]+(?:[.,][0-9]{1,2})?)", str(value))
    if not match:
        return None
    try:
        return round(float(match.group(1).replace(",", ".")) * 100)
    except ValueError:
        return None


def _int(value: Any, default: int | None = None) -> int | None:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _ebay_access_token(values: dict[str, str]) -> str:
    direct = values.get("oauth_token", "").strip()
    if direct:
        return direct
    client_id = values.get("client_id", "").strip()
    client_secret = values.get("client_secret", "").strip()
    refresh_token = values.get("refresh_token", "").strip()
    if not (client_id and client_secret and refresh_token):
        raise RuntimeError(
            "eBay needs oauth_token, or client_id + client_secret + refresh_token"
        )
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    response = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=20,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"eBay OAuth refresh failed ({response.status_code})")
    payload = response.json()
    token = str(payload.get("access_token") or "")
    if not token:
        raise RuntimeError("eBay OAuth refresh returned no access token")
    return token


def _fetch_ebay_active(values: dict[str, str]) -> list[dict[str, Any]]:
    token = _ebay_access_token(values)
    site_id = values.get("site_id", "0").strip() or "0"
    compatibility = values.get("compatibility_level", "1477").strip() or "1477"
    items: list[dict[str, Any]] = []
    page = 1
    while page <= 125:
        body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetMyeBaySellingRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <ErrorLanguage>en_US</ErrorLanguage>
  <WarningLevel>High</WarningLevel>
  <ActiveList>
    <Include>true</Include>
    <Pagination>
      <EntriesPerPage>200</EntriesPerPage>
      <PageNumber>{page}</PageNumber>
    </Pagination>
  </ActiveList>
</GetMyeBaySellingRequest>"""
        response = requests.post(
            "https://api.ebay.com/ws/api.dll",
            headers={
                "Content-Type": "text/xml",
                "X-EBAY-API-CALL-NAME": "GetMyeBaySelling",
                "X-EBAY-API-COMPATIBILITY-LEVEL": compatibility,
                "X-EBAY-API-SITEID": site_id,
                "X-EBAY-API-IAF-TOKEN": token,
            },
            data=body.encode("utf-8"),
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"eBay inventory request failed ({response.status_code})")
        root = ET.fromstring(response.content)
        ns = {"e": "urn:ebay:apis:eBLBaseComponents"}
        ack = root.findtext("e:Ack", namespaces=ns)
        if ack not in {"Success", "Warning"}:
            message = root.findtext(".//e:LongMessage", namespaces=ns) or "Unknown eBay API error"
            raise RuntimeError(message)
        for node in root.findall(".//e:ActiveList/e:ItemArray/e:Item", ns):
            item_id = node.findtext("e:ItemID", namespaces=ns)
            if not item_id:
                continue
            price_node = node.find("e:SellingStatus/e:CurrentPrice", ns)
            if price_node is None:
                price_node = node.find("e:StartPrice", ns)
            quantity = _int(node.findtext("e:QuantityAvailable", namespaces=ns))
            if quantity is None:
                quantity = _int(node.findtext("e:Quantity", namespaces=ns), 1)
            items.append(
                {
                    "source_id": item_id,
                    "sku": node.findtext("e:SKU", namespaces=ns),
                    "title": node.findtext("e:Title", namespaces=ns) or "Untitled",
                    "status": ListingStatus.ACTIVE,
                    "quantity": quantity,
                    "price_cents": _money(price_node.text if price_node is not None else None),
                    "currency": price_node.attrib.get("currencyID") if price_node is not None else None,
                    "url": node.findtext("e:ListingDetails/e:ViewItemURL", namespaces=ns),
                }
            )
        total_pages = _int(
            root.findtext(".//e:ActiveList/e:PaginationResult/e:TotalNumberOfPages", namespaces=ns),
            1,
        ) or 1
        if page >= total_pages:
            break
        page += 1
    return items


def sync_ebay_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id, Channel.EBAY)
    items = _fetch_ebay_active(values)
    synced_at = datetime.now(timezone.utc)
    record_workspace_channel_snapshot(
        workspace_id,
        Channel.EBAY,
        items,
        synced_at=synced_at,
        full_snapshot=True,
        note="eBay GetMyeBaySelling",
    )
    return {"source": Channel.EBAY, "items": len(items), "active": len(items)}


def _remote_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _money_resource(value: Any) -> tuple[int | None, str | None]:
    if isinstance(value, dict):
        amount = value.get("amount")
        divisor = _int(value.get("divisor"), 100) or 100
        try:
            cents = round(int(amount) * 100 / divisor) if amount is not None else None
        except (TypeError, ValueError, ZeroDivisionError):
            cents = None
        currency = value.get("currency_code") or value.get("currency")
        return cents, str(currency) if currency else None
    return _money(value), None


def _etsy_access_token(values: dict[str, str]) -> str:
    cached = values.get("_runtime_oauth_token", "").strip()
    if cached:
        return cached
    keystring = values.get("keystring", "").strip()
    refresh_token = values.get("refresh_token", "").strip()
    if refresh_token:
        response = requests.post(
            "https://api.etsy.com/v3/public/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "client_id": keystring,
                "refresh_token": refresh_token,
            },
            timeout=20,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Etsy OAuth refresh failed ({response.status_code})")
        token = str((response.json() or {}).get("access_token") or "").strip()
        if not token:
            raise RuntimeError("Etsy OAuth refresh returned no access token")
        values["_runtime_oauth_token"] = token
        return token
    token = values.get("oauth_token", "").strip()
    if not token:
        raise RuntimeError("Etsy needs oauth_token or refresh_token")
    return token


def exchange_etsy_authorization_code(
    values: dict[str, str],
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> dict[str, str]:
    keystring = values.get("keystring", "").strip()
    if not keystring or not code.strip() or not code_verifier.strip() or not redirect_uri.strip():
        raise RuntimeError("Etsy OAuth exchange is missing required values")
    response = requests.post(
        "https://api.etsy.com/v3/public/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "authorization_code",
            "client_id": keystring,
            "redirect_uri": redirect_uri,
            "code": code,
            "code_verifier": code_verifier,
        },
        timeout=20,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Etsy OAuth exchange failed ({response.status_code})")
    payload = response.json() or {}
    access_token = str(payload.get("access_token") or "").strip()
    refresh_token = str(payload.get("refresh_token") or "").strip()
    if not access_token or not refresh_token:
        raise RuntimeError("Etsy OAuth exchange returned incomplete token data")
    result = {
        "oauth_token": access_token,
        "refresh_token": refresh_token,
    }
    scope = str(payload.get("scope") or "").strip()
    if scope:
        result["oauth_scope"] = scope
    expires_in = payload.get("expires_in")
    if expires_in not in (None, ""):
        result["oauth_expires_in"] = str(expires_in)
    return result


def _etsy_headers(values: dict[str, str]) -> dict[str, str]:
    keystring = values.get("keystring", "").strip()
    shared_secret = values.get("shared_secret", "").strip()
    if not keystring or not shared_secret:
        raise RuntimeError("Etsy needs keystring and shared_secret")
    return {
        "x-api-key": f"{keystring}:{shared_secret}",
        "Authorization": f"Bearer {_etsy_access_token(values)}",
        "Accept": "application/json",
    }


def _etsy_get(values: dict[str, str], path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.get(
        "https://api.etsy.com/v3/application/" + path.lstrip("/"),
        headers=_etsy_headers(values),
        params=params or {},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Etsy API request failed ({response.status_code})")
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _fetch_etsy_active(values: dict[str, str], *, max_pages: int = 100) -> list[dict[str, Any]]:
    shop_id = values.get("shop_id", "").strip()
    if not shop_id:
        raise RuntimeError("Etsy needs shop_id")
    items: list[dict[str, Any]] = []
    offset = 0
    for _page in range(max_pages):
        payload = _etsy_get(
            values,
            f"shops/{shop_id}/listings",
            params={"state": "active", "limit": 100, "offset": offset},
        )
        rows = list(payload.get("results") or [])
        for raw in rows:
            listing_id = raw.get("listing_id")
            if listing_id in (None, ""):
                continue
            price_cents, currency = _money_resource(raw.get("price"))
            skus = [str(value).strip() for value in (raw.get("skus") or []) if str(value).strip()]
            materials = [str(value) for value in (raw.get("materials") or []) if str(value).strip()]
            created = _remote_datetime(
                raw.get("creation_timestamp")
                or raw.get("created_timestamp")
                or raw.get("original_creation_timestamp")
            )
            items.append(
                {
                    "source_id": str(listing_id),
                    "sku": skus[0] if len(skus) == 1 else None,
                    "title": raw.get("title") or "Untitled",
                    "description": raw.get("description"),
                    "status": ListingStatus.ACTIVE,
                    "quantity": _int(raw.get("quantity"), 1),
                    "price_cents": price_cents,
                    "currency": currency or values.get("currency", "EUR"),
                    "url": raw.get("url"),
                    "listed_at": created.isoformat() if created else None,
                    "material": ", ".join(materials) if materials else None,
                    "tags": raw.get("tags") or [],
                    "taxonomy_id": raw.get("taxonomy_id"),
                    "product_type": raw.get("listing_type"),
                    "attributes": {
                        "who_made": raw.get("who_made"),
                        "when_made": raw.get("when_made"),
                        "is_supply": raw.get("is_supply"),
                        "is_customizable": raw.get("is_customizable"),
                        "is_personalizable": raw.get("is_personalizable"),
                    },
                }
            )
        if len(rows) < 100:
            break
        offset += len(rows)
    return items


def _fetch_etsy_orders(values: dict[str, str]) -> list[dict[str, Any]]:
    shop_id = values.get("shop_id", "").strip()
    if not shop_id:
        raise RuntimeError("Etsy needs shop_id")
    days = max(1, min(_int(values.get("order_days"), 365) or 365, 3650))
    min_created = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    orders: list[dict[str, Any]] = []
    offset = 0
    while offset < 5000:
        payload = _etsy_get(
            values,
            f"shops/{shop_id}/receipts",
            params={
                "limit": 100,
                "offset": offset,
                "min_created": min_created,
            },
        )
        receipts = list(payload.get("results") or [])
        for receipt in receipts:
            if receipt.get("was_paid") is False:
                continue
            receipt_id = receipt.get("receipt_id")
            transactions = list(receipt.get("transactions") or [])
            if not transactions and receipt_id not in (None, ""):
                tx_payload = _etsy_get(
                    values,
                    f"shops/{shop_id}/receipts/{receipt_id}/transactions",
                    params={"limit": 100, "offset": 0},
                )
                transactions = list(tx_payload.get("results") or [])
            cancelled = bool(receipt.get("was_canceled"))
            shipped = bool(receipt.get("was_shipped"))
            paid = bool(receipt.get("was_paid"))
            status = "cancelled" if cancelled else ("completed" if shipped else ("paid" if paid else "open"))
            buyer = str(receipt.get("name") or "").strip() or None
            receipt_created = _remote_datetime(
                receipt.get("created_timestamp") or receipt.get("create_timestamp")
            )
            for tx in transactions:
                transaction_id = tx.get("transaction_id")
                if transaction_id in (None, ""):
                    continue
                unit_cents, currency = _money_resource(tx.get("price"))
                quantity = max(1, _int(tx.get("quantity"), 1) or 1)
                total_cents = unit_cents * quantity if unit_cents is not None else None
                tx_created = _remote_datetime(
                    tx.get("created_timestamp") or tx.get("create_timestamp")
                )
                orders.append(
                    {
                        "external_order_id": f"{receipt_id}:{transaction_id}",
                        "listing_external_id": str(tx.get("listing_id") or ""),
                        "sku": tx.get("sku"),
                        "title": tx.get("title") or "Etsy sale",
                        "counterparty": buyer,
                        "total_cents": total_cents,
                        "currency": currency or receipt.get("currency_code") or values.get("currency", "EUR"),
                        "status": status,
                        "lifecycle_status": status,
                        "is_closed": cancelled or shipped,
                        "occurred_at": (tx_created or receipt_created).isoformat()
                        if (tx_created or receipt_created)
                        else None,
                        "quantity": quantity,
                        "extra": {
                            "receipt_id": receipt_id,
                            "transaction_id": transaction_id,
                        },
                    }
                )
        if len(receipts) < 100:
            break
        offset += len(receipts)
    return orders


def test_etsy_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id, Channel.ETSY)
    shop_id = values.get("shop_id", "").strip()
    listings = _etsy_get(
        values,
        f"shops/{shop_id}/listings",
        params={"state": "active", "limit": 1, "offset": 0},
    )
    receipts = _etsy_get(
        values,
        f"shops/{shop_id}/receipts",
        params={"limit": 1, "offset": 0},
    )
    return {
        "ok": True,
        "detail": "Etsy listing and transaction scopes are available.",
        "listing_count": listings.get("count"),
        "receipt_count": receipts.get("count"),
    }


def sync_etsy_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id, Channel.ETSY)
    items = _fetch_etsy_active(values)
    orders = _fetch_etsy_orders(values)
    synced_at = datetime.now(timezone.utc)
    listing_result = record_workspace_channel_snapshot(
        workspace_id,
        Channel.ETSY,
        items,
        synced_at=synced_at,
        full_snapshot=True,
        note="Etsy Open API v3",
    )
    order_result = record_workspace_channel_orders(
        workspace_id,
        Channel.ETSY,
        orders,
        synced_at=synced_at,
    )
    return {
        "source": Channel.ETSY,
        **listing_result,
        **order_result,
    }


def _public_https_base(raw_value: str, *, label: str) -> str:
    raw = str(raw_value or "").strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise RuntimeError(f"{label} must use a public HTTPS URL")
    if parsed.query or parsed.fragment:
        raise RuntimeError(f"{label} must not include a query or fragment")
    hostname = parsed.hostname.casefold()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise RuntimeError(f"{label} must use a public hostname")
    try:
        literal = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise RuntimeError(f"{label} must not use a private or local IP address")
    try:
        addresses = {
            address
            for family, _socktype, _proto, _canonname, sockaddr in socket.getaddrinfo(
                hostname, parsed.port or 443, type=socket.SOCK_STREAM
            )
            for address in [sockaddr[0]]
        }
    except OSError as exc:
        raise RuntimeError(f"{label} hostname could not be resolved") from exc
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if not ip.is_global:
            raise RuntimeError(f"{label} resolves to a private or local IP address")
    return raw


def _woocommerce_base(values: dict[str, str]) -> str:
    return _public_https_base(
        values.get("store_url", ""),
        label="WooCommerce store_url",
    )


def _woocommerce_headers(values: dict[str, str]) -> dict[str, str]:
    key = values.get("consumer_key", "").strip()
    secret = values.get("consumer_secret", "").strip()
    if not key or not secret:
        raise RuntimeError("WooCommerce needs consumer_key and consumer_secret")
    basic = base64.b64encode(f"{key}:{secret}".encode()).decode()
    return {
        "Authorization": f"Basic {basic}",
        "Accept": "application/json",
    }


def _woo_get(
    values: dict[str, str],
    path: str,
    *,
    params: dict[str, Any] | None = None,
) -> Any:
    response = requests.get(
        _woocommerce_base(values) + "/wp-json/wc/v3/" + path.lstrip("/"),
        headers=_woocommerce_headers(values),
        params=params or {},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"WooCommerce API request failed ({response.status_code})")
    return response.json()


def _woo_quantity(raw: dict[str, Any]) -> int:
    quantity = _int(raw.get("stock_quantity"))
    if quantity is not None:
        return max(0, quantity)
    return 0 if str(raw.get("stock_status") or "").lower() == "outofstock" else 1


def _woo_status(raw: dict[str, Any], parent_status: str = "publish") -> str:
    published = str(raw.get("status") or parent_status).lower() == "publish"
    in_stock = str(raw.get("stock_status") or "instock").lower() != "outofstock"
    return ListingStatus.ACTIVE if published and in_stock else ListingStatus.INACTIVE


def _woo_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    for row in raw.get("attributes") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        value = row.get("option")
        if value in (None, ""):
            value = row.get("options")
        attrs[name] = value
    return attrs


def _fetch_woocommerce_products(values: dict[str, str]) -> list[dict[str, Any]]:
    currency = values.get("currency", "EUR").strip().upper() or "EUR"
    result: list[dict[str, Any]] = []
    page = 1
    while page <= 100:
        products = _woo_get(
            values,
            "products",
            params={"per_page": 100, "page": page, "status": "publish"},
        )
        rows = products if isinstance(products, list) else []
        for product in rows:
            product_id = product.get("id")
            if product_id in (None, ""):
                continue
            categories = [
                str(row.get("name"))
                for row in (product.get("categories") or [])
                if isinstance(row, dict) and row.get("name")
            ]
            brands = [
                str(row.get("name"))
                for row in (product.get("brands") or [])
                if isinstance(row, dict) and row.get("name")
            ]
            common = {
                "description": product.get("description") or product.get("short_description"),
                "category": " / ".join(categories) if categories else None,
                "brand": brands[0] if brands else None,
                "tags": [
                    str(row.get("name"))
                    for row in (product.get("tags") or [])
                    if isinstance(row, dict) and row.get("name")
                ],
                "url": product.get("permalink"),
                "image_url": (
                    (product.get("images") or [{}])[0].get("src")
                    if product.get("images")
                    else None
                ),
                "product_type": product.get("type"),
            }
            created = _remote_datetime(product.get("date_created_gmt") or product.get("date_created"))
            if str(product.get("type") or "").lower() == "variable":
                variation_page = 1
                while variation_page <= 100:
                    variations = _woo_get(
                        values,
                        f"products/{product_id}/variations",
                        params={"per_page": 100, "page": variation_page},
                    )
                    variation_rows = variations if isinstance(variations, list) else []
                    for variation in variation_rows:
                        variation_id = variation.get("id")
                        if variation_id in (None, ""):
                            continue
                        attrs = _woo_metadata(variation)
                        suffix = " / ".join(str(value) for value in attrs.values() if value not in (None, "", []))
                        result.append(
                            {
                                "source_id": f"{product_id}:{variation_id}",
                                "sku": variation.get("sku") or None,
                                "title": product.get("name") + (f" - {suffix}" if suffix else ""),
                                "status": _woo_status(variation, str(product.get("status") or "publish")),
                                "quantity": _woo_quantity(variation),
                                "price_cents": _money(variation.get("price")),
                                "currency": currency,
                                "listed_at": (
                                    _remote_datetime(
                                        variation.get("date_created_gmt")
                                        or variation.get("date_created")
                                    )
                                    or created
                                ).isoformat()
                                if (
                                    _remote_datetime(
                                        variation.get("date_created_gmt")
                                        or variation.get("date_created")
                                    )
                                    or created
                                )
                                else None,
                                "attributes": attrs,
                                **common,
                            }
                        )
                    if len(variation_rows) < 100:
                        break
                    variation_page += 1
            else:
                result.append(
                    {
                        "source_id": str(product_id),
                        "sku": product.get("sku") or None,
                        "title": product.get("name") or "Untitled",
                        "status": _woo_status(product),
                        "quantity": _woo_quantity(product),
                        "price_cents": _money(product.get("price")),
                        "currency": currency,
                        "listed_at": created.isoformat() if created else None,
                        "attributes": _woo_metadata(product),
                        **common,
                    }
                )
        if len(rows) < 100:
            break
        page += 1
    return result


def _fetch_woocommerce_orders(values: dict[str, str]) -> list[dict[str, Any]]:
    days = max(1, min(_int(values.get("order_days"), 365) or 365, 3650))
    after = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    result: list[dict[str, Any]] = []
    page = 1
    while page <= 100:
        payload = _woo_get(
            values,
            "orders",
            params={"per_page": 100, "page": page, "after": after},
        )
        orders = payload if isinstance(payload, list) else []
        for order in orders:
            order_id = order.get("id")
            status = str(order.get("status") or "open").lower()
            currency = str(order.get("currency") or values.get("currency", "EUR"))
            billing = order.get("billing") or {}
            counterparty = " ".join(
                value for value in (
                    str(billing.get("first_name") or "").strip(),
                    str(billing.get("last_name") or "").strip(),
                ) if value
            ) or None
            occurred = _remote_datetime(order.get("date_created_gmt") or order.get("date_created"))
            for line in order.get("line_items") or []:
                line_id = line.get("id")
                if order_id in (None, "") or line_id in (None, ""):
                    continue
                product_id = line.get("product_id")
                variation_id = _int(line.get("variation_id"), 0) or 0
                listing_external_id = (
                    f"{product_id}:{variation_id}" if variation_id else str(product_id or "")
                )
                result.append(
                    {
                        "external_order_id": f"{order_id}:{line_id}",
                        "listing_external_id": listing_external_id,
                        "sku": line.get("sku"),
                        "title": line.get("name") or "WooCommerce sale",
                        "counterparty": counterparty,
                        "total_cents": _money(line.get("total")),
                        "currency": currency,
                        "status": status,
                        "lifecycle_status": status,
                        "is_closed": status in {"completed", "cancelled", "refunded", "failed", "trash"},
                        "occurred_at": occurred.isoformat() if occurred else None,
                        "quantity": _int(line.get("quantity"), 1) or 1,
                        "extra": {
                            "order_id": order_id,
                            "line_item_id": line_id,
                            "product_id": product_id,
                            "variation_id": variation_id,
                        },
                    }
                )
        if len(orders) < 100:
            break
        page += 1
    return result


def test_woocommerce_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id, Channel.WOOCOMMERCE)
    products = _woo_get(values, "products", params={"per_page": 1, "page": 1})
    orders = _woo_get(values, "orders", params={"per_page": 1, "page": 1})
    return {
        "ok": True,
        "detail": "WooCommerce products and orders are readable.",
        "products_visible": len(products) if isinstance(products, list) else 0,
        "orders_visible": len(orders) if isinstance(orders, list) else 0,
    }


def sync_woocommerce_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id, Channel.WOOCOMMERCE)
    items = _fetch_woocommerce_products(values)
    orders = _fetch_woocommerce_orders(values)
    synced_at = datetime.now(timezone.utc)
    listing_result = record_workspace_channel_snapshot(
        workspace_id,
        Channel.WOOCOMMERCE,
        items,
        synced_at=synced_at,
        full_snapshot=True,
        note="WooCommerce REST API v3",
    )
    order_result = record_workspace_channel_orders(
        workspace_id,
        Channel.WOOCOMMERCE,
        orders,
        synced_at=synced_at,
    )
    return {
        "source": Channel.WOOCOMMERCE,
        **listing_result,
        **order_result,
    }


def import_biblio_workspace(
    workspace_id: uuid.UUID,
    rows: list[dict[str, Any]],
    *,
    filename: str = "BIBLIO inventory",
) -> dict[str, Any]:
    if not rows:
        raise ValueError("No BIBLIO inventory rows could be read")
    active = sum(1 for row in rows if str(row.get("status") or "active").lower() == "active")
    record_workspace_channel_snapshot(
        workspace_id,
        Channel.BIBLIO,
        rows,
        synced_at=datetime.now(timezone.utc),
        full_snapshot=True,
        note=f"Imported {filename}",
    )
    return {"source": Channel.BIBLIO, "items": len(rows), "active": active}


def _biblio_rows(workspace_id: uuid.UUID) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with db.session_scope() as session:
        listings = session.execute(
            select(models.ChannelListing, models.InventoryItem)
            .join(models.InventoryItem, models.InventoryItem.id == models.ChannelListing.inventory_item_id)
            .where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.channel == Channel.BIBLIO,
            )
            .order_by(models.ChannelListing.external_id)
        ).all()
        latest = session.execute(
            select(models.ConnectorSyncRun)
            .where(
                models.ConnectorSyncRun.workspace_id == workspace_id,
                models.ConnectorSyncRun.channel == Channel.BIBLIO,
                models.ConnectorSyncRun.run_type == "ftp_sync",
                models.ConnectorSyncRun.status == SyncRunStatus.SUCCESS,
            )
            .order_by(models.ConnectorSyncRun.completed_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        cutoff = latest.completed_at if latest and latest.completed_at else None

        active: list[dict[str, Any]] = []
        deletes: list[dict[str, Any]] = []
        for listing, item in listings:
            attrs = dict(item.attributes or {})
            extra = dict(listing.extra or {})
            row = {
                "source_id": listing.external_id,
                "sku": listing.external_sku or item.sku,
                "title": listing.title or item.title,
                "author": extra.get("author") or attrs.get("author"),
                "description": extra.get("description") or attrs.get("description") or item.notes,
                "isbn": extra.get("isbn") or attrs.get("isbn"),
                "price_cents": listing.price_cents,
                "currency": listing.currency or item.currency or "EUR",
                "quantity": listing.quantity if listing.quantity is not None else item.quantity,
                "status": listing.status,
            }
            if listing.status == ListingStatus.ACTIVE and int(row["quantity"] or 0) > 0:
                active.append(row)
            elif cutoff is None or listing.updated_at > cutoff or listing.last_seen_at > cutoff:
                deletes.append(row)
    return active, deletes


def _missing_biblio(row: dict[str, Any]) -> list[str]:
    missing = []
    for key, label in (
        ("sku", "SKU"),
        ("author", "author"),
        ("title", "title"),
        ("description", "description"),
        ("price_cents", "price"),
    ):
        if row.get(key) in (None, ""):
            missing.append(label)
    return missing


def _biblio_tsv(rows: list[dict[str, Any]], *, sold: bool) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    writer.writerow(
        ["Book ID", "Author", "Title", "Description", "Price", "Status", "ISBN", "Quantity"]
    )
    for row in rows:
        price = (
            f"{int(row['price_cents']) / 100:.2f}"
            if row.get("price_cents") not in (None, "")
            else "0.00"
        )
        writer.writerow(
            [
                row.get("sku") or row.get("source_id") or "",
                row.get("author") or "",
                row.get("title") or "",
                row.get("description") or "",
                price,
                "sold" if sold else "for sale",
                row.get("isbn") or "",
                0 if sold else max(1, int(row.get("quantity") or 1)),
            ]
        )
    return output.getvalue().encode("utf-8")


def _record_biblio_run(
    workspace_id: uuid.UUID,
    *,
    status: str,
    started_at: datetime,
    active_count: int,
    delete_count: int,
    detail: dict[str, Any],
    error: str | None = None,
) -> None:
    with db.session_scope() as session:
        workspace = session.get(models.Workspace, workspace_id)
        if workspace is None:
            raise RuntimeError("Workspace does not exist")
        account, _ = get_or_create_channel_account(session, workspace, Channel.BIBLIO, {})
        account.last_synced_at = started_at
        account.status = (
            ChannelAccountStatus.CONNECTED
            if status == SyncRunStatus.SUCCESS
            else ChannelAccountStatus.ERROR
        )
        session.add(
            models.ConnectorSyncRun(
                workspace_id=workspace_id,
                channel_account_id=account.id,
                channel=Channel.BIBLIO,
                run_type="ftp_sync",
                status=status,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                active_count=active_count,
                delete_count=delete_count,
                detail=detail,
                error=error,
            )
        )


def test_biblio_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id, Channel.BIBLIO)
    host = values.get("host", "ftp.biblio.com").strip() or "ftp.biblio.com"
    username = values.get("username", "").strip()
    password = values.get("password", "").strip()
    if not username or not password:
        raise RuntimeError("BIBLIO needs username and password")
    ftp = ftplib.FTP()
    try:
        ftp.connect(host, timeout=_int(values.get("timeout_seconds"), 20) or 20)
        ftp.login(username, password)
        ftp.set_pasv(True)
        directory = values.get("directory", "").strip()
        if directory and directory not in {".", "./"}:
            ftp.cwd(directory)
        pwd = ftp.pwd()
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()
    return {"ok": True, "detail": f"Connected successfully; directory {pwd}"}


def sync_biblio_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id, Channel.BIBLIO)
    active, deletes = _biblio_rows(workspace_id)
    incomplete = [(row, _missing_biblio(row)) for row in active if _missing_biblio(row)]
    if incomplete:
        examples = ", ".join(
            f"{row.get('sku') or row.get('source_id')} ({'/'.join(missing)})"
            for row, missing in incomplete[:5]
        )
        raise RuntimeError(f"BIBLIO upload blocked: required fields are missing. Examples: {examples}")

    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    prefix = re.sub(
        r"[^A-Za-z0-9_-]+",
        "-",
        values.get("filename_prefix", "reseller-dashboard").strip() or "reseller-dashboard",
    ).strip("-")
    inventory_filename = f"{prefix}-{stamp}.txt" if active else None
    deletes_filename = f"{prefix}-{stamp}-deletes.txt" if deletes else None
    started = datetime.now(timezone.utc)
    if not inventory_filename and not deletes_filename:
        _record_biblio_run(
            workspace_id,
            status=SyncRunStatus.SUCCESS,
            started_at=started,
            active_count=0,
            delete_count=0,
            detail={"message": "Nothing to upload"},
        )
        return {"ok": True, "active": 0, "deletes": 0, "detail": "Nothing to upload"}

    host = values.get("host", "ftp.biblio.com").strip() or "ftp.biblio.com"
    username = values.get("username", "").strip()
    password = values.get("password", "").strip()
    if not username or not password:
        raise RuntimeError("BIBLIO needs username and password")

    try:
        ftp = ftplib.FTP()
        ftp.connect(host, timeout=_int(values.get("timeout_seconds"), 20) or 20)
        ftp.login(username, password)
        ftp.set_pasv(True)
        directory = values.get("directory", "").strip()
        if directory and directory not in {".", "./"}:
            ftp.cwd(directory)
        if inventory_filename:
            ftp.storbinary(f"STOR {inventory_filename}", io.BytesIO(_biblio_tsv(active, sold=False)))
        if deletes_filename:
            ftp.storbinary(f"STOR {deletes_filename}", io.BytesIO(_biblio_tsv(deletes, sold=True)))
        try:
            ftp.quit()
        except Exception:
            ftp.close()
    except Exception as exc:
        _record_biblio_run(
            workspace_id,
            status=SyncRunStatus.ERROR,
            started_at=started,
            active_count=len(active),
            delete_count=len(deletes),
            detail={
                "inventory_filename": inventory_filename,
                "deletes_filename": deletes_filename,
            },
            error=str(exc),
        )
        raise RuntimeError("BIBLIO FTP sync failed") from exc

    _record_biblio_run(
        workspace_id,
        status=SyncRunStatus.SUCCESS,
        started_at=started,
        active_count=len(active),
        delete_count=len(deletes),
        detail={
            "inventory_filename": inventory_filename,
            "deletes_filename": deletes_filename,
        },
    )
    return {
        "ok": True,
        "active": len(active),
        "deletes": len(deletes),
        "inventory_filename": inventory_filename,
        "deletes_filename": deletes_filename,
    }


def _workspace_or_env_ebay_values(workspace_id: uuid.UUID) -> dict[str, str]:
    if has_credentials(workspace_id, Channel.EBAY):
        return _credentials(workspace_id, Channel.EBAY)
    values = {
        "oauth_token": os.getenv("EBAY_OAUTH_TOKEN", "").strip(),
        "client_id": os.getenv("EBAY_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("EBAY_CLIENT_SECRET", "").strip(),
        "refresh_token": os.getenv("EBAY_REFRESH_TOKEN", "").strip(),
        "site_id": os.getenv("EBAY_SITE_ID", "0").strip() or "0",
        "compatibility_level": os.getenv("EBAY_COMPATIBILITY_LEVEL", "1477").strip() or "1477",
    }
    if not values["oauth_token"] and not (
        values["client_id"] and values["client_secret"] and values["refresh_token"]
    ):
        raise RuntimeError("eBay credentials are not configured")
    return values


def _workspace_or_env_biblio_values(workspace_id: uuid.UUID) -> dict[str, str]:
    if has_credentials(workspace_id, Channel.BIBLIO):
        return _credentials(workspace_id, Channel.BIBLIO)
    username = os.getenv("BIBLIO_FTP_USERNAME", "").strip()
    password = os.getenv("BIBLIO_FTP_PASSWORD", "").strip()
    if not username or not password:
        raise RuntimeError("BIBLIO credentials are not configured")
    return {
        "host": os.getenv("BIBLIO_FTP_HOST", "ftp.biblio.com").strip() or "ftp.biblio.com",
        "username": username,
        "password": password,
        "directory": os.getenv("BIBLIO_FTP_DIRECTORY", "").strip(),
        "timeout_seconds": os.getenv("BIBLIO_FTP_TIMEOUT_SECONDS", "20").strip() or "20",
        "filename_prefix": os.getenv("BIBLIO_FTP_FILENAME_PREFIX", "reseller-dashboard").strip()
        or "reseller-dashboard",
    }


def close_ebay_workspace_listing(workspace_id: uuid.UUID, external_id: str) -> dict[str, Any]:
    """End one eBay listing, idempotently.

    We first read active inventory. If the listing is already absent, the close
    is complete and no destructive API call is repeated.
    """
    values = _workspace_or_env_ebay_values(workspace_id)
    active = _fetch_ebay_active(values)
    if not any(str(row.get("source_id")) == str(external_id) for row in active):
        return {"remote": "already_ended", "external_id": str(external_id)}

    token = _ebay_access_token(values)
    site_id = values.get("site_id", "0").strip() or "0"
    compatibility = values.get("compatibility_level", "1477").strip() or "1477"
    item_id = escape(str(external_id))
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<EndItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <ItemID>{item_id}</ItemID>
  <EndingReason>NotAvailable</EndingReason>
</EndItemRequest>"""
    response = requests.post(
        "https://api.ebay.com/ws/api.dll",
        headers={
            "Content-Type": "text/xml",
            "X-EBAY-API-CALL-NAME": "EndItem",
            "X-EBAY-API-COMPATIBILITY-LEVEL": compatibility,
            "X-EBAY-API-SITEID": site_id,
            "X-EBAY-API-IAF-TOKEN": token,
        },
        data=body.encode("utf-8"),
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"eBay EndItem request failed ({response.status_code})")
    root = ET.fromstring(response.content)
    ns = {"e": "urn:ebay:apis:eBLBaseComponents"}
    ack = root.findtext("e:Ack", namespaces=ns)
    if ack not in {"Success", "Warning"}:
        message = root.findtext(".//e:LongMessage", namespaces=ns) or "Unknown eBay EndItem error"
        raise RuntimeError(message)
    return {"remote": "ended", "external_id": str(external_id)}


def _biblio_listing_row(workspace_id: uuid.UUID, listing_id: uuid.UUID) -> dict[str, Any]:
    with db.session_scope() as session:
        listing = session.get(models.ChannelListing, listing_id)
        if (
            listing is None
            or listing.workspace_id != workspace_id
            or listing.channel != Channel.BIBLIO
        ):
            raise RuntimeError("BIBLIO listing no longer exists")
        item = session.get(models.InventoryItem, listing.inventory_item_id)
        if item is None:
            raise RuntimeError("BIBLIO master inventory item no longer exists")
        attrs = dict(item.attributes or {})
        extra = dict(listing.extra or {})
        return {
            "source_id": listing.external_id,
            "sku": listing.external_sku or item.sku,
            "title": listing.title or item.title,
            "author": extra.get("author") or attrs.get("author"),
            "description": extra.get("description") or attrs.get("description") or item.notes,
            "isbn": extra.get("isbn") or attrs.get("isbn"),
            "price_cents": listing.price_cents,
            "currency": listing.currency or item.currency or "EUR",
            "quantity": 0,
            "status": ListingStatus.SOLD,
        }


def close_biblio_workspace_listing(
    workspace_id: uuid.UUID,
    listing_id: uuid.UUID,
) -> dict[str, Any]:
    """Upload one explicit BIBLIO delete record.

    This does not run a full inventory sync, so a sold-reconciliation action
    cannot accidentally publish unrelated inventory changes.
    """
    row = _biblio_listing_row(workspace_id, listing_id)
    values = _workspace_or_env_biblio_values(workspace_id)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    prefix = re.sub(
        r"[^A-Za-z0-9_-]+",
        "-",
        values.get("filename_prefix", "reseller-dashboard").strip() or "reseller-dashboard",
    ).strip("-")
    filename = f"{prefix}-{stamp}-deletes.txt"
    started = datetime.now(timezone.utc)

    try:
        ftp = ftplib.FTP()
        ftp.connect(
            values.get("host", "ftp.biblio.com").strip() or "ftp.biblio.com",
            timeout=_int(values.get("timeout_seconds"), 20) or 20,
        )
        ftp.login(values.get("username", ""), values.get("password", ""))
        ftp.set_pasv(True)
        directory = values.get("directory", "").strip()
        if directory and directory not in {".", "./"}:
            ftp.cwd(directory)
        ftp.storbinary(f"STOR {filename}", io.BytesIO(_biblio_tsv([row], sold=True)))
        try:
            ftp.quit()
        except Exception:
            ftp.close()
    except Exception as exc:
        _record_biblio_run(
            workspace_id,
            status=SyncRunStatus.ERROR,
            started_at=started,
            active_count=0,
            delete_count=1,
            detail={"deletes_filename": filename, "cross_channel": True},
            error=str(exc),
        )
        raise RuntimeError("BIBLIO delete upload failed") from exc

    _record_biblio_run(
        workspace_id,
        status=SyncRunStatus.SUCCESS,
        started_at=started,
        active_count=0,
        delete_count=1,
        detail={"deletes_filename": filename, "cross_channel": True},
    )
    return {
        "remote": "delete_uploaded",
        "external_id": str(row.get("source_id") or ""),
        "deletes_filename": filename,
    }
