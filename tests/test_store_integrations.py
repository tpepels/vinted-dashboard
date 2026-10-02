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
    for channel in (
        Channel.SHOPIFY,
        Channel.BIGCOMMERCE,
        Channel.SQUARESPACE,
        Channel.WIX,
    ):
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



def test_wix_sync_imports_variants_inventory_and_orders(monkeypatch):
    workspace_id = _workspace("wix-sync")
    site_id = "11111111-2222-3333-4444-555555555555"
    _credential(
        workspace_id,
        Channel.WIX,
        {
            "site_id": site_id,
            "api_key": "wix-api-key",
            "currency": "EUR",
            "order_days": "365",
        },
    )

    def fake_post(url, **kwargs):
        assert kwargs["headers"]["Authorization"] == "wix-api-key"
        assert kwargs["headers"]["wix-site-id"] == site_id
        body = kwargs["json"]

        if url.endswith("/stores/v3/products/query-variants"):
            assert body["fields"] == ["CURRENCY"]
            return FakeResponse(
                {
                    "variants": [
                        {
                            "variantId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                            "visible": True,
                            "sku": "WIX-COAT-M",
                            "optionChoices": [
                                {
                                    "optionChoiceNames": {
                                        "optionName": "Size",
                                        "choiceName": "M",
                                        "renderType": "TEXT_CHOICES",
                                    }
                                },
                                {
                                    "optionChoiceNames": {
                                        "optionName": "Colour",
                                        "choiceName": "Blue",
                                        "renderType": "TEXT_CHOICES",
                                    }
                                },
                            ],
                            "price": {
                                "actualPrice": {
                                    "amount": "72.50",
                                    "formattedAmount": "€72.50",
                                }
                            },
                            "inventoryStatus": {
                                "inStock": True,
                                "preorderEnabled": False,
                            },
                            "productData": {
                                "productId": "99999999-8888-7777-6666-555555555555",
                                "name": "Wool coat",
                                "productType": "PHYSICAL",
                                "visible": True,
                                "currency": "EUR",
                                "directCategoryIds": ["outerwear"],
                            },
                        }
                    ],
                    "pagingMetadata": {"cursors": {}},
                }
            )

        if url.endswith("/stores/v3/inventory-items/query"):
            return FakeResponse(
                {
                    "inventoryItems": [
                        {
                            "id": "inventory-one",
                            "productId": "99999999-8888-7777-6666-555555555555",
                            "variantId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                            "locationId": "location-one",
                            "trackQuantity": True,
                            "quantity": 2,
                            "inStock": True,
                        },
                        {
                            "id": "inventory-two",
                            "productId": "99999999-8888-7777-6666-555555555555",
                            "variantId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                            "locationId": "location-two",
                            "trackQuantity": True,
                            "quantity": 1,
                            "inStock": True,
                        },
                    ],
                    "pagingMetadata": {"cursors": {}},
                }
            )

        if url.endswith("/ecom/v1/orders/search"):
            return FakeResponse(
                {
                    "orders": [
                        {
                            "id": "order-wix-1",
                            "number": "10042",
                            "createdDate": "2026-02-10T12:00:00Z",
                            "status": "APPROVED",
                            "paymentStatus": "PAID",
                            "fulfillmentStatus": "FULFILLED",
                            "currency": "EUR",
                            "lineItems": [
                                {
                                    "id": "line-wix-1",
                                    "productName": {"original": "Wool coat"},
                                    "catalogReference": {
                                        "catalogItemId": "99999999-8888-7777-6666-555555555555",
                                        "appId": hosted.WIX_STORES_APP_ID,
                                        "options": {
                                            "variantId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
                                        },
                                    },
                                    "quantity": 1,
                                    "physicalProperties": {"sku": "WIX-COAT-M"},
                                    "price": {"amount": "72.50"},
                                }
                            ],
                        }
                    ],
                    "metadata": {"cursors": {}},
                }
            )
        raise AssertionError(url)

    monkeypatch.setattr(hosted.requests, "post", fake_post)
    result = hosted.sync_wix_workspace(workspace_id)
    assert result["items"] == 1
    assert result["orders"] == 1
    assert result["linked"] == 1

    with db.session_scope() as session:
        item = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == workspace_id,
                models.InventoryItem.sku == "WIX-COAT-M",
            )
        ).scalar_one()
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.channel == Channel.WIX,
            )
        ).scalar_one()
        sale = session.execute(
            select(models.Sale).where(
                models.Sale.workspace_id == workspace_id,
                models.Sale.channel == Channel.WIX,
            )
        ).scalar_one()

        assert item.quantity == 3
        assert listing.external_id == (
            "99999999-8888-7777-6666-555555555555:"
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        )
        assert listing.external_sku == "WIX-COAT-M"
        assert listing.price_cents == 7250
        assert listing.extra["remote_metadata"]["attributes"] == {
            "Size": "M",
            "Colour": "Blue",
            "category_ids": ["outerwear"],
        }
        assert sale.inventory_item_id == item.id
        assert sale.external_order_id == "order-wix-1:line-wix-1"
        assert sale.total_cents == 7250
        assert sale.status == "completed"


def test_wix_credentials_are_accepted(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "wix@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Wix",
        },
    )
    assert registered.status_code == 200
    csrf = registered.json()["csrf_token"]

    saved = client.put(
        "/api/app/connectors/wix/credentials",
        headers={"X-CSRF-Token": csrf},
        json={
            "values": {
                "site_id": "11111111-2222-3333-4444-555555555555",
                "api_key": "wix-api-key",
                "order_days": "365",
            }
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["operational"] is True

    connectors = {
        row["channel"]: row
        for row in client.get("/api/app/connectors").json()["connectors"]
    }
    assert connectors["wix"]["configured"] is True
    assert connectors["wix"]["operational"] is True
    assert connectors["wix"]["sync_available"] is True


def test_wix_rejects_invalid_site_id(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "wix-invalid@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Wix Invalid",
        },
    )
    csrf = registered.json()["csrf_token"]

    response = client.put(
        "/api/app/connectors/wix/credentials",
        headers={"X-CSRF-Token": csrf},
        json={
            "values": {
                "site_id": "../not-a-site",
                "api_key": "wix-api-key",
            }
        },
    )
    assert response.status_code == 400
    assert "valid site UUID" in response.json()["detail"]


def test_wix_ignores_non_store_order_lines(monkeypatch):
    workspace_id = _workspace("wix-non-store-line")
    _credential(
        workspace_id,
        Channel.WIX,
        {
            "site_id": "11111111-2222-3333-4444-555555555555",
            "api_key": "wix-api-key",
        },
    )

    monkeypatch.setattr(
        hosted,
        "_wix_post",
        lambda values, path, body=None: {
            "orders": [
                {
                    "id": "order-1",
                    "status": "APPROVED",
                    "paymentStatus": "PAID",
                    "lineItems": [
                        {
                            "id": "line-other",
                            "catalogReference": {
                                "catalogItemId": "external-item",
                                "appId": "some-other-wix-app",
                            },
                            "quantity": 1,
                            "price": {"amount": "10"},
                        }
                    ],
                }
            ]
        }
        if path == "ecom/v1/orders/search"
        else {},
    )
    assert hosted._fetch_wix_orders(
        {
            "site_id": "11111111-2222-3333-4444-555555555555",
            "api_key": "wix-api-key",
        }
    ) == []



def test_catalog_exposes_depop_as_marketplace():
    catalog = {row["channel"]: row for row in connector_catalog()}
    assert catalog[Channel.DEPOP]["group"] == "marketplace"
    assert Capability.FETCH_LISTINGS in catalog[Channel.DEPOP]["capabilities"]
    assert Capability.FETCH_ORDERS in catalog[Channel.DEPOP]["capabilities"]


def test_depop_sync_imports_products_and_orders(monkeypatch):
    workspace_id = _workspace("depop-sync")
    _credential(
        workspace_id,
        Channel.DEPOP,
        {
            "api_key": "pak_test_depop",
            "environment": "staging",
            "currency": "EUR",
            "order_days": "365",
        },
    )

    def fake_get(url, **kwargs):
        assert url.startswith("https://partnerapi-staging.depop.com/")
        assert kwargs["headers"]["Authorization"] == "Bearer pak_test_depop"

        if url.endswith("/api/v1/products/"):
            assert kwargs["params"]["state"] == "all"
            assert kwargs["params"]["limit"] == 100
            return FakeResponse(
                {
                    "meta": {"cursor": None, "has_more": False},
                    "data": [
                        {
                            "sku": "DEP-COAT-1",
                            "product_id": 7033001,
                            "slug": "vintage-wool-coat-7033001",
                            "status": "STATUS_ONSALE",
                            "description": "Vintage wool coat\nExcellent condition.",
                            "price_currency": "EUR",
                            "price_amount": "35.00",
                            "current_price": "31.50",
                            "quantity": 2,
                            "pictures": [
                                {
                                    "url": "https://media-photos-staging.depop.com/coat/P0.jpg",
                                    "height": 1280,
                                    "width": 1280,
                                }
                            ],
                            "department": "menswear",
                            "product_type": "coats-jackets",
                            "condition": "used_excellent",
                            "colour": ["blue"],
                            "style": ["vintage"],
                            "source": ["preloved"],
                            "attributes": {"material": ["wool"]},
                            "brand": "barbour",
                            "created_at": "2026-01-15T10:30:00Z",
                            "updated_at": "2026-01-20T14:20:00Z",
                        }
                    ],
                }
            )

        if url.endswith("/api/v1/orders/"):
            assert kwargs["params"]["limit"] == 200
            assert "from" in kwargs["params"]
            return FakeResponse(
                {
                    "meta": {"cursor": None, "has_more": False},
                    "data": [
                        {
                            "seller_id": 123456,
                            "purchase_id": "purchase-42",
                            "status": "SHIPPING_PENDING",
                            "currency": "EUR",
                            "buyer_pays_amount": "35.50",
                            "seller_receives_amount": "29.50",
                            "buyer_shipping_price": "4.00",
                            "line_items": [
                                {
                                    "purchase_item_id": 2385551,
                                    "sku": "DEP-COAT-1",
                                    "product_id": 7033001,
                                    "slug": "vintage-wool-coat-7033001",
                                    "parcel_id": "parcel-1",
                                    "description": "Vintage wool coat",
                                    "original_price": "35.00",
                                    "sold_price": "31.50",
                                    "sold_via_offers": True,
                                    "image_url": "https://media-photos-staging.depop.com/coat/P0.jpg",
                                }
                            ],
                            "seller_fee_breakdown": [
                                {
                                    "fee_type": "PAYMENT_FEE",
                                    "amount": "2.00",
                                    "currency": "EUR",
                                }
                            ],
                            "buyer_address": {
                                "name": "Must not be persisted",
                                "address": "Private address",
                            },
                            "created_at": "2026-02-10T12:00:00Z",
                        }
                    ],
                }
            )
        raise AssertionError(url)

    monkeypatch.setattr(hosted.requests, "get", fake_get)
    result = hosted.sync_depop_workspace(workspace_id)
    assert result["items"] == 1
    assert result["orders"] == 1
    assert result["linked"] == 1

    with db.session_scope() as session:
        item = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == workspace_id,
                models.InventoryItem.sku == "DEP-COAT-1",
            )
        ).scalar_one()
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.channel == Channel.DEPOP,
            )
        ).scalar_one()
        sale = session.execute(
            select(models.Sale).where(
                models.Sale.workspace_id == workspace_id,
                models.Sale.channel == Channel.DEPOP,
            )
        ).scalar_one()

        assert item.quantity == 2
        assert item.attributes["brand"] == "barbour"
        assert listing.external_id == "7033001"
        assert listing.external_sku == "DEP-COAT-1"
        assert listing.price_cents == 3150
        assert listing.url == "https://www.depop.com/products/vintage-wool-coat-7033001/"
        assert listing.extra["remote_metadata"]["attributes"] == {
            "material": ["wool"],
            "department": "menswear",
            "colour": ["blue"],
            "style": ["vintage"],
            "source": ["preloved"],
        }
        assert sale.inventory_item_id == item.id
        assert sale.external_order_id == "purchase-42:2385551"
        assert sale.total_cents == 3150
        assert sale.status == "shipping_pending"
        assert "buyer_address" not in (sale.extra or {})


def test_depop_credentials_are_accepted(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "depop@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Depop",
        },
    )
    assert registered.status_code == 200
    csrf = registered.json()["csrf_token"]

    saved = client.put(
        "/api/app/connectors/depop/credentials",
        headers={"X-CSRF-Token": csrf},
        json={
            "values": {
                "api_key": "pak_test_depop",
                "environment": "production",
                "order_days": "365",
            }
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["operational"] is True

    connectors = {
        row["channel"]: row
        for row in client.get("/api/app/connectors").json()["connectors"]
    }
    assert connectors["depop"]["configured"] is True
    assert connectors["depop"]["operational"] is True
    assert connectors["depop"]["sync_available"] is True
    assert "partner approval" in connectors["depop"]["note"]


def test_depop_rejects_invalid_environment(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "depop-invalid@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Depop Invalid",
        },
    )
    csrf = registered.json()["csrf_token"]

    response = client.put(
        "/api/app/connectors/depop/credentials",
        headers={"X-CSRF-Token": csrf},
        json={
            "values": {
                "api_key": "pak_test_depop",
                "environment": "local",
            }
        },
    )
    assert response.status_code == 400
    assert "production or staging" in response.json()["detail"]


def test_depop_pagination_uses_api_cursor(monkeypatch):
    calls = []

    def fake_get(values, path, params=None):
        calls.append(dict(params or {}))
        if len(calls) == 1:
            return {
                "meta": {"cursor": "next-page", "has_more": True},
                "data": [],
            }
        return {
            "meta": {"cursor": None, "has_more": False},
            "data": [],
        }

    monkeypatch.setattr(hosted, "_depop_get", fake_get)
    assert hosted._fetch_depop_products(
        {"api_key": "pak_test_depop", "environment": "production"}
    ) == []
    assert calls[0]["state"] == "all"
    assert "cursor" not in calls[0]
    assert calls[1]["cursor"] == "next-page"
