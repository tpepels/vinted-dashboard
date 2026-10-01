from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.connectors import hosted
from app.constants import Channel
from app.crypto import encrypt_json
from app.product_models import ConnectorCredential


def _workspace(slug: str = "hosted"):
    with db.session_scope() as session:
        workspace = models.Workspace(name="Hosted", slug=slug, settings={})
        session.add(workspace)
        session.flush()
        ident = workspace.id
    return ident


def test_workspace_ebay_sync_uses_encrypted_credentials(monkeypatch):
    workspace_id = _workspace()
    with db.session_scope() as session:
        session.add(
            ConnectorCredential(
                workspace_id=workspace_id,
                channel=Channel.EBAY,
                encrypted_payload=encrypt_json({"oauth_token": "token", "site_id": "0"}),
            )
        )

    xml = b"""<?xml version="1.0" encoding="utf-8"?>
<GetMyeBaySellingResponse xmlns="urn:ebay:apis:eBLBaseComponents">
  <Ack>Success</Ack>
  <ActiveList>
    <ItemArray>
      <Item>
        <ItemID>123</ItemID>
        <Title>Vintage jacket</Title>
        <SKU>JACKET-1</SKU>
        <QuantityAvailable>1</QuantityAvailable>
        <SellingStatus><CurrentPrice currencyID="EUR">24.50</CurrentPrice></SellingStatus>
        <ListingDetails><ViewItemURL>https://example.test/123</ViewItemURL></ListingDetails>
      </Item>
    </ItemArray>
    <PaginationResult><TotalNumberOfPages>1</TotalNumberOfPages></PaginationResult>
  </ActiveList>
</GetMyeBaySellingResponse>"""

    def fake_post(url, **kwargs):
        assert url == "https://api.ebay.com/ws/api.dll"
        assert kwargs["headers"]["X-EBAY-API-IAF-TOKEN"] == "token"
        return SimpleNamespace(status_code=200, content=xml)

    monkeypatch.setattr(hosted.requests, "post", fake_post)
    result = hosted.sync_ebay_workspace(workspace_id)
    assert result["active"] == 1

    with db.session_scope() as session:
        item = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == workspace_id,
                models.InventoryItem.sku == "JACKET-1",
            )
        ).scalar_one()
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.channel == Channel.EBAY,
            )
        ).scalar_one()
        assert item.title == "Vintage jacket"
        assert listing.inventory_item_id == item.id
        assert listing.price_cents == 2450


def test_workspace_biblio_sync_uploads_master_listing(monkeypatch):
    workspace_id = _workspace()
    with db.session_scope() as session:
        session.add(
            ConnectorCredential(
                workspace_id=workspace_id,
                channel=Channel.BIBLIO,
                encrypted_payload=encrypt_json(
                    {
                        "host": "ftp.biblio.com",
                        "username": "seller",
                        "password": "secret",
                        "filename_prefix": "test",
                    }
                ),
            )
        )
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="BK-1",
            title="Stoner",
            category="book",
            quantity=1,
            currency="EUR",
            attributes={
                "author": "John Williams",
                "isbn": "9780099561545",
                "description": "Paperback, very good condition",
            },
        )
        session.add(item)
        session.flush()
        account = models.ChannelAccount(
            workspace_id=workspace_id,
            channel=Channel.BIBLIO,
            display_name="BIBLIO",
            status="connected",
            config={},
        )
        session.add(account)
        session.flush()
        session.add(
            models.ChannelListing(
                workspace_id=workspace_id,
                inventory_item_id=item.id,
                channel_account_id=account.id,
                channel=Channel.BIBLIO,
                external_id="BK-1",
                external_sku="BK-1",
                title="Stoner",
                price_cents=1200,
                currency="EUR",
                quantity=1,
                status="active",
                extra={
                    "author": "John Williams",
                    "description": "Paperback, very good condition",
                    "isbn": "9780099561545",
                },
            )
        )

    class FakeFTP:
        uploads = {}

        def connect(self, host, timeout=20):
            assert host == "ftp.biblio.com"

        def login(self, username, password):
            assert username == "seller"
            assert password == "secret"

        def set_pasv(self, value):
            assert value is True

        def cwd(self, _directory):
            return None

        def storbinary(self, command, stream):
            self.uploads[command.removeprefix("STOR ")] = stream.read()

        def quit(self):
            return None

        def close(self):
            return None

        def pwd(self):
            return "/"

    monkeypatch.setattr(hosted.ftplib, "FTP", FakeFTP)
    result = hosted.sync_biblio_workspace(workspace_id)
    assert result["active"] == 1
    assert result["deletes"] == 0
    payload = FakeFTP.uploads[result["inventory_filename"]].decode("utf-8")
    assert "BK-1\tJohn Williams\tStoner" in payload
    assert "12.00\tfor sale\t9780099561545\t1" in payload


def test_marketplace_export_contains_listing_columns():
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "exporter@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Exporter",
        },
    )
    assert registered.status_code == 200
    csrf = registered.json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf}
    created = client.post(
        "/api/app/inventory",
        headers=headers,
        json={"sku": "SKU-1", "title": "Example", "quantity": 1, "category": "general"},
    )
    item_id = created.json()["item"]["id"]

    with db.session_scope() as session:
        user_item = session.get(models.InventoryItem, uuid.UUID(item_id))
        account = models.ChannelAccount(
            workspace_id=user_item.workspace_id,
            channel=Channel.VINTED,
            display_name="Vinted",
            status="connected",
            config={},
        )
        session.add(account)
        session.flush()
        session.add(
            models.ChannelListing(
                workspace_id=user_item.workspace_id,
                inventory_item_id=user_item.id,
                channel_account_id=account.id,
                channel=Channel.VINTED,
                external_id="V-1",
                title="Example on Vinted",
                price_cents=900,
                currency="EUR",
                status="active",
                quantity=1,
            )
        )

    response = client.get("/api/app/export?format=csv&channel=vinted")
    assert response.status_code == 200
    text = response.content.decode("utf-8-sig")
    assert "Channel" in text
    assert "Listing ID" in text
    assert "Example on Vinted" in text


def test_workspace_native_analytics_history_reports_vinted_gains():
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "analytics@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Analytics",
        },
    )
    assert registered.status_code == 200
    csrf = registered.json()["csrf_token"]
    pair = client.post("/api/app/extension/pairings", headers={"X-CSRF-Token": csrf})
    token = TestClient(entry.app).post(
        "/api/extension/pair",
        json={"code": pair.json()["code"], "extension_version": "2.0.0"},
    ).json()["token"]
    auth = {"Authorization": "Bearer " + token}

    now = datetime.now(timezone.utc)
    first = (now - timedelta(days=2)).timestamp()
    second = (now - timedelta(days=1)).timestamp()

    def snapshot(at, views, favourites, followers):
        return {
            "collected_at": at,
            "current_user": {
                "id": "seller",
                "followers_count": followers,
                "following_count": 5,
            },
            "listings": [
                {
                    "id": "V-1",
                    "title": "Vintage coat",
                    "status": "active",
                    "price_cents": 3000,
                    "currency": "EUR",
                    "views": views,
                    "favourites": favourites,
                }
            ],
            "notifications": [],
            "orders": [],
            "market_results": [],
            "extension_version": "2.0.0",
        }

    assert TestClient(entry.app).post(
        "/api/extension/browser-sync", headers=auth, json=snapshot(first, 10, 2, 100)
    ).status_code == 200
    assert TestClient(entry.app).post(
        "/api/extension/browser-sync", headers=auth, json=snapshot(second, 18, 5, 104)
    ).status_code == 200

    history = client.get("/api/app/analytics/history?days=30")
    assert history.status_code == 200, history.text
    data = history.json()
    assert sum(row["views_gained"] for row in data["daily"]) == 8
    assert sum(row["favourites_gained"] for row in data["daily"]) == 3
    assert data["followers"][-1]["followers"] == 104
    assert data["top_listings"][0]["views_gain_7d"] == 8



def test_product_listings_feed_includes_latest_snapshot_and_all_statuses():
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "listings@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Listings",
        },
    )
    assert registered.status_code == 200
    csrf = registered.json()["csrf_token"]
    created = client.post(
        "/api/app/inventory",
        headers={"X-CSRF-Token": csrf},
        json={"sku": "L-1", "title": "Listing item", "quantity": 1, "category": "general"},
    )
    item_id = uuid.UUID(created.json()["item"]["id"])

    now = datetime.now(timezone.utc)
    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        account = models.ChannelAccount(
            workspace_id=item.workspace_id,
            channel=Channel.VINTED,
            display_name="Vinted",
            status="connected",
            config={},
        )
        session.add(account)
        session.flush()
        active = models.ChannelListing(
            workspace_id=item.workspace_id,
            inventory_item_id=item.id,
            channel_account_id=account.id,
            channel=Channel.VINTED,
            external_id="V-active",
            external_sku="SKU-A",
            title="Same title",
            price_cents=1200,
            currency="EUR",
            status="active",
            quantity=1,
            first_seen_at=now - timedelta(days=20),
            last_seen_at=now,
            extra={"listed_at": (now - timedelta(days=30)).isoformat()},
        )
        sold = models.ChannelListing(
            workspace_id=item.workspace_id,
            inventory_item_id=item.id,
            channel_account_id=account.id,
            channel=Channel.VINTED,
            external_id="V-sold",
            title="Sold title",
            price_cents=900,
            currency="EUR",
            status="sold",
            quantity=0,
            first_seen_at=now - timedelta(days=40),
            last_seen_at=now,
            extra={},
        )
        session.add_all([active, sold])
        session.flush()
        session.add_all(
            [
                models.ListingSnapshot(
                    channel_listing_id=active.id,
                    captured_at=now - timedelta(days=2),
                    price_cents=1200,
                    status="active",
                    views=10,
                    favourites=2,
                ),
                models.ListingSnapshot(
                    channel_listing_id=active.id,
                    captured_at=now - timedelta(days=1),
                    price_cents=1200,
                    status="active",
                    views=18,
                    favourites=5,
                ),
            ]
        )

    response = client.get("/api/app/listings")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["count"] == 2
    rows = {row["external_id"]: row for row in data["listings"]}
    assert set(rows) == {"V-active", "V-sold"}
    assert rows["V-active"]["views"] == 18
    assert rows["V-active"]["favourites"] == 5
    assert rows["V-active"]["external_sku"] == "SKU-A"
    assert rows["V-active"]["listed_at"].startswith((now - timedelta(days=30)).date().isoformat())
    assert rows["V-sold"]["status"] == "sold"

    vinted = client.get("/api/app/listings?channel=vinted")
    assert vinted.status_code == 200
    assert vinted.json()["count"] == 2
