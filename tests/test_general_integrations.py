from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.connectors import hosted
from app.connectors.base import Capability, connector_catalog
from app.constants import Channel, ItemCategory
from app.crypto import encrypt_json
from app.product_models import ConnectorCredential


NOW = datetime.now(timezone.utc)


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


def test_connector_catalog_exposes_general_marketplaces():
    catalog = {row["channel"]: row for row in connector_catalog()}
    assert catalog[Channel.ETSY]["group"] == "marketplace"
    assert Capability.FETCH_LISTINGS in catalog[Channel.ETSY]["capabilities"]
    assert Capability.FETCH_ORDERS in catalog[Channel.ETSY]["capabilities"]
    assert catalog[Channel.WOOCOMMERCE]["group"] == "store"
    assert Capability.FETCH_LISTINGS in catalog[Channel.WOOCOMMERCE]["capabilities"]
    assert Capability.FETCH_ORDERS in catalog[Channel.WOOCOMMERCE]["capabilities"]


def test_etsy_sync_imports_listing_metadata_and_links_order(monkeypatch):
    workspace_id = _workspace("etsy-sync")
    _credential(
        workspace_id,
        Channel.ETSY,
        {
            "keystring": "key",
            "shared_secret": "secret",
            "oauth_token": "123.token",
            "shop_id": "42",
            "currency": "EUR",
            "order_days": "365",
        },
    )

    def fake_get(url, **kwargs):
        assert kwargs["headers"]["x-api-key"] == "key:secret"
        assert kwargs["headers"]["Authorization"] == "Bearer 123.token"
        if url.endswith("/shops/42/listings"):
            return FakeResponse(
                {
                    "count": 1,
                    "results": [
                        {
                            "listing_id": 100,
                            "title": "Handmade wool scarf",
                            "description": "Blue winter scarf",
                            "state": "active",
                            "creation_timestamp": 1_700_000_000,
                            "quantity": 2,
                            "url": "https://www.etsy.com/listing/100",
                            "price": {
                                "amount": 2500,
                                "divisor": 100,
                                "currency_code": "EUR",
                            },
                            "skus": ["SCARF-1"],
                            "materials": ["wool"],
                            "tags": ["winter", "blue"],
                            "listing_type": "physical",
                            "taxonomy_id": 123,
                        }
                    ],
                }
            )
        if url.endswith("/shops/42/receipts"):
            return FakeResponse(
                {
                    "count": 1,
                    "results": [
                        {
                            "receipt_id": 500,
                            "was_paid": True,
                            "was_shipped": True,
                            "was_canceled": False,
                            "name": "Buyer",
                            "created_timestamp": 1_700_100_000,
                            "transactions": [
                                {
                                    "transaction_id": 501,
                                    "listing_id": 100,
                                    "sku": "SCARF-1",
                                    "title": "Handmade wool scarf",
                                    "price": {
                                        "amount": 2500,
                                        "divisor": 100,
                                        "currency_code": "EUR",
                                    },
                                    "quantity": 1,
                                    "created_timestamp": 1_700_100_000,
                                }
                            ],
                        }
                    ],
                }
            )
        raise AssertionError(url)

    monkeypatch.setattr(hosted.requests, "get", fake_get)
    result = hosted.sync_etsy_workspace(workspace_id)
    assert result["items"] == 1
    assert result["active"] == 1
    assert result["orders"] == 1
    assert result["linked"] == 1

    with db.session_scope() as session:
        item = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == workspace_id,
                models.InventoryItem.sku == "SCARF-1",
            )
        ).scalar_one()
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.channel == Channel.ETSY,
            )
        ).scalar_one()
        sale = session.execute(
            select(models.Sale).where(
                models.Sale.workspace_id == workspace_id,
                models.Sale.channel == Channel.ETSY,
            )
        ).scalar_one()

        assert item.title == "Handmade wool scarf"
        assert listing.price_cents == 2500
        assert listing.extra["remote_metadata"]["material"] == "wool"
        assert listing.extra["remote_metadata"]["tags"] == ["winter", "blue"]
        assert sale.external_order_id == "500:501"
        assert sale.inventory_item_id == item.id
        assert sale.total_cents == 2500
        assert sale.status == "completed"


def test_woocommerce_sync_supports_clothing_and_variations(monkeypatch):
    workspace_id = _workspace("woo-sync")
    _credential(
        workspace_id,
        Channel.WOOCOMMERCE,
        {
            "store_url": "https://shop.example.test",
            "consumer_key": "ck_test",
            "consumer_secret": "cs_test",
            "currency": "EUR",
            "order_days": "365",
        },
    )

    def fake_get(url, **kwargs):
        assert kwargs["headers"]["Authorization"].startswith("Basic ")
        if url.endswith("/wp-json/wc/v3/products"):
            return FakeResponse(
                [
                    {
                        "id": 200,
                        "name": "Vintage jacket",
                        "type": "variable",
                        "status": "publish",
                        "permalink": "https://shop.example.test/product/jacket",
                        "description": "A vintage outdoor jacket",
                        "date_created_gmt": "2026-01-01T10:00:00",
                        "categories": [{"id": 1, "name": "Clothing"}],
                        "brands": [{"id": 2, "name": "Patagonia"}],
                        "tags": [{"id": 3, "name": "outdoor"}],
                        "images": [{"src": "https://shop.example.test/jacket.jpg"}],
                    }
                ]
            )
        if url.endswith("/wp-json/wc/v3/products/200/variations"):
            return FakeResponse(
                [
                    {
                        "id": 201,
                        "sku": "JACKET-M-BLUE",
                        "price": "39.50",
                        "stock_quantity": 1,
                        "stock_status": "instock",
                        "date_created_gmt": "2026-01-02T10:00:00",
                        "attributes": [
                            {"name": "Size", "option": "M"},
                            {"name": "Colour", "option": "Blue"},
                        ],
                    }
                ]
            )
        if url.endswith("/wp-json/wc/v3/orders"):
            return FakeResponse(
                [
                    {
                        "id": 900,
                        "status": "processing",
                        "currency": "EUR",
                        "date_created_gmt": "2026-09-30T12:00:00",
                        "billing": {"first_name": "Jane", "last_name": "Doe"},
                        "line_items": [
                            {
                                "id": 901,
                                "product_id": 200,
                                "variation_id": 201,
                                "sku": "JACKET-M-BLUE",
                                "name": "Vintage jacket - M / Blue",
                                "quantity": 1,
                                "total": "39.50",
                            }
                        ],
                    }
                ]
            )
        raise AssertionError(url)

    monkeypatch.setattr(hosted.requests, "get", fake_get)
    result = hosted.sync_woocommerce_workspace(workspace_id)
    assert result["items"] == 1
    assert result["orders"] == 1
    assert result["linked"] == 1

    with db.session_scope() as session:
        item = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == workspace_id,
                models.InventoryItem.sku == "JACKET-M-BLUE",
            )
        ).scalar_one()
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.channel == Channel.WOOCOMMERCE,
            )
        ).scalar_one()
        sale = session.execute(
            select(models.Sale).where(
                models.Sale.workspace_id == workspace_id,
                models.Sale.channel == Channel.WOOCOMMERCE,
            )
        ).scalar_one()

        assert item.category == ItemCategory.CLOTHING
        assert item.attributes["brand"] == "Patagonia"
        assert item.attributes["marketplace_category"] == "Clothing"
        assert listing.external_id == "200:201"
        assert listing.external_sku == "JACKET-M-BLUE"
        assert listing.price_cents == 3950
        assert listing.extra["remote_metadata"]["attributes"] == {
            "Size": "M",
            "Colour": "Blue",
        }
        assert sale.external_order_id == "900:901"
        assert sale.inventory_item_id == item.id
        assert sale.total_cents == 3950
        assert sale.status == "processing"


def test_connector_credentials_api_accepts_etsy_and_woocommerce(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "integrations@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Integrations",
        },
    )
    assert registered.status_code == 200
    csrf = registered.json()["csrf_token"]

    etsy = client.put(
        "/api/app/connectors/etsy/credentials",
        headers={"X-CSRF-Token": csrf},
        json={
            "values": {
                "keystring": "key",
                "shared_secret": "secret",
                "shop_id": "42",
                "oauth_token": "123.token",
            }
        },
    )
    assert etsy.status_code == 200, etsy.text

    woo = client.put(
        "/api/app/connectors/woocommerce/credentials",
        headers={"X-CSRF-Token": csrf},
        json={
            "values": {
                "store_url": "https://shop.example.test",
                "consumer_key": "ck_test",
                "consumer_secret": "cs_test",
            }
        },
    )
    assert woo.status_code == 200, woo.text

    connectors = {
        row["channel"]: row
        for row in client.get("/api/app/connectors").json()["connectors"]
    }
    assert connectors["etsy"]["configured"] is True
    assert connectors["etsy"]["sync_available"] is True
    assert connectors["woocommerce"]["configured"] is True
    assert connectors["woocommerce"]["sync_available"] is True


def test_woocommerce_rejects_non_https_store_url(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "woo-http@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Woo HTTP",
        },
    )
    csrf = registered.json()["csrf_token"]
    response = client.put(
        "/api/app/connectors/woocommerce/credentials",
        headers={"X-CSRF-Token": csrf},
        json={
            "values": {
                "store_url": "http://shop.example.test",
                "consumer_key": "ck_test",
                "consumer_secret": "cs_test",
            }
        },
    )
    assert response.status_code == 400
    assert "HTTPS" in response.json()["detail"]
