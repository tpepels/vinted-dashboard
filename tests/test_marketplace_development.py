"""Connector operation definitions must match implemented paths, not UI optimism."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.constants import Channel
from app.connectors.base import Capability, CONNECTORS
from app.connectors.development import COVERAGE, OPERATIONS, STATES, contract
from app.connectors.workspace_sync import record_workspace_channel_snapshot


def test_contract_is_exhaustive_and_keeps_implementation_separate_from_verification():
    data = contract()
    assert data["version"] == 1
    keys = [row["key"] for row in data["operations"]]
    assert len(set(keys)) == len(keys) == 10
    assert set(COVERAGE) == {channel for channel in CONNECTORS if channel not in {Channel.CSV, Channel.EXCEL}}
    assert set(STATES) == {"implemented", "partial", "manual", "blocked", "missing"}
    assert len(data["channels"]) == len(COVERAGE)
    for row in data["channels"]:
        assert list(row["operations"]) == keys
        assert all(operation["status"] in STATES for operation in row["operations"].values())
        assert sum(row["coverage"].values()) == len(OPERATIONS)
        assert all(operation["evidence"] is None or isinstance(operation["evidence"], str)
                   for operation in row["operations"].values())


def test_marketplace_claims_reflect_code_boundaries():
    by_channel = {row["channel"]: row["operations"] for row in contract()["channels"]}
    assert by_channel[Channel.EBAY]["read_orders"]["status"] == "missing"
    assert Capability.FETCH_ORDERS not in CONNECTORS[Channel.EBAY].capabilities
    assert by_channel[Channel.EBAY]["close"]["status"] == "implemented"
    assert by_channel[Channel.BIBLIO]["photos"]["status"] == "partial"
    assert by_channel[Channel.BIBLIO]["verify"]["status"] == "partial"
    assert by_channel[Channel.BIBLIO]["read_orders"]["status"] == "blocked"
    assert by_channel[Channel.DEPOP]["connect"]["status"] == "blocked"
    assert by_channel[Channel.WOOCOMMERCE]["publish"]["status"] == "implemented"
    assert by_channel[Channel.WOOCOMMERCE]["update"]["status"] == "partial"
    assert by_channel[Channel.WOOCOMMERCE]["stock"]["status"] == "partial"
    assert by_channel[Channel.VINTED]["close"]["status"] == "manual"


def _registered(email):
    client = TestClient(entry.app)
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "a-long-test-password", "workspace_name": "Market audit"},
    )
    assert response.status_code == 200, response.text
    return client


def test_development_endpoint_reports_workspace_only_and_provisional_relations():
    first = _registered("market-audit-one@example.test")
    with db.session_scope() as session:
        workspace = session.execute(select(models.Membership)).scalar_one().workspace_id
        placeholder = models.InventoryItem(
            workspace_id=workspace,
            sku="IMPORT-PLACEHOLDER",
            title="External source",
            category="book",
            quantity=1,
            attributes={"connector_import_placeholder": True},
        )
        session.add(placeholder)
        session.flush()
        session.add(models.ChannelListing(
            workspace_id=workspace,
            channel="biblio",
            inventory_item_id=placeholder.id,
            external_id="BOOK-PHOTO-1",
            title="External source",
            price_cents=900,
            status="active",
            quantity=1,
        ))
        session.add(models.ChannelListing(
            workspace_id=workspace,
            channel="biblio",
            inventory_item_id=None,
            external_id="BOOK-UNLINKED-2",
            title="Unlinked source",
            price_cents=900,
            status="active",
            quantity=1,
        ))
        session.add(models.Sale(
            workspace_id=workspace,
            channel="biblio",
            direction="sell",
            external_order_id="ORDER-1",
            title="External source",
            status="completed",
            total_cents=900,
        ))

    anonymous = TestClient(entry.app)
    assert anonymous.get("/api/app/connectors/development").status_code == 401
    result = first.get("/api/app/connectors/development")
    assert result.status_code == 200, result.text
    body = result.json()
    biblio = next(row for row in body["channels"] if row["channel"] == Channel.BIBLIO)
    assert biblio["runtime"]["listing_count"] == 2
    assert biblio["runtime"]["master_references"] == 1
    assert biblio["runtime"]["unlinked_listings"] == 1
    assert biblio["runtime"]["import_placeholders"] == 1
    assert biblio["runtime"]["seller_sales"] == 1
    assert biblio["runtime"]["sales_unlinked_to_master"] == 1
    assert biblio["runtime"]["last_successful_sync_at"] is None

    second = _registered("market-audit-two@example.test")
    response = second.get("/api/app/connectors/development")
    assert response.status_code == 200, response.text
    other = next(row for row in response.json()["channels"] if row["channel"] == Channel.BIBLIO)
    assert other["runtime"]["listing_count"] == 0
    assert other["runtime"]["import_placeholders"] == 0
    assert other["runtime"]["seller_sales"] == 0


def test_import_creates_explicitly_provisional_stock_instead_of_silent_verified_copy():
    _registered("import-relation@example.test")
    with db.session_scope() as session:
        workspace = session.execute(select(models.Membership)).scalar_one().workspace_id
    result = record_workspace_channel_snapshot(
        workspace,
        Channel.ETSY,
        [{"source_id": "ETSY-REMOTE-123", "title": "Independent listing", "quantity": 1,
          "status": "active", "price_cents": 500, "currency": "EUR"}],
        synced_at=datetime.now(timezone.utc),
        full_snapshot=False,
    )
    assert result["items"] == 1
    with db.session_scope() as session:
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace,
                models.ChannelListing.channel == Channel.ETSY,
            )
        ).scalar_one()
        item = session.get(models.InventoryItem, listing.inventory_item_id)
        assert item is not None
        assert item.attributes["connector_import_placeholder"] is True
        assert item.attributes["connector_import_channel"] == Channel.ETSY
        assert item.attributes["connector_import_external_id"] == "ETSY-REMOTE-123"
