"""Workspace-native marketplace connector adapters.

All connector reads and writes use the workspace/master-inventory schema.
Encrypted workspace credentials are preferred. The bootstrap personal workspace
may additionally use server environment credentials as a self-hosted fallback.
"""

from __future__ import annotations

import base64
import csv
import ftplib
import hashlib
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
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

from curl_cffi import requests
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select

from app import db, models
from app.constants import Channel, ChannelAccountStatus, ListingStatus, SyncRunStatus
from app.crypto import decrypt_json
from app.product_models import ConnectorCredential
from app.workspace_bootstrap import BOOTSTRAP_WORKSPACE_SLUG, get_or_create_channel_account
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


def _is_bootstrap_workspace(workspace_id: uuid.UUID) -> bool:
    with db.session_scope() as session:
        workspace = session.get(models.Workspace, workspace_id)
        return bool(workspace and workspace.slug == BOOTSTRAP_WORKSPACE_SLUG)


def biblio_configured(workspace_id: uuid.UUID) -> bool:
    if has_credentials(workspace_id, Channel.BIBLIO):
        return True
    if not _is_bootstrap_workspace(workspace_id):
        return False
    return bool(
        os.getenv("BIBLIO_FTP_USERNAME", "").strip()
        and os.getenv("BIBLIO_FTP_PASSWORD", "").strip()
    )


def ebay_configured(workspace_id: uuid.UUID) -> bool:
    if has_credentials(workspace_id, Channel.EBAY):
        return True
    if not _is_bootstrap_workspace(workspace_id):
        return False
    direct = os.getenv("EBAY_OAUTH_TOKEN", "").strip()
    refreshable = (
        os.getenv("EBAY_CLIENT_ID", "").strip()
        and os.getenv("EBAY_CLIENT_SECRET", "").strip()
        and os.getenv("EBAY_REFRESH_TOKEN", "").strip()
    )
    return bool(direct or refreshable)


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
    values = _workspace_or_env_ebay_values(workspace_id)
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
    raw = str(value).strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
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



def _shopify_domain(values: dict[str, str]) -> str:
    raw = values.get("store_domain", "").strip().lower()
    if not raw:
        raise RuntimeError("Shopify needs store_domain")
    if "://" in raw:
        parsed = urlparse(raw)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError("Shopify store_domain must be a bare HTTPS myshopify.com store")
        raw = parsed.hostname.lower()
    if "/" in raw or ":" in raw or "@" in raw:
        raise RuntimeError("Shopify store_domain must be a bare myshopify.com hostname")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", raw):
        raise RuntimeError("Shopify store_domain must end in .myshopify.com")
    return raw


def _shopify_graphql(
    values: dict[str, str],
    query: str,
    *,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    token = values.get("access_token", "").strip()
    if not token:
        raise RuntimeError("Shopify needs access_token")
    version = values.get("api_version", "2026-10").strip() or "2026-10"
    if not re.fullmatch(r"\d{4}-\d{2}", version):
        raise RuntimeError("Shopify api_version must look like 2026-10")
    response = requests.post(
        f"https://{_shopify_domain(values)}/admin/api/{version}/graphql.json",
        headers={
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={"query": query, "variables": variables or {}},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Shopify Admin API request failed ({response.status_code})")
    payload = response.json() or {}
    errors = payload.get("errors") or []
    if errors:
        detail = str((errors[0] or {}).get("message") or "GraphQL error")
        raise RuntimeError(f"Shopify Admin API error: {detail}")
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


SHOPIFY_VARIANTS_QUERY = """
query ResellerVariants($cursor: String) {
  productVariants(first: 100, after: $cursor) {
    nodes {
      id
      displayName
      title
      sku
      price
      inventoryQuantity
      availableForSale
      createdAt
      selectedOptions { name value }
      product {
        id
        title
        status
        productType
        vendor
        tags
        onlineStoreUrl
        featuredMedia { preview { image { url } } }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


SHOPIFY_ORDERS_QUERY = """
query ResellerOrders($cursor: String, $query: String!) {
  orders(first: 100, after: $cursor, query: $query, reverse: true) {
    nodes {
      id
      name
      createdAt
      cancelledAt
      closed
      displayFinancialStatus
      displayFulfillmentStatus
      currencyCode
      lineItems(first: 100) {
        nodes {
          id
          name
          sku
          quantity
          currentQuantity
          originalTotalSet { shopMoney { amount currencyCode } }
          variant { id }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def _fetch_shopify_products(values: dict[str, str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    cursor: str | None = None
    for _page in range(100):
        data = _shopify_graphql(
            values,
            SHOPIFY_VARIANTS_QUERY,
            variables={"cursor": cursor},
        )
        connection = data.get("productVariants") or {}
        nodes = list(connection.get("nodes") or [])
        for variant in nodes:
            variant_id = variant.get("id")
            if not variant_id:
                continue
            product = variant.get("product") or {}
            options = {
                str(row.get("name")): row.get("value")
                for row in (variant.get("selectedOptions") or [])
                if isinstance(row, dict) and row.get("name")
            }
            quantity = _int(variant.get("inventoryQuantity"))
            if quantity is None:
                quantity = 1 if variant.get("availableForSale") else 0
            active = (
                str(product.get("status") or "").upper() == "ACTIVE"
                and bool(variant.get("availableForSale"))
            )
            image_url = (
                ((product.get("featuredMedia") or {}).get("preview") or {})
                .get("image", {})
                .get("url")
            )
            title = str(variant.get("displayName") or "").strip()
            if not title:
                product_title = str(product.get("title") or "Untitled")
                variant_title = str(variant.get("title") or "").strip()
                title = (
                    product_title
                    if not variant_title or variant_title == "Default Title"
                    else f"{product_title} - {variant_title}"
                )
            created = _remote_datetime(variant.get("createdAt"))
            result.append(
                {
                    "source_id": str(variant_id),
                    "sku": variant.get("sku") or None,
                    "title": title,
                    "status": ListingStatus.ACTIVE if active else ListingStatus.INACTIVE,
                    "quantity": max(0, quantity),
                    "price_cents": _money(variant.get("price")),
                    "currency": values.get("currency", "EUR").strip().upper() or "EUR",
                    "listed_at": created.isoformat() if created else None,
                    "category": product.get("productType") or None,
                    "brand": product.get("vendor") or None,
                    "tags": list(product.get("tags") or []),
                    "attributes": options,
                    "product_type": product.get("productType") or None,
                    "url": product.get("onlineStoreUrl"),
                    "image_url": image_url,
                }
            )
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = str(page_info.get("endCursor") or "")
        if not cursor:
            break
    return result


def _shopify_order_status(order: dict[str, Any]) -> tuple[str, bool]:
    if order.get("cancelledAt"):
        return "cancelled", True
    financial = str(order.get("displayFinancialStatus") or "").strip().lower()
    fulfillment = str(order.get("displayFulfillmentStatus") or "").strip().lower()
    if financial in {"refunded", "voided"}:
        return financial, True
    if fulfillment == "fulfilled":
        return "completed", True
    return financial or fulfillment or "open", bool(order.get("closed"))


def _fetch_shopify_orders(values: dict[str, str]) -> list[dict[str, Any]]:
    days = max(1, min(_int(values.get("order_days"), 60) or 60, 3650))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    search = "created_at:>=" + cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    result: list[dict[str, Any]] = []
    cursor: str | None = None
    for _page in range(100):
        data = _shopify_graphql(
            values,
            SHOPIFY_ORDERS_QUERY,
            variables={"cursor": cursor, "query": search},
        )
        connection = data.get("orders") or {}
        orders = list(connection.get("nodes") or [])
        for order in orders:
            order_id = order.get("id")
            if not order_id:
                continue
            status, is_closed = _shopify_order_status(order)
            occurred = _remote_datetime(order.get("createdAt"))
            for line in ((order.get("lineItems") or {}).get("nodes") or []):
                line_id = line.get("id")
                if not line_id:
                    continue
                money = ((line.get("originalTotalSet") or {}).get("shopMoney") or {})
                variant = line.get("variant") or {}
                result.append(
                    {
                        "external_order_id": f"{order_id}:{line_id}",
                        "listing_external_id": str(variant.get("id") or ""),
                        "sku": line.get("sku"),
                        "title": line.get("name") or "Shopify sale",
                        "counterparty": None,
                        "total_cents": _money(money.get("amount")),
                        "currency": str(
                            money.get("currencyCode")
                            or order.get("currencyCode")
                            or values.get("currency", "EUR")
                        ),
                        "status": status,
                        "lifecycle_status": status,
                        "is_closed": is_closed,
                        "occurred_at": occurred.isoformat() if occurred else None,
                        "quantity": _int(line.get("quantity"), 1) or 1,
                        "extra": {
                            "order_id": order_id,
                            "order_name": order.get("name"),
                            "line_item_id": line_id,
                            "current_quantity": _int(line.get("currentQuantity")),
                        },
                    }
                )
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = str(page_info.get("endCursor") or "")
        if not cursor:
            break
    return result


def test_shopify_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id, Channel.SHOPIFY)
    variants = _shopify_graphql(
        values,
        "query { productVariants(first: 1) { nodes { id } } }",
    )
    orders = _shopify_graphql(
        values,
        "query { orders(first: 1) { nodes { id } } }",
    )
    return {
        "ok": True,
        "detail": "Shopify product and order scopes are readable.",
        "variants_visible": len(((variants.get("productVariants") or {}).get("nodes") or [])),
        "orders_visible": len(((orders.get("orders") or {}).get("nodes") or [])),
    }


def sync_shopify_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id, Channel.SHOPIFY)
    items = _fetch_shopify_products(values)
    orders = _fetch_shopify_orders(values)
    synced_at = datetime.now(timezone.utc)
    listing_result = record_workspace_channel_snapshot(
        workspace_id,
        Channel.SHOPIFY,
        items,
        synced_at=synced_at,
        full_snapshot=True,
        note="Shopify GraphQL Admin API",
    )
    order_result = record_workspace_channel_orders(
        workspace_id,
        Channel.SHOPIFY,
        orders,
        synced_at=synced_at,
    )
    return {"source": Channel.SHOPIFY, **listing_result, **order_result}


def _bigcommerce_headers(values: dict[str, str]) -> dict[str, str]:
    token = values.get("access_token", "").strip()
    if not token:
        raise RuntimeError("BigCommerce needs access_token")
    return {
        "X-Auth-Token": token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _bigcommerce_store_hash(values: dict[str, str]) -> str:
    store_hash = values.get("store_hash", "").strip().lower()
    if not re.fullmatch(r"[a-z0-9]+", store_hash):
        raise RuntimeError("BigCommerce store_hash must contain only letters and numbers")
    return store_hash


def _bigcommerce_get(
    values: dict[str, str],
    path: str,
    *,
    params: dict[str, Any] | None = None,
) -> Any:
    response = requests.get(
        f"https://api.bigcommerce.com/stores/{_bigcommerce_store_hash(values)}/{path.lstrip('/')}",
        headers=_bigcommerce_headers(values),
        params=params or {},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"BigCommerce API request failed ({response.status_code})")
    return response.json()


def _bigcommerce_quantity(product: dict[str, Any], variant: dict[str, Any] | None = None) -> int:
    source = variant or product
    tracking = str(product.get("inventory_tracking") or "none").lower()
    if tracking == "none":
        return 1
    return max(0, _int(source.get("inventory_level"), 0) or 0)


def _bigcommerce_product_common(product: dict[str, Any]) -> dict[str, Any]:
    images = list(product.get("images") or [])
    custom_url = product.get("custom_url") or {}
    custom_path = custom_url.get("url") if isinstance(custom_url, dict) else None
    category_ids = [str(value) for value in (product.get("categories") or [])]
    attributes: dict[str, Any] = {}
    if category_ids:
        attributes["category_ids"] = category_ids
    if custom_path:
        attributes["storefront_path"] = custom_path
    return {
        "description": product.get("description"),
        "brand": product.get("brand_name") or None,
        "product_type": product.get("type"),
        "tags": [
            value.strip()
            for value in str(product.get("search_keywords") or "").split(",")
            if value.strip()
        ],
        "attributes": attributes,
        "url": None,
        "image_url": (
            images[0].get("url_standard")
            if images and isinstance(images[0], dict)
            else None
        ),
    }


def _fetch_bigcommerce_products(values: dict[str, str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    page = 1
    while page <= 100:
        payload = _bigcommerce_get(
            values,
            "v3/catalog/products",
            params={"limit": 100, "page": page, "include": "variants,images"},
        )
        rows = list((payload or {}).get("data") or []) if isinstance(payload, dict) else []
        for product in rows:
            product_id = product.get("id")
            if product_id in (None, ""):
                continue
            common = _bigcommerce_product_common(product)
            variants = list(product.get("variants") or [])
            visible = bool(product.get("is_visible", True))
            availability = str(product.get("availability") or "available").lower()
            created = _remote_datetime(product.get("date_created"))
            if variants:
                for variant in variants:
                    variant_id = variant.get("id")
                    if variant_id in (None, ""):
                        continue
                    quantity = _bigcommerce_quantity(product, variant)
                    options = {
                        str(row.get("option_display_name") or row.get("option_id")): row.get("label")
                        for row in (variant.get("option_values") or [])
                        if isinstance(row, dict)
                    }
                    suffix = " / ".join(
                        str(value) for value in options.values() if value not in (None, "")
                    )
                    result.append(
                        {
                            "source_id": f"{product_id}:{variant_id}",
                            "sku": variant.get("sku") or product.get("sku") or None,
                            "title": str(product.get("name") or "Untitled")
                            + (f" - {suffix}" if suffix else ""),
                            "status": (
                                ListingStatus.ACTIVE
                                if visible and availability != "disabled" and quantity > 0
                                else ListingStatus.INACTIVE
                            ),
                            "quantity": quantity,
                            "price_cents": _money(
                                variant.get("price")
                                if variant.get("price") not in (None, "")
                                else product.get("price")
                            ),
                            "currency": values.get("currency", "EUR").strip().upper() or "EUR",
                            "listed_at": created.isoformat() if created else None,
                            **common,
                            "attributes": {**common.get("attributes", {}), **options},
                        }
                    )
            else:
                quantity = _bigcommerce_quantity(product)
                result.append(
                    {
                        "source_id": str(product_id),
                        "sku": product.get("sku") or None,
                        "title": product.get("name") or "Untitled",
                        "status": (
                            ListingStatus.ACTIVE
                            if visible and availability != "disabled" and quantity > 0
                            else ListingStatus.INACTIVE
                        ),
                        "quantity": quantity,
                        "price_cents": _money(product.get("price")),
                        "currency": values.get("currency", "EUR").strip().upper() or "EUR",
                        "listed_at": created.isoformat() if created else None,
                        **common,
                    }
                )
        pagination = ((payload or {}).get("meta") or {}).get("pagination") or {}
        total_pages = _int(pagination.get("total_pages"), page) or page
        if page >= total_pages or len(rows) < 100:
            break
        page += 1
    return result


def _bigcommerce_order_status(raw: dict[str, Any]) -> tuple[str, bool]:
    status = str(raw.get("status") or "open").strip().lower().replace(" ", "_")
    closed = status in {
        "completed", "shipped", "cancelled", "canceled", "refunded",
        "partially_refunded", "declined",
    }
    return status, closed


def _fetch_bigcommerce_orders(values: dict[str, str]) -> list[dict[str, Any]]:
    days = max(1, min(_int(values.get("order_days"), 365) or 365, 3650))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result: list[dict[str, Any]] = []
    page = 1
    while page <= 100:
        orders = _bigcommerce_get(
            values,
            "v2/orders",
            params={
                "limit": 50,
                "page": page,
                "min_date_created": cutoff.strftime("%a, %d %b %Y %H:%M:%S +0000"),
            },
        )
        rows = orders if isinstance(orders, list) else []
        for order in rows:
            order_id = order.get("id")
            if order_id in (None, ""):
                continue
            status, is_closed = _bigcommerce_order_status(order)
            occurred = _remote_datetime(order.get("date_created"))
            products = _bigcommerce_get(values, f"v2/orders/{order_id}/products")
            for line in products if isinstance(products, list) else []:
                line_id = line.get("id")
                if line_id in (None, ""):
                    continue
                product_id = line.get("product_id")
                variant_id = _int(line.get("variant_id"), 0) or 0
                listing_external_id = (
                    f"{product_id}:{variant_id}" if variant_id else str(product_id or "")
                )
                result.append(
                    {
                        "external_order_id": f"{order_id}:{line_id}",
                        "listing_external_id": listing_external_id,
                        "sku": line.get("sku"),
                        "title": line.get("name") or "BigCommerce sale",
                        "counterparty": None,
                        "total_cents": _money(
                            line.get("total_inc_tax")
                            if line.get("total_inc_tax") not in (None, "")
                            else line.get("total_ex_tax")
                        ),
                        "currency": str(
                            order.get("currency_code") or values.get("currency", "EUR")
                        ),
                        "status": status,
                        "lifecycle_status": status,
                        "is_closed": is_closed,
                        "occurred_at": occurred.isoformat() if occurred else None,
                        "quantity": _int(line.get("quantity"), 1) or 1,
                        "extra": {
                            "order_id": order_id,
                            "line_item_id": line_id,
                            "product_id": product_id,
                            "variant_id": variant_id,
                        },
                    }
                )
        if len(rows) < 50:
            break
        page += 1
    return result


def test_bigcommerce_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id, Channel.BIGCOMMERCE)
    products = _bigcommerce_get(
        values,
        "v3/catalog/products",
        params={"limit": 1, "page": 1},
    )
    orders = _bigcommerce_get(
        values,
        "v2/orders",
        params={"limit": 1, "page": 1},
    )
    return {
        "ok": True,
        "detail": "BigCommerce catalog and orders are readable.",
        "products_visible": len((products or {}).get("data") or [])
        if isinstance(products, dict)
        else 0,
        "orders_visible": len(orders) if isinstance(orders, list) else 0,
    }


def sync_bigcommerce_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id, Channel.BIGCOMMERCE)
    items = _fetch_bigcommerce_products(values)
    orders = _fetch_bigcommerce_orders(values)
    synced_at = datetime.now(timezone.utc)
    listing_result = record_workspace_channel_snapshot(
        workspace_id,
        Channel.BIGCOMMERCE,
        items,
        synced_at=synced_at,
        full_snapshot=True,
        note="BigCommerce REST Management API",
    )
    order_result = record_workspace_channel_orders(
        workspace_id,
        Channel.BIGCOMMERCE,
        orders,
        synced_at=synced_at,
    )
    return {"source": Channel.BIGCOMMERCE, **listing_result, **order_result}



SQUARESPACE_API_BASE = "https://api.squarespace.com"
SQUARESPACE_USER_AGENT = "ResellerDashboard/1.0"


def _squarespace_headers(values: dict[str, str]) -> dict[str, str]:
    token = values.get("access_token", "").strip()
    if not token:
        raise RuntimeError("Squarespace needs an API key or OAuth access token")
    return {
        "Authorization": f"Bearer {token}",
        "User-Agent": SQUARESPACE_USER_AGENT,
        "Accept": "application/json",
    }


def _squarespace_get(
    values: dict[str, str],
    path: str,
    *,
    params: dict[str, Any] | None = None,
) -> Any:
    response = requests.get(
        SQUARESPACE_API_BASE + "/" + path.lstrip("/"),
        headers=_squarespace_headers(values),
        params=params or {},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Squarespace API request failed ({response.status_code})")
    return response.json()


def _squarespace_paged(
    values: dict[str, str],
    path: str,
    result_key: str,
    *,
    first_params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    cursor: str | None = None
    for _page in range(100):
        params = {"cursor": cursor} if cursor else dict(first_params or {})
        payload = _squarespace_get(values, path, params=params)
        if not isinstance(payload, dict):
            break
        rows = payload.get(result_key) or []
        if isinstance(rows, list):
            result.extend(row for row in rows if isinstance(row, dict))
        pagination = payload.get("pagination") or {}
        if not pagination.get("hasNextPage"):
            break
        cursor = str(pagination.get("nextPageCursor") or "").strip()
        if not cursor:
            break
    return result


def _squarespace_site(values: dict[str, str]) -> dict[str, Any]:
    payload = _squarespace_get(values, "1.0/authorization/website")
    return payload if isinstance(payload, dict) else {}


def _squarespace_inventory(values: dict[str, str]) -> dict[str, dict[str, Any]]:
    rows = _squarespace_paged(
        values,
        "1.0/commerce/inventory",
        "inventory",
    )
    return {
        str(row.get("variantId")): row
        for row in rows
        if row.get("variantId") not in (None, "")
    }


def _squarespace_products(values: dict[str, str]) -> list[dict[str, Any]]:
    summaries = _squarespace_paged(
        values,
        "v2/commerce/products",
        "products",
    )
    ids = [
        str(row.get("id")).strip()
        for row in summaries
        if row.get("id") not in (None, "")
    ]
    details: list[dict[str, Any]] = []
    for start in range(0, len(ids), 50):
        batch = ",".join(ids[start:start + 50])
        payload = _squarespace_get(values, f"v2/commerce/products/{batch}")
        if isinstance(payload, dict):
            rows = payload.get("products") or []
            details.extend(row for row in rows if isinstance(row, dict))
    if not details:
        return summaries
    by_id = {
        str(row.get("id")): row
        for row in details
        if row.get("id") not in (None, "")
    }
    return [
        by_id.get(str(summary.get("id")), summary)
        for summary in summaries
    ]


def _squarespace_price(pricing: Any) -> tuple[int | None, str | None]:
    if not isinstance(pricing, dict):
        return None, None
    on_sale = bool(pricing.get("onSale"))
    money = pricing.get("salePrice") if on_sale else pricing.get("basePrice")
    if not isinstance(money, dict):
        money = pricing.get("basePrice") or pricing.get("salePrice")
    if not isinstance(money, dict):
        return None, None
    currency = str(money.get("currency") or "").strip().upper() or None
    return _money(money.get("value")), currency


def _squarespace_listing_url(site_url: Any, product_url: Any) -> str | None:
    raw = str(product_url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return raw
    base = str(site_url or "").strip().rstrip("/")
    if base.startswith("https://") and raw.startswith("/"):
        return base + raw
    return None


def _squarespace_variant_title(product: dict[str, Any], variant: dict[str, Any]) -> str:
    base = str(product.get("name") or "Untitled").strip() or "Untitled"
    attributes = variant.get("attributes") or {}
    if not isinstance(attributes, dict):
        return base
    suffix = " / ".join(
        str(value).strip()
        for value in attributes.values()
        if str(value or "").strip()
    )
    return f"{base} - {suffix}" if suffix else base


def _fetch_squarespace_products(
    values: dict[str, str],
    *,
    site: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    site = site or _squarespace_site(values)
    fallback_currency = (
        str(site.get("currency") or values.get("currency") or "EUR").strip().upper()
        or "EUR"
    )
    site_url = site.get("url")
    inventory = _squarespace_inventory(values)
    products = _squarespace_products(values)
    result: list[dict[str, Any]] = []

    for product in products:
        product_id = product.get("id")
        if product_id in (None, ""):
            continue
        visible = bool(product.get("isVisible", True))
        created = _remote_datetime(product.get("createdOn"))
        product_type = str(product.get("type") or "").strip().upper() or None
        tags = list(product.get("tags") or [])
        images = [
            row for row in (product.get("images") or [])
            if isinstance(row, dict)
        ]
        common = {
            "description": product.get("description"),
            "tags": tags,
            "product_type": product_type,
            "url": _squarespace_listing_url(site_url, product.get("url")),
            "image_url": images[0].get("url") if images else None,
            "listed_at": created.isoformat() if created else None,
        }
        variants = [
            row for row in (product.get("variants") or [])
            if isinstance(row, dict)
        ]
        if not variants:
            price_cents, currency = _squarespace_price(product.get("pricing"))
            result.append(
                {
                    "source_id": str(product_id),
                    "sku": product.get("sku") or None,
                    "title": product.get("name") or "Untitled",
                    "status": ListingStatus.ACTIVE if visible else ListingStatus.INACTIVE,
                    "quantity": 1 if visible else 0,
                    "price_cents": price_cents,
                    "currency": currency or fallback_currency,
                    **common,
                }
            )
            continue

        for variant in variants:
            variant_id = variant.get("id")
            if variant_id in (None, ""):
                continue
            stock = inventory.get(str(variant_id), {})
            unlimited = bool(stock.get("isUnlimited"))
            inventory_managed = product_type in {"PHYSICAL", "SERVICE"}
            quantity = (
                1
                if not inventory_managed or unlimited
                else max(0, _int(stock.get("quantity"), 0) or 0)
            )
            active = visible and (not inventory_managed or unlimited or quantity > 0)
            price_cents, currency = _squarespace_price(
                variant.get("pricing") or product.get("pricing")
            )
            attributes = (
                dict(variant.get("attributes") or {})
                if isinstance(variant.get("attributes"), dict)
                else {}
            )
            if unlimited:
                attributes["inventory_unlimited"] = True
            variant_image = variant.get("image") or {}
            result.append(
                {
                    "source_id": str(variant_id),
                    "sku": variant.get("sku") or stock.get("sku") or None,
                    "title": _squarespace_variant_title(product, variant),
                    "status": ListingStatus.ACTIVE if active else ListingStatus.INACTIVE,
                    "quantity": quantity,
                    "price_cents": price_cents,
                    "currency": currency or fallback_currency,
                    "attributes": attributes,
                    "image_url": (
                        variant_image.get("url")
                        if isinstance(variant_image, dict) and variant_image.get("url")
                        else common["image_url"]
                    ),
                    **{key: value for key, value in common.items() if key != "image_url"},
                }
            )
    return result


def _squarespace_order_status(order: dict[str, Any]) -> tuple[str, bool]:
    payment = str(order.get("paymentState") or "").strip().upper()
    fulfillment = str(order.get("fulfillmentStatus") or "").strip().upper()
    if fulfillment in {"CANCELED", "CANCELLED"}:
        return "cancelled", True
    if payment in {"REFUNDED", "FAILED", "REFUND_FAILED"}:
        return payment.lower(), True
    if fulfillment == "FULFILLED" and payment == "PAID":
        return "completed", True
    status = payment.lower() or fulfillment.lower() or "open"
    return status, fulfillment == "FULFILLED"


def _fetch_squarespace_orders(values: dict[str, str]) -> list[dict[str, Any]]:
    days = max(1, min(_int(values.get("order_days"), 365) or 365, 3650))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    payment_states = ",".join(
        (
            "NOT_CHARGED",
            "AUTHORIZED",
            "PAID",
            "REFUNDED",
            "PENDING",
            "FAILED",
            "REFUND_PENDING",
            "REFUND_FAILED",
            "PARTIALLY_PAID",
        )
    )
    orders = _squarespace_paged(
        values,
        "1.0/commerce/orders",
        "result",
        first_params={
            "modifiedAfter": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "paymentStates": payment_states,
        },
    )
    result: list[dict[str, Any]] = []
    for order in orders:
        order_id = order.get("id")
        if order_id in (None, ""):
            continue
        status, is_closed = _squarespace_order_status(order)
        occurred = _remote_datetime(order.get("createdOn"))
        for line in order.get("lineItems") or []:
            if not isinstance(line, dict):
                continue
            line_id = line.get("id")
            if line_id in (None, ""):
                continue
            variant_id = line.get("variantId")
            product_id = line.get("productId")
            money = line.get("unitPricePaid") or {}
            unit_cents = _money(money.get("value")) if isinstance(money, dict) else None
            quantity = max(1, _int(line.get("quantity"), 1) or 1)
            total_cents = unit_cents * quantity if unit_cents is not None else None
            result.append(
                {
                    "external_order_id": f"{order_id}:{line_id}",
                    "listing_external_id": str(variant_id or product_id or ""),
                    "sku": line.get("sku"),
                    "title": line.get("productName") or "Squarespace sale",
                    "counterparty": None,
                    "total_cents": total_cents,
                    "currency": str(
                        (money.get("currency") if isinstance(money, dict) else None)
                        or values.get("currency")
                        or "EUR"
                    ),
                    "status": status,
                    "lifecycle_status": status,
                    "is_closed": is_closed,
                    "occurred_at": occurred.isoformat() if occurred else None,
                    "quantity": quantity,
                    "extra": {
                        "order_id": order_id,
                        "order_number": order.get("orderNumber"),
                        "line_item_id": line_id,
                        "product_id": product_id,
                        "variant_id": variant_id,
                        "payment_state": order.get("paymentState"),
                        "fulfillment_status": order.get("fulfillmentStatus"),
                    },
                }
            )
    return result


def test_squarespace_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id, Channel.SQUARESPACE)
    site = _squarespace_site(values)
    products = _squarespace_get(values, "v2/commerce/products")
    inventory = _squarespace_get(values, "1.0/commerce/inventory")
    orders = _squarespace_get(
        values,
        "1.0/commerce/orders",
        params={"paymentStates": "PAID,PARTIALLY_PAID,REFUNDED"},
    )
    return {
        "ok": True,
        "detail": "Squarespace products, inventory and orders are readable.",
        "site_title": site.get("title"),
        "products_visible": len((products or {}).get("products") or [])
        if isinstance(products, dict)
        else 0,
        "inventory_visible": len((inventory or {}).get("inventory") or [])
        if isinstance(inventory, dict)
        else 0,
        "orders_visible": len((orders or {}).get("result") or [])
        if isinstance(orders, dict)
        else 0,
    }


def sync_squarespace_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id, Channel.SQUARESPACE)
    site = _squarespace_site(values)
    items = _fetch_squarespace_products(values, site=site)
    orders = _fetch_squarespace_orders(values)
    synced_at = datetime.now(timezone.utc)
    listing_result = record_workspace_channel_snapshot(
        workspace_id,
        Channel.SQUARESPACE,
        items,
        synced_at=synced_at,
        full_snapshot=True,
        note="Squarespace Commerce APIs",
    )
    order_result = record_workspace_channel_orders(
        workspace_id,
        Channel.SQUARESPACE,
        orders,
        synced_at=synced_at,
    )
    return {"source": Channel.SQUARESPACE, **listing_result, **order_result}



WIX_API_BASE = "https://www.wixapis.com"
WIX_STORES_APP_ID = "215238eb-22a5-4c36-9e7b-e7c08025e04e"


def _wix_headers(values: dict[str, str]) -> dict[str, str]:
    api_key = values.get("api_key", "").strip()
    site_id = values.get("site_id", "").strip()
    if not api_key or not site_id:
        raise RuntimeError("Wix needs api_key and site_id")
    return {
        "Authorization": api_key,
        "wix-site-id": site_id,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _wix_post(
    values: dict[str, str],
    path: str,
    *,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = requests.post(
        WIX_API_BASE + "/" + path.lstrip("/"),
        headers=_wix_headers(values),
        json=body or {},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Wix API request failed ({response.status_code})")
    payload = response.json() or {}
    return payload if isinstance(payload, dict) else {}


def _wix_next_cursor(payload: dict[str, Any]) -> str | None:
    for key in ("pagingMetadata", "metadata"):
        metadata = payload.get(key) or {}
        if not isinstance(metadata, dict):
            continue
        cursors = metadata.get("cursors") or {}
        if isinstance(cursors, dict):
            value = cursors.get("next")
            if value:
                return str(value)
        value = metadata.get("nextCursor")
        if value:
            return str(value)
    return None


def _wix_query_variants(values: dict[str, str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    cursor: str | None = None
    for _page in range(100):
        cursor_paging: dict[str, Any] = {"limit": 1000}
        if cursor:
            cursor_paging["cursor"] = cursor
        payload = _wix_post(
            values,
            "stores/v3/products/query-variants",
            body={
                "fields": ["CURRENCY"],
                "query": {"cursorPaging": cursor_paging},
            },
        )
        rows = payload.get("variants") or []
        if isinstance(rows, list):
            result.extend(row for row in rows if isinstance(row, dict))
        cursor = _wix_next_cursor(payload)
        if not cursor:
            break
    return result


def _wix_query_inventory(values: dict[str, str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    cursor: str | None = None
    for _page in range(100):
        cursor_paging: dict[str, Any] = {"limit": 1000}
        if cursor:
            cursor_paging["cursor"] = cursor
        payload = _wix_post(
            values,
            "stores/v3/inventory-items/query",
            body={"query": {"cursorPaging": cursor_paging}},
        )
        rows = payload.get("inventoryItems") or []
        if isinstance(rows, list):
            result.extend(row for row in rows if isinstance(row, dict))
        cursor = _wix_next_cursor(payload)
        if not cursor:
            break
    return result


def _wix_inventory_by_variant(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        product_id = str(row.get("productId") or "").strip()
        variant_id = str(row.get("variantId") or "").strip()
        if not product_id or not variant_id:
            continue
        key = f"{product_id}:{variant_id}"
        current = grouped.setdefault(
            key,
            {
                "tracked": False,
                "untracked": False,
                "untracked_in_stock": False,
                "quantity": 0,
                "locations": 0,
            },
        )
        current["locations"] += 1
        tracked = bool(row.get("trackQuantity"))
        if tracked:
            current["tracked"] = True
            current["quantity"] += max(0, _int(row.get("quantity"), 0) or 0)
        else:
            current["untracked"] = True
            current["untracked_in_stock"] = (
                current["untracked_in_stock"] or bool(row.get("inStock"))
            )
    return grouped


def _wix_variant_attributes(variant: dict[str, Any]) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    for row in variant.get("optionChoices") or []:
        if not isinstance(row, dict):
            continue
        names = row.get("optionChoiceNames") or {}
        if not isinstance(names, dict):
            continue
        name = str(names.get("optionName") or "").strip()
        choice = str(names.get("choiceName") or "").strip()
        if name and choice:
            attributes[name] = choice
    product = variant.get("productData") or {}
    if isinstance(product, dict):
        category_ids = [
            str(value)
            for value in (product.get("directCategoryIds") or [])
            if value not in (None, "")
        ]
        if category_ids:
            attributes["category_ids"] = category_ids
    return attributes


def _wix_variant_image(variant: dict[str, Any]) -> str | None:
    media = variant.get("media") or {}
    if not isinstance(media, dict):
        return None
    for candidate in (
        media.get("url"),
        (media.get("thumbnail") or {}).get("url")
        if isinstance(media.get("thumbnail"), dict)
        else None,
        (media.get("image") or {}).get("url")
        if isinstance(media.get("image"), dict)
        else None,
    ):
        if candidate:
            return str(candidate)
    return None


def _fetch_wix_products(values: dict[str, str]) -> list[dict[str, Any]]:
    variants = _wix_query_variants(values)
    inventory = _wix_inventory_by_variant(_wix_query_inventory(values))
    fallback_currency = values.get("currency", "EUR").strip().upper() or "EUR"
    result: list[dict[str, Any]] = []

    for variant in variants:
        product = variant.get("productData") or {}
        if not isinstance(product, dict):
            product = {}
        product_id = str(product.get("productId") or "").strip()
        variant_id = str(variant.get("variantId") or "").strip()
        if not product_id or not variant_id:
            continue
        source_id = f"{product_id}:{variant_id}"
        stock = inventory.get(source_id)
        status_info = variant.get("inventoryStatus") or {}
        if not isinstance(status_info, dict):
            status_info = {}

        if stock and stock.get("untracked_in_stock"):
            in_stock = True
            quantity = 1
        elif stock and stock.get("tracked"):
            quantity = max(0, _int(stock.get("quantity"), 0) or 0)
            in_stock = quantity > 0 or bool(status_info.get("preorderEnabled"))
        elif stock and stock.get("untracked"):
            in_stock = bool(status_info.get("inStock"))
            quantity = 1 if in_stock else 0
        else:
            in_stock = bool(status_info.get("inStock")) or bool(status_info.get("preorderEnabled"))
            quantity = 1 if in_stock else 0

        visible = bool(product.get("visible", True)) and bool(variant.get("visible", True))
        active = visible and in_stock
        attributes = _wix_variant_attributes(variant)
        suffix = " / ".join(
            str(value)
            for key, value in attributes.items()
            if key != "category_ids" and value not in (None, "")
        )
        base_title = str(product.get("name") or "Untitled").strip() or "Untitled"
        price = ((variant.get("price") or {}).get("actualPrice") or {})
        currency = str(product.get("currency") or fallback_currency).strip().upper() or fallback_currency

        result.append(
            {
                "source_id": source_id,
                "sku": variant.get("sku") or None,
                "title": base_title + (f" - {suffix}" if suffix else ""),
                "status": ListingStatus.ACTIVE if active else ListingStatus.INACTIVE,
                "quantity": quantity,
                "price_cents": _money(price.get("amount")) if isinstance(price, dict) else None,
                "currency": currency,
                "product_type": product.get("productType"),
                "attributes": attributes,
                "image_url": _wix_variant_image(variant),
            }
        )
    return result


def _wix_order_status(order: dict[str, Any]) -> tuple[str, bool]:
    status = str(order.get("status") or "").strip().upper()
    payment = str(order.get("paymentStatus") or "").strip().upper()
    fulfillment = str(order.get("fulfillmentStatus") or "").strip().upper()

    if status in {"CANCELED", "CANCELLED", "REJECTED"}:
        return "cancelled", True
    if "REFUND" in payment:
        return payment.lower(), True
    if payment in {"FAILED", "DECLINED"}:
        return payment.lower(), True
    if status == "APPROVED" and payment == "PAID" and fulfillment == "FULFILLED":
        return "completed", True
    if payment:
        return payment.lower(), fulfillment == "FULFILLED"
    if status:
        return status.lower(), status in {"CANCELED", "CANCELLED", "REJECTED"}
    return fulfillment.lower() or "open", fulfillment == "FULFILLED"


def _fetch_wix_orders(values: dict[str, str]) -> list[dict[str, Any]]:
    days = max(1, min(_int(values.get("order_days"), 365) or 365, 3650))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result: list[dict[str, Any]] = []
    cursor: str | None = None

    for _page in range(100):
        cursor_paging: dict[str, Any] = {"limit": 100}
        if cursor:
            cursor_paging["cursor"] = cursor
        payload = _wix_post(
            values,
            "ecom/v1/orders/search",
            body={
                "search": {
                    "filter": {
                        "createdDate": {
                            "$gte": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
                        }
                    },
                    "cursorPaging": cursor_paging,
                }
            },
        )
        orders = payload.get("orders") or []
        for order in orders if isinstance(orders, list) else []:
            if not isinstance(order, dict):
                continue
            order_id = str(order.get("id") or "").strip()
            if not order_id:
                continue
            status, is_closed = _wix_order_status(order)
            occurred = _remote_datetime(order.get("createdDate"))
            order_currency = str(
                order.get("currency") or values.get("currency") or "EUR"
            ).strip().upper() or "EUR"

            for line in order.get("lineItems") or []:
                if not isinstance(line, dict):
                    continue
                line_id = str(line.get("id") or "").strip()
                if not line_id:
                    continue
                catalog = line.get("catalogReference") or {}
                if not isinstance(catalog, dict):
                    catalog = {}
                if catalog.get("appId") != WIX_STORES_APP_ID:
                    continue
                options = catalog.get("options") or {}
                if not isinstance(options, dict):
                    options = {}
                product_id = str(catalog.get("catalogItemId") or "").strip()
                variant_id = str(options.get("variantId") or "").strip()
                listing_external_id = (
                    f"{product_id}:{variant_id}"
                    if product_id and variant_id
                    else product_id
                )
                physical = line.get("physicalProperties") or {}
                if not isinstance(physical, dict):
                    physical = {}
                price = line.get("price") or {}
                unit_cents = _money(price.get("amount")) if isinstance(price, dict) else None
                quantity = max(1, _int(line.get("quantity"), 1) or 1)
                title_obj = line.get("productName") or {}
                title = (
                    title_obj.get("original")
                    if isinstance(title_obj, dict)
                    else title_obj
                ) or "Wix sale"

                result.append(
                    {
                        "external_order_id": f"{order_id}:{line_id}",
                        "listing_external_id": listing_external_id,
                        "sku": physical.get("sku") or None,
                        "title": title,
                        "counterparty": None,
                        "total_cents": unit_cents * quantity if unit_cents is not None else None,
                        "currency": order_currency,
                        "status": status,
                        "lifecycle_status": status,
                        "is_closed": is_closed,
                        "occurred_at": occurred.isoformat() if occurred else None,
                        "quantity": quantity,
                        "extra": {
                            "order_id": order_id,
                            "order_number": order.get("number"),
                            "line_item_id": line_id,
                            "product_id": product_id or None,
                            "variant_id": variant_id or None,
                            "payment_status": order.get("paymentStatus"),
                            "fulfillment_status": order.get("fulfillmentStatus"),
                        },
                    }
                )
        cursor = _wix_next_cursor(payload)
        if not cursor:
            break
    return result


def test_wix_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id, Channel.WIX)
    variants = _wix_post(
        values,
        "stores/v3/products/query-variants",
        body={
            "fields": ["CURRENCY"],
            "query": {"cursorPaging": {"limit": 1}},
        },
    )
    inventory = _wix_post(
        values,
        "stores/v3/inventory-items/query",
        body={"query": {"cursorPaging": {"limit": 1}}},
    )
    orders = _wix_post(
        values,
        "ecom/v1/orders/search",
        body={"search": {"cursorPaging": {"limit": 1}}},
    )
    return {
        "ok": True,
        "detail": "Wix catalog, inventory and orders are readable.",
        "variants_visible": len(variants.get("variants") or []),
        "inventory_visible": len(inventory.get("inventoryItems") or []),
        "orders_visible": len(orders.get("orders") or []),
    }


def sync_wix_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id, Channel.WIX)
    items = _fetch_wix_products(values)
    orders = _fetch_wix_orders(values)
    synced_at = datetime.now(timezone.utc)
    listing_result = record_workspace_channel_snapshot(
        workspace_id,
        Channel.WIX,
        items,
        synced_at=synced_at,
        full_snapshot=True,
        note="Wix Catalog V3, Inventory V3 and eCommerce Orders",
    )
    order_result = record_workspace_channel_orders(
        workspace_id,
        Channel.WIX,
        orders,
        synced_at=synced_at,
    )
    return {"source": Channel.WIX, **listing_result, **order_result}



DEPOP_API_BASES = {
    "production": "https://partnerapi.depop.com",
    "staging": "https://partnerapi-staging.depop.com",
}


def _depop_environment(values: dict[str, str]) -> str:
    environment = str(values.get("environment") or "production").strip().lower()
    if environment not in DEPOP_API_BASES:
        raise RuntimeError("Depop environment must be production or staging")
    return environment


def _depop_headers(values: dict[str, str]) -> dict[str, str]:
    api_key = str(values.get("api_key") or "").strip()
    if not api_key:
        raise RuntimeError("Depop needs a partner API key")
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }


def _depop_get(
    values: dict[str, str],
    path: str,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    environment = _depop_environment(values)
    response = requests.get(
        DEPOP_API_BASES[environment] + "/" + path.lstrip("/"),
        headers=_depop_headers(values),
        params=params or {},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Depop API request failed ({response.status_code}) in {environment}"
        )
    payload = response.json() or {}
    return payload if isinstance(payload, dict) else {}


def _depop_title(product: dict[str, Any]) -> str:
    description = str(product.get("description") or "").strip()
    if description:
        first_line = next(
            (line.strip() for line in description.splitlines() if line.strip()),
            "",
        )
        if first_line:
            return first_line[:160]
    slug = str(product.get("slug") or "").strip()
    if slug:
        return slug.replace("-", " ")[:160]
    return f"Depop item {product.get('product_id') or ''}".strip()


def _depop_product_attributes(product: dict[str, Any]) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    raw = product.get("attributes")
    if isinstance(raw, dict):
        attributes.update(raw)
    for key in (
        "department",
        "size_set_id",
        "size_id",
        "colour",
        "style",
        "age",
        "source",
    ):
        value = product.get(key)
        if value not in (None, "", [], {}):
            attributes[key] = value
    return attributes


def _fetch_depop_products(values: dict[str, str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    cursor: str | None = None
    for _page in range(1000):
        params: dict[str, Any] = {
            "limit": 100,
            "state": "all",
            "sort_by": "id_desc",
        }
        if cursor:
            params["cursor"] = cursor
        payload = _depop_get(values, "api/v1/products/", params=params)
        rows = payload.get("data") or []
        for product in rows if isinstance(rows, list) else []:
            if not isinstance(product, dict):
                continue
            product_id = product.get("product_id")
            if product_id in (None, ""):
                continue
            quantity = max(0, _int(product.get("quantity"), 0) or 0)
            remote_status = str(product.get("status") or "").strip().upper()
            active = remote_status in {
                "STATUS_ONSALE",
                "ONSALE",
                "ON_SALE",
                "SELLING",
            } and quantity > 0
            pictures = [
                row for row in (product.get("pictures") or [])
                if isinstance(row, dict)
            ]
            created = _remote_datetime(product.get("created_at"))
            current_price = (
                product.get("current_price")
                or product.get("discount_price")
                or product.get("price_amount")
            )
            slug = str(product.get("slug") or "").strip()
            attributes = _depop_product_attributes(product)
            brand = product.get("brand_name") or product.get("brand")
            colour = product.get("colour")
            if isinstance(colour, list):
                colour_value = ", ".join(str(value) for value in colour if value)
            else:
                colour_value = colour

            result.append(
                {
                    "source_id": str(product_id),
                    "sku": product.get("sku") or None,
                    "title": _depop_title(product),
                    "status": ListingStatus.ACTIVE if active else ListingStatus.INACTIVE,
                    "quantity": quantity,
                    "price_cents": _money(current_price),
                    "currency": str(
                        product.get("price_currency")
                        or values.get("currency")
                        or "EUR"
                    ).strip().upper(),
                    "url": (
                        f"https://www.depop.com/products/{slug}/"
                        if slug
                        else None
                    ),
                    "listed_at": created.isoformat() if created else None,
                    "description": product.get("description"),
                    "category": product.get("product_type") or product.get("department"),
                    "product_type": product.get("product_type"),
                    "condition": product.get("condition"),
                    "brand": brand,
                    "color": colour_value,
                    "tags": product.get("style") or product.get("source"),
                    "attributes": attributes,
                    "image_url": pictures[0].get("url") if pictures else None,
                }
            )

        meta = payload.get("meta") or {}
        if not isinstance(meta, dict) or not meta.get("has_more"):
            break
        cursor = str(meta.get("cursor") or "").strip()
        if not cursor:
            break
    return result


def _depop_order_status(order: dict[str, Any]) -> tuple[str, bool]:
    raw = str(order.get("status") or "").strip().upper()
    status = raw.lower() or "open"
    return status, raw in {
        "COMPLETED",
        "REFUNDED",
        "CANCELLED",
        "CANCELED",
        "FAILED",
    }


def _fetch_depop_orders(values: dict[str, str]) -> list[dict[str, Any]]:
    days = max(1, min(_int(values.get("order_days"), 365) or 365, 3650))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result: list[dict[str, Any]] = []
    cursor: str | None = None

    for _page in range(1000):
        params: dict[str, Any] = {
            "limit": 200,
            "from": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if cursor:
            params["cursor"] = cursor
        payload = _depop_get(values, "api/v1/orders/", params=params)
        orders = payload.get("data") or []
        for order in orders if isinstance(orders, list) else []:
            if not isinstance(order, dict):
                continue
            purchase_id = str(order.get("purchase_id") or "").strip()
            if not purchase_id:
                continue
            status, is_closed = _depop_order_status(order)
            occurred = _remote_datetime(order.get("created_at"))
            currency = str(
                order.get("currency")
                or values.get("currency")
                or "EUR"
            ).strip().upper()

            for line in order.get("line_items") or []:
                if not isinstance(line, dict):
                    continue
                purchase_item_id = str(line.get("purchase_item_id") or "").strip()
                product_id = str(line.get("product_id") or "").strip()
                if not purchase_item_id or not product_id:
                    continue
                description = str(line.get("description") or "").strip()
                result.append(
                    {
                        "external_order_id": f"{purchase_id}:{purchase_item_id}",
                        "listing_external_id": product_id,
                        "sku": line.get("sku") or None,
                        "title": (
                            description.splitlines()[0][:160]
                            if description
                            else f"Depop sale {product_id}"
                        ),
                        "counterparty": None,
                        "total_cents": _money(line.get("sold_price")),
                        "currency": currency,
                        "status": status,
                        "lifecycle_status": status,
                        "is_closed": is_closed,
                        "occurred_at": occurred.isoformat() if occurred else None,
                        "quantity": 1,
                        "extra": {
                            "purchase_id": purchase_id,
                            "purchase_item_id": purchase_item_id,
                            "product_id": product_id,
                            "slug": line.get("slug"),
                            "parcel_id": line.get("parcel_id"),
                            "original_price": line.get("original_price"),
                            "sold_via_offers": bool(line.get("sold_via_offers")),
                            "image_url": line.get("image_url"),
                            "seller_receives_amount": order.get("seller_receives_amount"),
                            "seller_fee_breakdown": order.get("seller_fee_breakdown") or [],
                        },
                    }
                )

        meta = payload.get("meta") or {}
        if not isinstance(meta, dict) or not meta.get("has_more"):
            break
        cursor = str(meta.get("cursor") or "").strip()
        if not cursor:
            break
    return result


def test_depop_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id, Channel.DEPOP)
    shop = _depop_get(values, "api/v1/shop/")
    products = _depop_get(
        values,
        "api/v1/products/",
        params={"limit": 1, "state": "all"},
    )
    orders = _depop_get(
        values,
        "api/v1/orders/",
        params={"limit": 1},
    )
    return {
        "ok": True,
        "detail": "Depop shop, products and orders are readable.",
        "environment": _depop_environment(values),
        "username": shop.get("username"),
        "products_visible": len(products.get("data") or []),
        "orders_visible": len(orders.get("data") or []),
    }


def sync_depop_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id, Channel.DEPOP)
    items = _fetch_depop_products(values)
    orders = _fetch_depop_orders(values)
    synced_at = datetime.now(timezone.utc)
    listing_result = record_workspace_channel_snapshot(
        workspace_id,
        Channel.DEPOP,
        items,
        synced_at=synced_at,
        full_snapshot=True,
        note=f"Depop Selling API ({_depop_environment(values)})",
    )
    order_result = record_workspace_channel_orders(
        workspace_id,
        Channel.DEPOP,
        orders,
        synced_at=synced_at,
    )
    return {"source": Channel.DEPOP, **listing_result, **order_result}


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
                "listing_id": str(listing.id),
                "image_urls": [
                    str(value).strip()
                    for value in (extra.get("image_urls") or [])
                    if str(value or "").strip()
                ][:5],
                "photo_sync_signature": extra.get("photo_sync_signature"),
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


BIBLIO_MAX_PHOTOS = 5
BIBLIO_MAX_SOURCE_IMAGE_BYTES = 25 * 1024 * 1024


def _biblio_photo_signature(urls: list[str]) -> str | None:
    clean = [str(value).strip() for value in urls if str(value or "").strip()][:BIBLIO_MAX_PHOTOS]
    if not clean:
        return None
    return hashlib.sha256("\n".join(clean).encode("utf-8")).hexdigest()


def _biblio_photo_filename(book_id: str, index: int) -> str:
    value = str(book_id or "").strip()
    if not value:
        raise ValueError("BIBLIO Book ID is empty")
    if any(char in value for char in ("/", "\\", "'", "\x00")):
        raise ValueError("BIBLIO Book ID contains characters that cannot be used in photo filenames")
    suffix = "" if index == 0 else f"_{index}"
    return f"{value}{suffix}.jpg"


def _trusted_vinted_image_url(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and (
            host.endswith(".vinted.net")
            or host == "vinted.net"
            or host.endswith(".vinted.com")
            or host == "vinted.com"
        )
    )


def _download_biblio_jpeg(url: str) -> bytes:
    if not _trusted_vinted_image_url(url):
        raise ValueError("Photo URL is not a trusted Vinted HTTPS image URL")
    response = requests.get(
        url,
        timeout=20,
        impersonate="chrome",
        headers={"Accept": "image/avif,image/webp,image/apng,image/jpeg,*/*;q=0.8"},
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Photo download returned HTTP {response.status_code}")
    body = bytes(response.content or b"")
    if not body:
        raise RuntimeError("Photo download returned no data")
    if len(body) > BIBLIO_MAX_SOURCE_IMAGE_BYTES:
        raise RuntimeError("Photo is larger than the 25 MB safety limit")

    try:
        with Image.open(io.BytesIO(body)) as image:
            image = ImageOps.exif_transpose(image)
            width, height = image.size
            if width < 80 or height < 1:
                raise ValueError("Photo is below BIBLIO's 80 px minimum width")
            ratio = width / height
            if ratio < 0.33 or ratio > 3:
                raise ValueError("Photo aspect ratio is outside BIBLIO's supported range")
            if image.mode != "RGB":
                image = image.convert("RGB")
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=92, optimize=True)
            return output.getvalue()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Photo could not be converted to JPG") from exc


def _pending_biblio_photo_rows(active: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pending: list[dict[str, Any]] = []
    for row in active:
        urls = [
            str(value).strip()
            for value in (row.get("image_urls") or [])
            if str(value or "").strip()
        ][:BIBLIO_MAX_PHOTOS]
        signature = _biblio_photo_signature(urls)
        if not signature or signature == row.get("photo_sync_signature"):
            continue
        pending.append({**row, "image_urls": urls, "photo_signature": signature})
    return pending


def _mark_biblio_photo_sync(
    workspace_id: uuid.UUID,
    synced: list[tuple[str, str, int]],
) -> None:
    if not synced:
        return
    now = datetime.now(timezone.utc).isoformat()
    by_id = {uuid.UUID(listing_id): (signature, count) for listing_id, signature, count in synced}
    with db.session_scope() as session:
        rows = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.id.in_(list(by_id)),
            )
        ).scalars().all()
        for listing in rows:
            signature, count = by_id[listing.id]
            extra = dict(listing.extra or {})
            extra["photo_sync_signature"] = signature
            extra["photo_synced_at"] = now
            extra["photo_count"] = count
            listing.extra = extra


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
    values = _workspace_or_env_biblio_values(workspace_id)
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
    values = _workspace_or_env_biblio_values(workspace_id)
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
    photo_rows = _pending_biblio_photo_rows(active)
    started = datetime.now(timezone.utc)
    if not inventory_filename and not deletes_filename and not photo_rows:
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

    photo_synced: list[tuple[str, str, int]] = []
    photo_errors: list[str] = []
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

        for row in photo_rows:
            uploaded = 0
            book_id = str(row.get("sku") or row.get("source_id") or "").strip()
            for index, url in enumerate(row.get("image_urls") or []):
                try:
                    filename = _biblio_photo_filename(book_id, index)
                    jpeg = _download_biblio_jpeg(url)
                    ftp.storbinary(f"STOR {filename}", io.BytesIO(jpeg))
                    uploaded += 1
                except Exception as exc:
                    photo_errors.append(f"{book_id} photo {index + 1}: {exc}")
            if uploaded == len(row.get("image_urls") or []) and uploaded > 0:
                photo_synced.append(
                    (str(row["listing_id"]), str(row["photo_signature"]), uploaded)
                )
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
                "photos_pending": len(photo_rows),
                "photos_uploaded": sum(count for _listing_id, _signature, count in photo_synced),
                "photo_errors": photo_errors[:20],
            },
            error=str(exc),
        )
        raise RuntimeError("BIBLIO FTP sync failed") from exc

    _mark_biblio_photo_sync(workspace_id, photo_synced)

    _record_biblio_run(
        workspace_id,
        status=SyncRunStatus.SUCCESS,
        started_at=started,
        active_count=len(active),
        delete_count=len(deletes),
        detail={
            "inventory_filename": inventory_filename,
            "deletes_filename": deletes_filename,
            "photos_pending": len(photo_rows),
            "photos_uploaded": sum(count for _listing_id, _signature, count in photo_synced),
            "photo_errors": photo_errors[:20],
        },
    )
    return {
        "ok": True,
        "active": len(active),
        "deletes": len(deletes),
        "inventory_filename": inventory_filename,
        "deletes_filename": deletes_filename,
        "photos_uploaded": sum(count for _listing_id, _signature, count in photo_synced),
        "photo_errors": photo_errors,
    }


def _workspace_or_env_ebay_values(workspace_id: uuid.UUID) -> dict[str, str]:
    if has_credentials(workspace_id, Channel.EBAY):
        return _credentials(workspace_id, Channel.EBAY)
    if not _is_bootstrap_workspace(workspace_id):
        raise RuntimeError("eBay credentials are not configured for this workspace")
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
    if not _is_bootstrap_workspace(workspace_id):
        raise RuntimeError("BIBLIO credentials are not configured for this workspace")
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
