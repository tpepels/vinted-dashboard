from datetime import datetime, timezone

from sqlalchemy import select

from app import db, models
from app.connectors.workspace_sync import record_workspace_channel_snapshot
from app.constants import ItemStatus, ListingStatus, SyncRunStatus
from app.workspace_bootstrap import get_or_create_workspace


def _workspace_id():
    with db.session_scope() as session:
        workspace = get_or_create_workspace(session, "Workspace", "workspace-sync")
        session.flush()
        return workspace.id


def test_workspace_snapshot_persists_inventory_listing_and_audit_run():
    workspace_id = _workspace_id()
    synced_at = datetime.fromtimestamp(1_900_000_000, tz=timezone.utc)

    result = record_workspace_channel_snapshot(
        workspace_id,
        "vinted",
        [
            {
                "source_id": "101",
                "title": "Stoner",
                "status": "active",
                "quantity": 1,
                "price_cents": 800,
                "currency": "EUR",
                "url": "https://vinted.example/101",
            }
        ],
        synced_at=synced_at,
        full_snapshot=True,
        note="bridge snapshot",
    )

    assert result == {"items": 1, "active": 1}

    with db.session_scope() as session:
        item = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == workspace_id,
                models.InventoryItem.sku == "VINTED-101",
            )
        ).scalar_one()
        assert item.title == "Stoner"
        assert item.status == ItemStatus.ACTIVE
        assert item.quantity == 1

        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.channel == "vinted",
                models.ChannelListing.external_id == "101",
            )
        ).scalar_one()
        assert listing.inventory_item_id == item.id
        assert listing.price_cents == 800
        assert listing.url == "https://vinted.example/101"

        run = session.execute(
            select(models.ConnectorSyncRun).where(
                models.ConnectorSyncRun.workspace_id == workspace_id,
                models.ConnectorSyncRun.channel == "vinted",
                models.ConnectorSyncRun.run_type == "snapshot",
            )
        ).scalar_one()
        assert run.status == SyncRunStatus.SUCCESS
        assert run.item_count == 1
        assert run.active_count == 1
        assert run.detail == {"note": "bridge snapshot"}


def test_full_workspace_snapshot_deactivates_missing_listing():
    workspace_id = _workspace_id()
    first = datetime.fromtimestamp(1_900_000_000, tz=timezone.utc)
    second = datetime.fromtimestamp(1_900_000_100, tz=timezone.utc)

    record_workspace_channel_snapshot(
        workspace_id,
        "vinted",
        [
            {"source_id": "101", "title": "Stoner", "status": "active", "quantity": 1},
            {"source_id": "102", "title": "Dune", "status": "active", "quantity": 1},
        ],
        synced_at=first,
        full_snapshot=True,
    )
    record_workspace_channel_snapshot(
        workspace_id,
        "vinted",
        [{"source_id": "101", "title": "Stoner", "status": "active", "quantity": 1}],
        synced_at=second,
        full_snapshot=True,
    )

    with db.session_scope() as session:
        missing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.external_id == "102",
            )
        ).scalar_one()
        assert missing.status == ListingStatus.INACTIVE
        assert missing.quantity == 0

        item = session.get(models.InventoryItem, missing.inventory_item_id)
        assert item.status == ItemStatus.ARCHIVED
        assert item.quantity == 0


def test_workspace_snapshot_preserves_explicit_reconciliation_link():
    workspace_id = _workspace_id()
    first = datetime.fromtimestamp(1_900_000_000, tz=timezone.utc)
    second = datetime.fromtimestamp(1_900_000_100, tz=timezone.utc)

    record_workspace_channel_snapshot(
        workspace_id,
        "vinted",
        [{"source_id": "101", "title": "Original", "status": "active", "quantity": 1}],
        synced_at=first,
        full_snapshot=True,
    )

    with db.session_scope() as session:
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.external_id == "101",
            )
        ).scalar_one()
        reconciled = models.InventoryItem(
            workspace_id=workspace_id,
            sku="MANUAL-1",
            title="Reconciled physical item",
            category="general",
            quantity=1,
            status="active",
            attributes={},
        )
        session.add(reconciled)
        session.flush()
        listing.inventory_item_id = reconciled.id
        reconciled_id = reconciled.id

    record_workspace_channel_snapshot(
        workspace_id,
        "vinted",
        [{"source_id": "101", "title": "Updated", "status": "active", "quantity": 1}],
        synced_at=second,
        full_snapshot=True,
    )

    with db.session_scope() as session:
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.external_id == "101",
            )
        ).scalar_one()
        assert listing.inventory_item_id == reconciled_id
        assert listing.title == "Updated"
