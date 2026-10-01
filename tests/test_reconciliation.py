from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import db, entry, models
from app.connectors.workspace_sync import record_workspace_channel_snapshot
from app.reconciliation import apply_reconciliation_merges, reconciliation_suggestions


def _workspace(slug: str = "reconcile") -> uuid.UUID:
    with db.session_scope() as session:
        workspace = models.Workspace(name="Reconcile", slug=slug, settings={})
        session.add(workspace)
        session.flush()
        return workspace.id


def _listing(
    session,
    workspace_id: uuid.UUID,
    item: models.InventoryItem,
    *,
    channel: str,
    external_id: str,
    external_sku: str | None = None,
    isbn: str | None = None,
):
    account = session.execute(
        select(models.ChannelAccount).where(
            models.ChannelAccount.workspace_id == workspace_id,
            models.ChannelAccount.channel == channel,
        )
    ).scalar_one_or_none()
    if account is None:
        account = models.ChannelAccount(
            workspace_id=workspace_id,
            channel=channel,
            display_name=channel,
            status="connected",
            config={},
        )
        session.add(account)
        session.flush()
    row = models.ChannelListing(
        workspace_id=workspace_id,
        inventory_item_id=item.id,
        channel_account_id=account.id,
        channel=channel,
        external_id=external_id,
        external_sku=external_sku,
        title=item.title,
        status="active",
        quantity=1,
        first_seen_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
        extra={"isbn": isbn} if isbn else {},
    )
    session.add(row)
    session.flush()
    return row


def test_exact_isbn_is_suggested_without_automatic_merge():
    workspace_id = _workspace()
    with db.session_scope() as session:
        vinted = models.InventoryItem(
            workspace_id=workspace_id,
            sku="VINTED-1",
            title="Stoner",
            category="book",
            quantity=1,
            status="active",
            attributes={"author": "John Williams", "isbn": "9780099561545"},
        )
        biblio = models.InventoryItem(
            workspace_id=workspace_id,
            sku="BOOK-42",
            title="Stoner",
            category="book",
            quantity=1,
            status="active",
            attributes={"author": "John Williams", "isbn": "978-0-09-956154-5"},
        )
        session.add_all([vinted, biblio])
        session.flush()
        _listing(session, workspace_id, vinted, channel="vinted", external_id="1")
        _listing(
            session,
            workspace_id,
            biblio,
            channel="biblio",
            external_id="BOOK-42",
            external_sku="BOOK-42",
        )

        suggestions = reconciliation_suggestions(session, workspace_id)
        assert len(suggestions) == 1
        assert suggestions[0]["confidence"] == "high"
        assert suggestions[0]["reasons"] == ["Exact ISBN: 9780099561545"]

        count = session.execute(
            select(func.count(models.InventoryItem.id)).where(
                models.InventoryItem.workspace_id == workspace_id
            )
        ).scalar_one()
        assert count == 2


def test_exact_title_and_author_is_only_a_medium_suggestion():
    workspace_id = _workspace()
    with db.session_scope() as session:
        first = models.InventoryItem(
            workspace_id=workspace_id,
            sku="VINTED-10",
            title="  The   Left Hand of Darkness ",
            category="book",
            quantity=1,
            status="active",
            attributes={"author": "Ursula K. Le Guin"},
        )
        second = models.InventoryItem(
            workspace_id=workspace_id,
            sku="EB-10",
            title="The Left Hand of Darkness",
            category="book",
            quantity=1,
            status="active",
            attributes={"author": "ursula k le guin"},
        )
        session.add_all([first, second])
        session.flush()
        _listing(session, workspace_id, first, channel="vinted", external_id="10")
        _listing(session, workspace_id, second, channel="ebay", external_id="10")

        suggestions = reconciliation_suggestions(session, workspace_id)
        assert len(suggestions) == 1
        assert suggestions[0]["confidence"] == "medium"
        assert suggestions[0]["reasons"] == ["Exact title", "Exact author"]


def test_same_marketplace_rows_are_not_suggested_as_same_physical_stock():
    workspace_id = _workspace()
    with db.session_scope() as session:
        first = models.InventoryItem(
            workspace_id=workspace_id,
            sku="VINTED-1",
            title="Same",
            category="book",
            quantity=1,
            status="active",
            attributes={"isbn": "9780099561545"},
        )
        second = models.InventoryItem(
            workspace_id=workspace_id,
            sku="VINTED-2",
            title="Same",
            category="book",
            quantity=1,
            status="active",
            attributes={"isbn": "9780099561545"},
        )
        session.add_all([first, second])
        session.flush()
        _listing(session, workspace_id, first, channel="vinted", external_id="1")
        _listing(session, workspace_id, second, channel="vinted", external_id="2")

        assert reconciliation_suggestions(session, workspace_id) == []


def test_explicit_merge_moves_channels_and_sales_without_double_counting_stock():
    workspace_id = _workspace()
    with db.session_scope() as session:
        target = models.InventoryItem(
            workspace_id=workspace_id,
            sku="MASTER-1",
            title="Stoner",
            category="book",
            quantity=1,
            status="active",
            location="A1",
            attributes={"isbn": "9780099561545"},
        )
        source = models.InventoryItem(
            workspace_id=workspace_id,
            sku="VINTED-100",
            title="Stoner",
            category="general",
            quantity=1,
            status="active",
            condition="Very good",
            attributes={"author": "John Williams", "isbn": "9780099561545"},
        )
        session.add_all([target, source])
        session.flush()
        _listing(
            session,
            workspace_id,
            target,
            channel="biblio",
            external_id="MASTER-1",
            external_sku="MASTER-1",
        )
        source_listing = _listing(
            session,
            workspace_id,
            source,
            channel="vinted",
            external_id="100",
        )
        sale = models.Sale(
            workspace_id=workspace_id,
            inventory_item_id=source.id,
            channel="vinted",
            external_order_id="ORDER-1",
            direction="sell",
            title="Stoner",
            is_closed=True,
        )
        session.add(sale)
        session.flush()

        result = apply_reconciliation_merges(
            session,
            workspace_id,
            [(target.id, source.id)],
        )
        assert result[0]["moved_listings"] == 1
        assert result[0]["moved_sales"] == 1

        session.flush()
        assert session.get(models.InventoryItem, source.id) is None
        merged = session.get(models.InventoryItem, target.id)
        assert merged.sku == "MASTER-1"
        assert merged.quantity == 1
        assert merged.condition == "Very good"
        assert merged.attributes["author"] == "John Williams"
        assert source_listing.inventory_item_id == target.id
        assert sale.inventory_item_id == target.id


def test_bulk_merge_rejects_overlapping_source_and_target_roles():
    workspace_id = _workspace()
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with db.session_scope() as session:
        with pytest.raises(ValueError, match="both a target and a source"):
            apply_reconciliation_merges(session, workspace_id, [(a, b), (b, c)])


def test_connector_sync_preserves_manual_reconciliation():
    workspace_id = _workspace()
    now = datetime.now(timezone.utc)
    with db.session_scope() as session:
        master = models.InventoryItem(
            workspace_id=workspace_id,
            sku="MASTER-COAT",
            title="Vintage coat",
            category="clothing",
            quantity=1,
            status="active",
            attributes={"brand": "Example", "size": "M"},
        )
        session.add(master)
        session.flush()
        master_id = master.id

    record_workspace_channel_snapshot(
        workspace_id,
        "ebay",
        [
            {
                "source_id": "EB-1",
                "sku": "EBAY-SKU-1",
                "title": "Vintage coat",
                "status": "active",
                "quantity": 1,
            }
        ],
        synced_at=now,
        full_snapshot=True,
    )

    with db.session_scope() as session:
        generated = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == workspace_id,
                models.InventoryItem.sku == "EBAY-SKU-1",
            )
        ).scalar_one()
        apply_reconciliation_merges(
            session,
            workspace_id,
            [(master_id, generated.id)],
        )

    record_workspace_channel_snapshot(
        workspace_id,
        "ebay",
        [
            {
                "source_id": "EB-1",
                "sku": "EBAY-SKU-1",
                "title": "Vintage coat updated",
                "status": "active",
                "quantity": 1,
            }
        ],
        synced_at=now,
        full_snapshot=True,
    )

    with db.session_scope() as session:
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.channel == "ebay",
                models.ChannelListing.external_id == "EB-1",
            )
        ).scalar_one()
        assert listing.inventory_item_id == master_id
        assert session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == workspace_id,
                models.InventoryItem.sku == "EBAY-SKU-1",
            )
        ).scalar_one_or_none() is None


def test_reconciliation_api_requires_explicit_apply():
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "reconcile@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Reconcile API",
        },
    )
    assert registered.status_code == 200
    csrf = registered.json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf}

    first = client.post(
        "/api/app/inventory",
        headers=headers,
        json={
            "sku": "MASTER-ISBN",
            "title": "Book",
            "category": "book",
            "quantity": 1,
            "attributes": {"isbn": "9780099561545"},
        },
    ).json()["item"]
    second = client.post(
        "/api/app/inventory",
        headers=headers,
        json={
            "sku": "OTHER-ISBN",
            "title": "Book",
            "category": "book",
            "quantity": 1,
            "attributes": {"isbn": "9780099561545"},
        },
    ).json()["item"]

    with db.session_scope() as session:
        first_item = session.get(models.InventoryItem, uuid.UUID(first["id"]))
        second_item = session.get(models.InventoryItem, uuid.UUID(second["id"]))
        _listing(session, first_item.workspace_id, first_item, channel="vinted", external_id="1")
        _listing(session, second_item.workspace_id, second_item, channel="ebay", external_id="2")

    suggestions = client.get("/api/app/reconciliation")
    assert suggestions.status_code == 200
    assert suggestions.json()["count"] == 1

    inventory_before = client.get("/api/app/inventory").json()
    assert inventory_before["count"] == 2

    response = client.post(
        "/api/app/reconciliation/apply",
        headers=headers,
        json={
            "merges": [
                {
                    "target_item_id": first["id"],
                    "source_item_id": second["id"],
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1

    inventory_after = client.get("/api/app/inventory").json()
    assert inventory_after["count"] == 1
    assert len(inventory_after["items"][0]["listings"]) == 2
