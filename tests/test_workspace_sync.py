"""Verifies the Phase 2 live dual-write layer: real calls through
``app.channels``'s public entry points (``upsert_channel_snapshot`` via
``record_vinted_items``/``import_biblio_inventory``, and
``_record_biblio_ftp_run``) must keep the workspace/inventory ORM schema
(``app.models``) continuously up to date, matching the field/matching
conventions ``app.legacy_migration`` uses for the one-time batch backfill.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app import channels, db, models
from app.connectors import workspace_sync
from app.constants import ItemCategory, ItemStatus, ListingStatus, SyncRunStatus
from app.workspace_bootstrap import (
    BOOTSTRAP_WORKSPACE_SLUG,
    clean_isbn,
    get_or_create_channel_account,
    get_or_create_owner,
    get_or_create_workspace,
    normalize_sku,
)


@pytest.fixture(autouse=True)
def _isolate_legacy_sqlite(monkeypatch, tmp_path):
    """The autouse ``_isolated_orm_database`` fixture in ``conftest.py``
    already isolates the new ORM schema; this isolates the legacy sqlite3
    database too, so ``app.channels`` tests never touch a real path."""
    monkeypatch.setattr(channels, "DB_PATH", tmp_path / "channels.sqlite3")


def _bootstrap_workspace(session) -> models.Workspace:
    return session.execute(
        select(models.Workspace).where(models.Workspace.slug == BOOTSTRAP_WORKSPACE_SLUG)
    ).scalar_one()


# ---------------------------------------------------------------------------
# app.workspace_bootstrap helpers
# ---------------------------------------------------------------------------


def test_clean_isbn_accepts_only_isbn10_or_isbn13():
    assert clean_isbn("978-0-306-40615-7") == "9780306406157"
    assert clean_isbn("0-306-40615-2") == "0306406152"
    assert clean_isbn("not an isbn") is None
    assert clean_isbn(None) is None


def test_normalize_sku_strips_and_blanks_to_none():
    assert normalize_sku("  ABC-1  ") == "ABC-1"
    assert normalize_sku("") is None
    assert normalize_sku(None) is None


def test_get_or_create_workspace_is_idempotent():
    with db.session_scope() as session:
        first = get_or_create_workspace(session, "Personal Workspace", "personal")
        second = get_or_create_workspace(session, "Personal Workspace", "personal")
        assert first.id == second.id


def test_get_or_create_owner_is_idempotent():
    with db.session_scope() as session:
        workspace = get_or_create_workspace(session, "Personal Workspace", "personal")
        first = get_or_create_owner(session, workspace, "owner@example.com")
        second = get_or_create_owner(session, workspace, "owner@example.com")
        assert first.id == second.id
        assert len(workspace.memberships) == 1


def test_get_or_create_channel_account_reports_created_flag():
    with db.session_scope() as session:
        workspace = get_or_create_workspace(session, "Personal Workspace", "personal")
        account, created = get_or_create_channel_account(session, workspace, "vinted", {})
        assert created is True
        again, created_again = get_or_create_channel_account(session, workspace, "vinted", {})
        assert created_again is False
        assert again.id == account.id


# ---------------------------------------------------------------------------
# Inbound dual-write: channels.upsert_channel_snapshot
# ---------------------------------------------------------------------------


def test_vinted_snapshot_dual_writes_inventory_and_listing():
    channels.record_vinted_items(
        [
            {
                "id": "101",
                "title": "Stoner",
                "status": "active",
                "price_cents": 800,
                "currency": "EUR",
                "vinted_url": "https://vinted.example/101",
            }
        ],
        synced_at=1_900_000_000.0,
    )

    with db.session_scope() as session:
        workspace = _bootstrap_workspace(session)
        item = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == workspace.id, models.InventoryItem.sku == "VINTED-101"
            )
        ).scalar_one()
        assert item.title == "Stoner"
        assert item.status == ItemStatus.ACTIVE
        assert item.quantity == 1
        assert item.category == ItemCategory.GENERAL

        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace.id,
                models.ChannelListing.channel == "vinted",
                models.ChannelListing.external_id == "101",
            )
        ).scalar_one()
        assert listing.inventory_item_id == item.id
        assert listing.price_cents == 800
        assert listing.status == "active"
        assert listing.url == "https://vinted.example/101"

        run = session.execute(
            select(models.ConnectorSyncRun).where(
                models.ConnectorSyncRun.workspace_id == workspace.id,
                models.ConnectorSyncRun.channel == "vinted",
                models.ConnectorSyncRun.run_type == "snapshot",
            )
        ).scalar_one()
        assert run.status == SyncRunStatus.SUCCESS
        assert run.item_count == 1
        assert run.active_count == 1
        assert run.started_at == datetime.fromtimestamp(1_900_000_000.0, tz=timezone.utc)

        account = session.execute(
            select(models.ChannelAccount).where(
                models.ChannelAccount.workspace_id == workspace.id, models.ChannelAccount.channel == "vinted"
            )
        ).scalar_one()
        assert account.last_synced_at == run.started_at


def test_repeated_snapshot_is_idempotent_not_duplicated():
    item_payload = [
        {
            "id": "101",
            "title": "Stoner",
            "status": "active",
            "price_cents": 800,
            "currency": "EUR",
            "vinted_url": "https://vinted.example/101",
        }
    ]
    channels.record_vinted_items(item_payload, synced_at=1_900_000_000.0)
    channels.record_vinted_items(item_payload, synced_at=1_900_000_100.0)

    with db.session_scope() as session:
        workspace = _bootstrap_workspace(session)
        items = session.execute(
            select(models.InventoryItem).where(models.InventoryItem.workspace_id == workspace.id)
        ).scalars().all()
        assert len(items) == 1

        listings = session.execute(
            select(models.ChannelListing).where(models.ChannelListing.workspace_id == workspace.id)
        ).scalars().all()
        assert len(listings) == 1
        assert listings[0].last_seen_at == datetime.fromtimestamp(1_900_000_100.0, tz=timezone.utc)

        runs = session.execute(
            select(models.ConnectorSyncRun).where(
                models.ConnectorSyncRun.workspace_id == workspace.id,
                models.ConnectorSyncRun.channel == "vinted",
            )
        ).scalars().all()
        assert len(runs) == 2  # distinct synced_at => distinct audit rows


def test_repeated_snapshot_same_timestamp_does_not_duplicate_sync_run():
    item_payload = [{"id": "101", "title": "Stoner", "status": "active"}]
    channels.record_vinted_items(item_payload, synced_at=1_900_000_000.0)
    channels.record_vinted_items(item_payload, synced_at=1_900_000_000.0)

    with db.session_scope() as session:
        workspace = _bootstrap_workspace(session)
        runs = session.execute(
            select(models.ConnectorSyncRun).where(
                models.ConnectorSyncRun.workspace_id == workspace.id,
                models.ConnectorSyncRun.channel == "vinted",
            )
        ).scalars().all()
        assert len(runs) == 1


def test_full_snapshot_deactivates_missing_listing():
    channels.record_vinted_items(
        [
            {"id": "101", "title": "Stoner", "status": "active", "price_cents": 800},
            {"id": "102", "title": "Dune", "status": "active", "price_cents": 1200},
        ],
        synced_at=1_900_000_000.0,
    )
    # Second full snapshot omits item 102: it must be marked inactive, not
    # left stale/active forever, mirroring the legacy channel_items behavior.
    channels.record_vinted_items(
        [{"id": "101", "title": "Stoner", "status": "active", "price_cents": 800}],
        synced_at=1_900_000_100.0,
    )

    with db.session_scope() as session:
        workspace = _bootstrap_workspace(session)
        missing_listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace.id,
                models.ChannelListing.channel == "vinted",
                models.ChannelListing.external_id == "102",
            )
        ).scalar_one()
        assert missing_listing.status == ListingStatus.INACTIVE
        assert missing_listing.quantity == 0

        missing_item = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == workspace.id, models.InventoryItem.sku == "VINTED-102"
            )
        ).scalar_one()
        assert missing_item.status == ItemStatus.ARCHIVED
        assert missing_item.quantity == 0

        still_active = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == workspace.id, models.InventoryItem.sku == "VINTED-101"
            )
        ).scalar_one()
        assert still_active.status == ItemStatus.ACTIVE


def test_ebay_item_without_sku_gets_synthesized_sku():
    channels.upsert_channel_snapshot(
        "ebay",
        [{"source_id": "EB-1", "sku": None, "title": "Camera", "status": "active", "quantity": 2}],
        synced_at=1_900_000_000.0,
    )

    with db.session_scope() as session:
        workspace = _bootstrap_workspace(session)
        item = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == workspace.id, models.InventoryItem.sku == "EBAY-EB-1"
            )
        ).scalar_one()
        assert item.title == "Camera"
        assert item.quantity == 2


def test_cross_channel_sku_merges_into_one_inventory_item():
    channels.upsert_channel_snapshot(
        "vinted",
        [{"source_id": "V1", "sku": "SHARED-1", "title": "Shared Thing", "status": "active", "quantity": 1}],
        synced_at=1_900_000_000.0,
    )
    channels.upsert_channel_snapshot(
        "biblio",
        [{"source_id": "SHARED-1", "sku": "SHARED-1", "title": "Shared Thing", "status": "active", "quantity": 1}],
        synced_at=1_900_000_000.0,
    )

    with db.session_scope() as session:
        workspace = _bootstrap_workspace(session)
        items = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == workspace.id, models.InventoryItem.sku == "SHARED-1"
            )
        ).scalars().all()
        assert len(items) == 1
        listings = session.execute(
            select(models.ChannelListing).where(models.ChannelListing.inventory_item_id == items[0].id)
        ).scalars().all()
        assert {listing.channel for listing in listings} == {"vinted", "biblio"}
        # BIBLIO involvement tags the merged item as a book.
        assert items[0].category == ItemCategory.BOOK


def test_biblio_import_dual_writes_with_isbn_attribute():
    text = (
        "SKU\tTitle\tAuthor\tISBN\tPrice\tStatus\tQuantity\n"
        "BK-1\tA Lost Lady\tWilla Cather\t9780306406157\t9.50\tFor sale\t2\n"
    )
    channels.import_biblio_inventory(text, filename="inventory.tsv")

    with db.session_scope() as session:
        workspace = _bootstrap_workspace(session)
        item = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == workspace.id, models.InventoryItem.sku == "BK-1"
            )
        ).scalar_one()
        assert item.category == ItemCategory.BOOK
        assert item.attributes.get("isbn") == "9780306406157"
        assert item.attributes.get("author") == "Willa Cather"

        run = session.execute(
            select(models.ConnectorSyncRun).where(
                models.ConnectorSyncRun.workspace_id == workspace.id,
                models.ConnectorSyncRun.channel == "biblio",
                models.ConnectorSyncRun.run_type == "snapshot",
            )
        ).scalar_one()
        assert run.detail.get("note") == "Imported inventory.tsv"


# ---------------------------------------------------------------------------
# Outbound dual-write: channels._record_biblio_ftp_run
# ---------------------------------------------------------------------------


def test_biblio_ftp_run_dual_writes_connector_sync_run():
    channels._record_biblio_ftp_run(
        "test",
        "success",
        detail="Connected successfully; directory /",
    )

    with db.session_scope() as session:
        workspace = _bootstrap_workspace(session)
        run = session.execute(
            select(models.ConnectorSyncRun).where(
                models.ConnectorSyncRun.workspace_id == workspace.id,
                models.ConnectorSyncRun.channel == "biblio",
                models.ConnectorSyncRun.run_type == "ftp_test",
            )
        ).scalar_one()
        assert run.status == SyncRunStatus.SUCCESS
        assert run.error is None


def test_biblio_ftp_run_error_status_sets_error_field():
    channels._record_biblio_ftp_run(
        "sync",
        "error",
        inventory_filename="inv.txt",
        active_count=5,
        detail="Connection refused",
    )

    with db.session_scope() as session:
        workspace = _bootstrap_workspace(session)
        run = session.execute(
            select(models.ConnectorSyncRun).where(
                models.ConnectorSyncRun.workspace_id == workspace.id,
                models.ConnectorSyncRun.channel == "biblio",
                models.ConnectorSyncRun.run_type == "ftp_sync",
            )
        ).scalar_one()
        assert run.status == SyncRunStatus.ERROR
        assert run.error == "Connection refused"
        assert run.detail.get("inventory_filename") == "inv.txt"
        assert run.active_count == 5


# ---------------------------------------------------------------------------
# Best-effort contract: dual-write failures must never surface to callers.
# ---------------------------------------------------------------------------


def test_dual_write_failure_does_not_break_legacy_sync(monkeypatch, caplog):
    def _boom(*args, **kwargs):
        raise RuntimeError("simulated ORM outage")

    monkeypatch.setattr(workspace_sync.db, "session_scope", _boom)

    result = channels.record_vinted_items(
        [{"id": "101", "title": "Stoner", "status": "active"}], synced_at=1_900_000_000.0
    )

    assert result["items"] == 1  # legacy write still succeeded
