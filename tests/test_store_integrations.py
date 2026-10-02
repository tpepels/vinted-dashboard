from __future__ import annotations

from datetime import datetime, timezone
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.connectors import hosted
from app.connectors.base import Capability, connector_catalog
from app.constants import Channel, ItemCategory
from app.crypto import encrypt_json
from app.product_models import ConnectorCredential


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _workspace(slug: str) -> uuid.UUID:
    with db.session_scope() as session:
        workspace = models.Workspace(name=slug, slug=slug, settings={})
        session.add(workspace)
        session.flush()
        return workspace.id


def _credential(workspace_id: uuid.UUID, channel: str, values: dict[str, str]) -> None:
    with db.session_scope() as session:
        session.add(
            ConnectorCredential(
                workspace_id=workspace_id,
                channel=channel,
                encrypted_payload=encrypt_json(values),
            )
        )


def test_catalog_exposes_store_connectors():
    catalog = {row["channel"]: row for row in connector_catalog()}
    for channel in (Channel.SHOPIFY, Channel.BIGCOMMERCE, Channel.SQUARESPACE):
        assert catalog[channel]["group"] == "store"
        assert Capability.FETCH_LISTINGS in catalog[channel]["capabilities"]
        assert Capability.FETCH_ORDERS in catalog[channel]["capabilities"]


def test_shopify_sync_imports_variants_and_orders(monkeypatch):
    workspace_id = _workspace("shopify-sync")
    _credential(
        workspace_id,
        Channel.SHOPIFY,
        {
            "store_domain": "example-store.myshopify.com",
            "access_token": "shpat_test",
            "api_version": "2026-10",
            "currency": "EUR",
            "order_days": "60",
        },
    )

    def fake_post(url, **kwargs):
        assert url == "https://example-store.myshopify.com/admin/api/2026-10/graphql.json"
        assert kwargs["headers"]["X-Shopify-Access-Token"] == "shpat_test"
        query = kwargs["json"]["query"]
        if "productVariants" in query:
            return FakeResponse(
                {
                    "data": {
                        "productVariants": {
                            "nodes": [
                                {
                                    "id": "gid://shopify/ProductVariant/101",
                                    "displayName": "Ceramic mug - Blue",
                                    "title": "Blue",
                                    "sku": "MUG-BLUE",
                                    "price": "12.50",
                                    "inventoryQuantity": 3,
                                    "availableForSale": True,
                                    "createdAt": "2026-01-02T10:00:00Z",
                                    "selectedOptions": [
                                        {"name": "Colour", "value": "Blue"}
                                    ],
                                    "product": {
                                        "id": "gid://shopify/Product/100",
                                        "title": "Ceramic mug",
                                        "status": "ACTIVE",
                                        "productType": "Home decor",
                                        "vendor": "Studio",
                                        "tags": ["ceramic", "blue"],
                                        "onlineStoreUrl": "https://shop.example.test/products/mug",
                                        "featuredMedia": {
                                            "preview": {
                                                "image": {
                                                    "url": "https://cdn.example.test/mug.jpg"
                                                }
                                            }
                                        },
                                    },
                                }
                            ],
                            "pageInfo": {
                                "hasNextPage": False,
                                "endCursor": "variant-end",
                            },
                        }
                    }
                }
            )
        if "orders(" in query:
            return FakeResponse(
                {
                    "data": {
                        "orders": {
                            "nodes": [
                                {
                                    "id": "gid://shopify/Order/200",
                                    "name": "#1001",
                                    "createdAt": "2026-02-03T12:00:00Z",
                                    "cancelledAt": None,
                                    "closed": True,
                                    "displayFinancialStatus": "PAID",
                                    "displayFulfillmentStatus": "FULFILLED",
                                    "currencyCode": "EUR",
                                    "lineItems": {
                                        "nodes": [
                                            {
                                                "id": "gid://shopify/LineItem/201",
                                                "name": "Ceramic mug - Blue",
                                                "sku": "MUG-BLUE",
                                                "quantity": 1,
                                                "currentQuantity": 1,
                                                "originalTotalSet": {
                                                    "shopMoney": {
                                                        "amount": "12.50",
                                                        "currencyCode": "EUR",
                                                    }
                                                },
                                                "variant": {
                                                    "id": "gid://shopify/ProductVariant/101"
                                                },
                                            }
                                        ]
                                    },
                                }
                            ],
                            "pageInfo": {
                                "hasNextPage": False,
                                "endCursor": "order-end",
                            },
                        }
                    }
                }
            )
        raise AssertionError(query)

    monkeypatch.setattr(hosted.requests, "post", fake_post)
    result = hosted.sync_shopify_workspace(workspace_id)
    assert result["items"] == 1
    assert result["orders"] == 1
    assert result["linked"] == 1

    with db.session_scope() as session:
        item = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == workspace_id,
                models.InventoryItem.sku == "MUG-BLUE",
            )
        ).scalar_one()
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.channel == Channel.SHOPIFY,
            )
        ).scalar_one()
        sale = session.execute(
            select(models.Sale).where(
                models.Sale.workspace_id == workspace_id,
                models.Sale.channel == Channel.SHOPIFY,
            )
        ).scalar_one()

        assert item.category == ItemCategory.HOME
        assert item.attributes["brand"] == "Studio"
        assert item.quantity == 3
        assert listing.external_id == "gid://shopify/ProductVariant/101"
        assert listing.price_cents == 1250
        assert listing.extra["remote_metadata"]["attributes"] == {"Colour": "Blue"}
        assert sale.inventory_item_id == item.id
        assert sale.total_cents == 1250
        assert sale.status == "completed"


def test_bigcommerce_sync_imports_variants_and_orders(monkeypatch):
    workspace_id = _workspace("bigcommerce-sync")
    _credential(
        workspace_id,
        Channel.BIGCOMMERCE,
        {
            "store_hash": "abc123",
            "access_token": "bc-token",
            "currency": "EUR",
            "order_days": "365",
        },
    )

    def fake_get(url, **kwargs):
        assert url.startswith("https://api.bigcommerce.com/stores/abc123/")
        assert kwargs["headers"]["X-Auth-Token"] == "bc-token"
        if url.endswith("/v3/catalog/products"):
            return FakeResponse(
                {
                    "data": [
                        {
                            "id": 300,
                            "name": "Retro lamp",
                            "sku": "",
                            "price": "59.95",
                            "inventory_tracking": "variant",
                            "is_visible": True,
                            "availability": "available",
                            "date_created": "2026-01-05T10:00:00+00:00",
                            "description": "Brass desk lamp",
                            "type": "physical",
                            "categories": [5],
                            "search_keywords": "lamp, brass",
                            "custom_url": {"url": "/retro-lamp/"},
                            "images": [
                                {"url_standard": "https://cdn.example.test/lamp.jpg"}
                            ],
                            "variants": [
                                {
                                    "id": 301,
                                    "sku": "LAMP-BRASS",
                                    "price": "59.95",
                                    "inventory_level": 2,
                                    "option_values": [
                                        {
                                            "option_display_name": "Material",
                                            "label": "Brass",
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                    "meta": {"pagination": {"total_pages": 1}},
                }
            )
        if url.endswith("/v2/orders"):
            return FakeResponse(
                [
                    {
                        "id": 400,
                        "status": "Completed",
                        "currency_code": "EUR",
                        "date_created": "Thu, 05 Feb 2026 12:00:00 +0000",
                    }
                ]
            )
        if url.endswith("/v2/orders/400/products"):
            return FakeResponse(
                [
                    {
                        "id": 401,
                        "product_id": 300,
                        "variant_id": 301,
                        "sku": "LAMP-BRASS",
                        "name": "Retro lamp - Brass",
                        "quantity": 1,
                        "total_inc_tax": "59.95",
                    }
                ]
            )
        raise AssertionError(url)

    monkeypatch.setattr(hosted.requests, "get", fake_get)
    result = hosted.sync_bigcommerce_workspace(workspace_id)
    assert result["items"] == 1
    assert result["orders"] == 1
    assert result["linked"] == 1

    with db.session_scope() as session:
        item = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == workspace_id,
                models.InventoryItem.sku == "LAMP-BRASS",
            )
        ).scalar_one()
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.channel == Channel.BIGCOMMERCE,
            )
        ).scalar_one()
        sale = session.execute(
            select(models.Sale).where(
                models.Sale.workspace_id == workspace_id,
                models.Sale.channel == Channel.BIGCOMMERCE,
            )
        ).scalar_one()

        assert item.quantity == 2
        assert listing.external_id == "300:301"
        assert listing.price_cents == 5995
        assert listing.url is None
        assert listing.extra["remote_metadata"]["attributes"] == {
            "category_ids": ["5"],
            "storefront_path": "/retro-lamp/",
            "Material": "Brass",
        }
        assert sale.inventory_item_id == item.id
        assert sale.total_cents == 5995
        assert sale.status == "completed"


def test_shopify_and_bigcommerce_credentials_are_accepted(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "stores@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Stores",
        },
    )
    assert registered.status_code == 200
    csrf = registered.json()["csrf_token"]

    shopify = client.put(
        "/api/app/connectors/shopify/credentials",
        headers={"X-CSRF-Token": csrf},
        json={
            "values": {
                "store_domain": "store.myshopify.com",
                "access_token": "shpat_test",
                "api_version": "2026-10",
            }
        },
    )
    assert shopify.status_code == 200, shopify.text

    bigcommerce = client.put(
        "/api/app/connectors/bigcommerce/credentials",
        headers={"X-CSRF-Token": csrf},
        json={
            "values": {
                "store_hash": "abc123",
                "access_token": "bc-token",
            }
        },
    )
    assert bigcommerce.status_code == 200, bigcommerce.text

    connectors = {
        row["channel"]: row
        for row in client.get("/api/app/connectors").json()["connectors"]
    }
    assert connectors["shopify"]["configured"] is True
    assert connectors["shopify"]["sync_available"] is True
    assert connectors["bigcommerce"]["configured"] is True
    assert connectors["bigcommerce"]["sync_available"] is True


def test_shopify_rejects_non_shopify_admin_host(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "bad-shopify@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Bad Shopify",
        },
    )
    csrf = registered.json()["csrf_token"]
    response = client.put(
        "/api/app/connectors/shopify/credentials",
        headers={"X-CSRF-Token": csrf},
        json={
            "values": {
                "store_domain": "https://example.com",
                "access_token": "shpat_test",
            }
        },
    )
    assert response.status_code == 400
    assert "myshopify.com" in response.json()["detail"]


def test_bigcommerce_rejects_malformed_store_hash(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "bad-bigcommerce@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Bad BigCommerce",
        },
    )
    csrf = registered.json()["csrf_token"]
    response = client.put(
        "/api/app/connectors/bigcommerce/credentials",
        headers={"X-CSRF-Token": csrf},
        json={
            "values": {
                "store_hash": "../bad",
                "access_token": "bc-token",
            }
        },
    )
    assert response.status_code == 400
    assert "store_hash" in response.json()["detail"]



def test_squarespace_sync_imports_variants_inventory_and_orders(monkeypatch):
    workspace_id = _workspace("squarespace-sync")
    _credential(
        workspace_id,
        Channel.SQUARESPACE,
        {
            "access_token": "sqsp-test-token",
            "currency": "EUR",
            "order_days": "365",
        },
    )

    def fake_get(url, **kwargs):
        assert kwargs["headers"]["Authorization"] == "Bearer sqsp-test-token"
        assert kwargs["headers"]["User-Agent"] == "ResellerDashboard/1.0"
        if url.endswith("/1.0/authorization/website"):
            return FakeResponse(
                {
                    "id": "website-1",
                    "title": "Example Shop",
                    "currency": "EUR",
                    "url": "https://example.squarespace.com",
                }
            )
        if url.endswith("/1.0/commerce/inventory"):
            return FakeResponse(
                {
                    "inventory": [
                        {
                            "variantId": "variant-101",
                            "sku": "COAT-M-BLUE",
                            "descriptor": "M / Blue",
                            "isUnlimited": False,
                            "quantity": 2,
                        }
                    ],
                    "pagination": {"hasNextPage": False},
                }
            )
        if url.endswith("/v2/commerce/products"):
            return FakeResponse(
                {
                    "products": [
                        {
                            "id": "product-100",
                            "name": "Vintage coat",
                            "type": "PHYSICAL",
                            "isVisible": True,
                            "modifiedOn": "2026-01-05T12:00:00Z",
                        }
                    ],
                    "pagination": {"hasNextPage": False},
                }
            )
        if url.endswith("/v2/commerce/products/product-100"):
            return FakeResponse(
                {
                    "products": [
                        {
                            "id": "product-100",
                            "name": "Vintage coat",
                            "description": "Blue wool coat",
                            "type": "PHYSICAL",
                            "isVisible": True,
                            "createdOn": "2026-01-01T10:00:00Z",
                            "modifiedOn": "2026-01-05T12:00:00Z",
                            "url": "/shop/p/vintage-coat",
                            "tags": ["vintage", "coat"],
                            "images": [
                                {"url": "https://images.example.test/coat.jpg"}
                            ],
                            "variants": [
                                {
                                    "id": "variant-101",
                                    "sku": "COAT-M-BLUE",
                                    "attributes": {
                                        "Size": "M",
                                        "Colour": "Blue",
                                    },
                                    "pricing": {
                                        "basePrice": {
                                            "currency": "EUR",
                                            "value": 45.00,
                                        },
                                        "onSale": False,
                                        "salePrice": {
                                            "currency": "EUR",
                                            "value": 40.00,
                                        },
                                    },
                                }
                            ],
                        }
                    ]
                }
            )
        if url.endswith("/1.0/commerce/orders"):
            return FakeResponse(
                {
                    "result": [
                        {
                            "id": "order-200",
                            "orderNumber": "42",
                            "createdOn": "2026-02-01T12:00:00Z",
                            "modifiedOn": "2026-02-01T13:00:00Z",
                            "paymentState": "PAID",
                            "fulfillmentStatus": "FULFILLED",
                            "lineItems": [
                                {
                                    "id": "line-201",
                                    "productId": "product-100",
                                    "variantId": "variant-101",
                                    "productName": "Vintage coat",
                                    "sku": "COAT-M-BLUE",
                                    "quantity": 1,
                                    "unitPricePaid": {
                                        "currency": "EUR",
                                        "value": 45.00,
                                    },
                                }
                            ],
                        }
                    ],
                    "pagination": {"hasNextPage": False},
                }
            )
        raise AssertionError(url)

    monkeypatch.setattr(hosted.requests, "get", fake_get)
    result = hosted.sync_squarespace_workspace(workspace_id)
    assert result["items"] == 1
    assert result["orders"] == 1
    assert result["linked"] == 1

    with db.session_scope() as session:
        item = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == workspace_id,
                models.InventoryItem.sku == "COAT-M-BLUE",
            )
        ).scalar_one()
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.channel == Channel.SQUARESPACE,
            )
        ).scalar_one()
        sale = session.execute(
            select(models.Sale).where(
                models.Sale.workspace_id == workspace_id,
                models.Sale.channel == Channel.SQUARESPACE,
            )
        ).scalar_one()

        assert item.quantity == 2
        assert listing.external_id == "variant-101"
        assert listing.external_sku == "COAT-M-BLUE"
        assert listing.price_cents == 4500
        assert listing.url == "https://example.squarespace.com/shop/p/vintage-coat"
        assert listing.extra["remote_metadata"]["attributes"] == {
            "Size": "M",
            "Colour": "Blue",
        }
        assert sale.inventory_item_id == item.id
        assert sale.external_order_id == "order-200:line-201"
        assert sale.total_cents == 4500
        assert sale.status == "completed"


def test_squarespace_credentials_are_accepted(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "squarespace@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Squarespace",
        },
    )
    assert registered.status_code == 200
    csrf = registered.json()["csrf_token"]

    saved = client.put(
        "/api/app/connectors/squarespace/credentials",
        headers={"X-CSRF-Token": csrf},
        json={"values": {"access_token": "sqsp-api-key", "order_days": "365"}},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["operational"] is True

    connectors = {
        row["channel"]: row
        for row in client.get("/api/app/connectors").json()["connectors"]
    }
    assert connectors["squarespace"]["configured"] is True
    assert connectors["squarespace"]["operational"] is True
    assert connectors["squarespace"]["sync_available"] is True


def test_squarespace_credentials_require_token(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "squarespace-missing@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Squarespace Missing",
        },
    )
    csrf = registered.json()["csrf_token"]
    response = client.put(
        "/api/app/connectors/squarespace/credentials",
        headers={"X-CSRF-Token": csrf},
        json={"values": {"order_days": "365"}},
    )
    assert response.status_code == 400
    assert "API key or OAuth access token" in response.json()["detail"]
