from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

from sqlalchemy import func, select

from app import db, jobs, models
from app.connectors import hosted
from app.connectors.workspace_sync import recompute_inventory_item
from app.cross_channel import (
    acknowledge_manual_action,
    auto_link_unlinked_sales,
    execute_action,
    plan_sale_reconciliation,
    reconcile_sale_state,
    unlinked_sale_reconciliation,
)
from app.product_models import BackgroundJob, CrossChannelAction
from app.workspace_ingest import record_workspace_snapshot


NOW = datetime.now(timezone.utc)


def _workspace() -> uuid.UUID:
    with db.session_scope() as session:
        row = models.Workspace(name="Cross channel", slug="cross-channel", settings={})
        session.add(row)
        session.flush()
        return row.id


def _account(session, workspace_id, channel):
    row = session.execute(
        select(models.ChannelAccount).where(
            models.ChannelAccount.workspace_id == workspace_id,
            models.ChannelAccount.channel == channel,
        )
    ).scalar_one_or_none()
    if row is None:
        row = models.ChannelAccount(
            workspace_id=workspace_id,
            channel=channel,
            display_name=channel,
            status="connected",
            config={},
        )
        session.add(row)
        session.flush()
    return row


def _listing(session, workspace_id, item, channel, external_id, status="active"):
    account = _account(session, workspace_id, channel)
    row = models.ChannelListing(
        workspace_id=workspace_id,
        inventory_item_id=item.id,
        channel_account_id=account.id,
        channel=channel,
        external_id=external_id,
        title=item.title,
        status=status,
        quantity=1 if status == "active" else 0,
        first_seen_at=NOW,
        last_seen_at=NOW,
        extra={},
    )
    session.add(row)
    session.flush()
    return row


def _sale(session, workspace_id, item, channel="vinted", status="sold"):
    account = _account(session, workspace_id, channel)
    row = models.Sale(
        workspace_id=workspace_id,
        channel_account_id=account.id,
        inventory_item_id=item.id if item else None,
        channel=channel,
        external_order_id=f"ORDER-{uuid.uuid4()}",
        direction="sell",
        title=item.title if item else "Unknown sale",
        status=status,
        lifecycle_status=status,
        is_closed=False,
        occurred_at=NOW,
        first_seen_at=NOW,
        last_seen_at=NOW,
        extra={},
    )
    session.add(row)
    session.flush()
    return row


def test_sale_queues_other_remote_channels_once_and_marks_physical_stock_sold():
    workspace_id = _workspace()
    with db.session_scope() as session:
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="MASTER-1",
            title="One physical copy",
            category="book",
            quantity=1,
            status="active",
            attributes={},
        )
        session.add(item)
        session.flush()
        _listing(session, workspace_id, item, "vinted", "V-1", status="sold")
        ebay = _listing(session, workspace_id, item, "ebay", "E-1")
        biblio = _listing(session, workspace_id, item, "biblio", "B-1")
        sale = _sale(session, workspace_id, item)

        created = plan_sale_reconciliation(session, sale)
        assert {row.channel for row in created} == {"ebay", "biblio"}
        assert all(row.status == "queued" for row in created)
        assert item.status == "sold"
        assert item.quantity == 0

        jobs_count = session.execute(
            select(func.count(BackgroundJob.id)).where(
                BackgroundJob.workspace_id == workspace_id,
                BackgroundJob.job_type == "cross_channel_close",
            )
        ).scalar_one()
        assert jobs_count == 2

        assert plan_sale_reconciliation(session, sale) == []
        assert session.execute(
            select(func.count(CrossChannelAction.id)).where(
                CrossChannelAction.workspace_id == workspace_id
            )
        ).scalar_one() == 2
        assert {ebay.status, biblio.status} == {"active"}


def test_vinted_cross_channel_close_is_manual_and_never_queued():
    workspace_id = _workspace()
    with db.session_scope() as session:
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="MASTER-2",
            title="Jacket",
            category="clothing",
            quantity=1,
            status="active",
            attributes={},
        )
        session.add(item)
        session.flush()
        _listing(session, workspace_id, item, "ebay", "E-2", status="sold")
        vinted = _listing(session, workspace_id, item, "vinted", "V-2")
        sale = _sale(session, workspace_id, item, channel="ebay")

        created = plan_sale_reconciliation(session, sale)
        assert len(created) == 1
        action = created[0]
        assert action.channel == "vinted"
        assert action.mode == "manual"
        assert action.status == "attention"
        assert session.execute(
            select(func.count(BackgroundJob.id)).where(
                BackgroundJob.workspace_id == workspace_id
            )
        ).scalar_one() == 0

        acknowledge_manual_action(session, workspace_id, action.id)
        assert action.status == "acknowledged"
        assert vinted.status == "active"


def test_cancelled_sale_cancels_pending_closes_and_restores_listing_derived_stock():
    workspace_id = _workspace()
    with db.session_scope() as session:
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="MASTER-3",
            title="Book",
            category="book",
            quantity=1,
            status="active",
            attributes={},
        )
        session.add(item)
        session.flush()
        _listing(session, workspace_id, item, "vinted", "V-3", status="sold")
        _listing(session, workspace_id, item, "ebay", "E-3")
        sale = _sale(session, workspace_id, item)
        created = plan_sale_reconciliation(session, sale)
        assert created[0].status == "queued"
        assert item.status == "sold"

        sale.status = "cancelled"
        sale.lifecycle_status = "cancelled"
        reconcile_sale_state(session, sale)
        assert created[0].status == "cancelled"
        assert item.status == "active"
        assert item.quantity == 1


def test_recompute_does_not_reactivate_stock_with_consuming_sale():
    workspace_id = _workspace()
    with db.session_scope() as session:
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="MASTER-4",
            title="Camera",
            category="general",
            quantity=1,
            status="active",
            attributes={},
        )
        session.add(item)
        session.flush()
        _listing(session, workspace_id, item, "ebay", "E-4")
        _sale(session, workspace_id, item)
        recompute_inventory_item(session, item)
        assert item.status == "sold"
        assert item.quantity == 0


def test_job_retry_reuses_same_queue_row():
    workspace_id = _workspace()
    job_id = jobs.enqueue("cross_channel_close", {"action_id": str(uuid.uuid4())}, workspace_id)
    claimed = jobs.claim_one()
    assert claimed and claimed["id"] == str(job_id)

    result = jobs.retry(str(job_id), "temporary", max_attempts=3, base_delay_seconds=0)
    assert result["will_retry"] is True
    claimed_again = jobs.claim_one()
    assert claimed_again and claimed_again["id"] == str(job_id)

    jobs.retry(str(job_id), "again", max_attempts=2, base_delay_seconds=0)
    with db.session_scope() as session:
        row = session.get(BackgroundJob, job_id)
        assert row.status == "error"
        assert row.attempts == 2
        assert session.execute(
            select(func.count(BackgroundJob.id)).where(BackgroundJob.id == job_id)
        ).scalar_one() == 1


def test_ebay_close_checks_active_inventory_before_enditem(monkeypatch):
    workspace_id = _workspace()
    monkeypatch.setattr(
        hosted,
        "_workspace_or_env_ebay_values",
        lambda _workspace_id: {
            "oauth_token": "token",
            "site_id": "0",
            "compatibility_level": "1477",
        },
    )
    monkeypatch.setattr(
        hosted,
        "_fetch_ebay_active",
        lambda _values: [{"source_id": "123"}],
    )
    monkeypatch.setattr(hosted, "_ebay_access_token", lambda _values: "token")

    calls = []
    xml = b"""<?xml version="1.0" encoding="utf-8"?>
<EndItemResponse xmlns="urn:ebay:apis:eBLBaseComponents"><Ack>Success</Ack></EndItemResponse>"""

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, content=xml)

    monkeypatch.setattr(hosted.requests, "post", fake_post)
    result = hosted.close_ebay_workspace_listing(workspace_id, "123")
    assert result["remote"] == "ended"
    assert len(calls) == 1
    assert b"<ItemID>123</ItemID>" in calls[0][1]["data"]
    assert b"<EndingReason>NotAvailable</EndingReason>" in calls[0][1]["data"]

    monkeypatch.setattr(hosted, "_fetch_ebay_active", lambda _values: [])
    calls.clear()
    result = hosted.close_ebay_workspace_listing(workspace_id, "123")
    assert result["remote"] == "already_ended"
    assert calls == []


def test_biblio_close_uploads_only_one_delete_file(monkeypatch):
    workspace_id = _workspace()
    with db.session_scope() as session:
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="BOOK-1",
            title="Stoner",
            category="book",
            quantity=0,
            status="sold",
            attributes={"author": "John Williams", "isbn": "9780099561545"},
        )
        session.add(item)
        session.flush()
        listing = _listing(session, workspace_id, item, "biblio", "REMOTE-BOOK-1")
        listing.external_sku = "LOCAL-BOOK-1"
        listing.price_cents = 1200
        listing.currency = "EUR"
        listing.extra = {"author": "John Williams", "description": "Used book"}
        listing_id = listing.id

    monkeypatch.setattr(
        hosted,
        "_workspace_or_env_biblio_values",
        lambda _workspace_id: {
            "host": "ftp.test",
            "username": "seller",
            "password": "secret",
            "directory": "",
            "filename_prefix": "test",
            "timeout_seconds": "20",
        },
    )

    class FakeFTP:
        uploads = {}

        def connect(self, host, timeout=20):
            assert host == "ftp.test"

        def auth(self):
            return None

        def login(self, username, password):
            assert (username, password) == ("seller", "secret")

        def prot_p(self):
            return None

        def set_pasv(self, enabled):
            assert enabled is True

        def cwd(self, _directory):
            raise AssertionError("no directory expected")

        def storbinary(self, command, stream):
            self.uploads[command] = stream.read()

        def quit(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(hosted.ftplib, "FTP_TLS", FakeFTP)
    result = hosted.close_biblio_workspace_listing(workspace_id, listing_id)
    assert result["remote"] == "delete_uploaded"
    assert len(FakeFTP.uploads) == 1
    command, payload = next(iter(FakeFTP.uploads.items()))
    assert command.startswith("STOR test-")
    assert command.endswith("-deletes.txt")
    text = payload.decode("utf-8")
    assert "REMOTE-BOOK-1" in text
    assert "LOCAL-BOOK-1" not in text
    assert "	sold	" in text
    assert result["external_id"] == "REMOTE-BOOK-1"
    assert result["inventory_signature"]


def test_biblio_close_refuses_delete_while_master_stock_remains(monkeypatch):
    workspace_id = _workspace()
    with db.session_scope() as session:
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="BOOK-SAFE",
            title="Still available",
            category="book",
            quantity=1,
            status="active",
            attributes={"author": "Author"},
        )
        session.add(item)
        session.flush()
        listing = _listing(session, workspace_id, item, "biblio", "REMOTE-SAFE")
        listing.price_cents = 1000
        listing.currency = "EUR"
        listing.extra = {"author": "Author", "description": "Description"}
        listing_id = listing.id

    monkeypatch.setattr(
        hosted,
        "_workspace_or_env_biblio_values",
        lambda _workspace_id: {
            "host": "ftp.biblio.com",
            "username": "seller",
            "password": "secret",
            "directory": "",
            "filename_prefix": "test",
            "timeout_seconds": "20",
        },
    )
    monkeypatch.setattr(
        hosted.ftplib,
        "FTP",
        lambda: (_ for _ in ()).throw(AssertionError("FTP must not open")),
    )

    try:
        hosted.close_biblio_workspace_listing(workspace_id, listing_id)
        assert False, "expected sold-out safety guard"
    except RuntimeError as exc:
        assert "not sold out" in str(exc)


def test_vinted_snapshot_exact_item_id_links_sale_and_queues_other_channel_close():
    workspace_id = _workspace()
    with db.session_scope() as session:
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="MASTER-5",
            title="Specific book",
            category="book",
            quantity=1,
            status="active",
            attributes={},
        )
        session.add(item)
        session.flush()
        _listing(session, workspace_id, item, "vinted", "V-5")
        _listing(session, workspace_id, item, "ebay", "E-5")
        item_id = item.id

    record_workspace_snapshot(
        workspace_id,
        {
            "collected_at": NOW.timestamp(),
            "current_user": {},
            "listings": [
                {
                    "id": "V-5",
                    "title": "Specific book",
                    "status": "sold",
                    "price_cents": 1000,
                    "currency": "EUR",
                }
            ],
            "notifications": [],
            "orders": [
                {
                    "id": "ORDER-5",
                    "item_id": "V-5",
                    "direction": "sell",
                    "title": "Specific book",
                    "status": "paid",
                    "lifecycle_status": "paid",
                    "is_closed": False,
                    "updated_at": NOW.isoformat(),
                }
            ],
        },
    )

    with db.session_scope() as session:
        sale = session.execute(
            select(models.Sale).where(models.Sale.external_order_id == "ORDER-5")
        ).scalar_one()
        assert sale.inventory_item_id == item_id
        item = session.get(models.InventoryItem, item_id)
        assert item.status == "sold"
        assert item.quantity == 0
        action = session.execute(
            select(CrossChannelAction).where(CrossChannelAction.trigger_sale_id == sale.id)
        ).scalar_one()
        assert action.channel == "ebay"
        assert action.status == "queued"
        assert session.execute(
            select(func.count(BackgroundJob.id)).where(
                BackgroundJob.job_type == "cross_channel_close"
            )
        ).scalar_one() == 1



def test_auto_link_historical_sale_by_unique_exact_channel_title():
    workspace_id = _workspace()
    with db.session_scope() as session:
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="VINTED-UNIQUE",
            title="The Left Hand of Darkness",
            category="book",
            quantity=1,
            status="active",
            attributes={},
        )
        session.add(item)
        session.flush()
        _listing(session, workspace_id, item, "vinted", "V-UNIQUE")
        sale = _sale(session, workspace_id, None)
        sale.title = "  the left hand of darkness  "
        item_id = item.id
        sale_id = sale.id

        result = auto_link_unlinked_sales(session, workspace_id)
        assert result == {
            "linked": 1,
            "remaining": 0,
            "ambiguous": 0,
            "unmatched": 0,
            "actions_created": 0,
        }
        session.flush()

        linked = session.get(models.Sale, sale_id)
        assert linked.inventory_item_id == item_id
        assert linked.extra["auto_link_reason"] == "exact_unique_title"
        assert session.get(models.InventoryItem, item_id).status == "sold"


def test_auto_link_historical_sale_by_unique_nonactive_master_title_without_listing():
    workspace_id = _workspace()
    with db.session_scope() as session:
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="MASTER-ONLY",
            title="Master only title",
            category="book",
            quantity=0,
            status="sold",
            attributes={},
        )
        session.add(item)
        session.flush()
        sale = _sale(session, workspace_id, None)
        sale.title = "master only title"
        item_id = item.id
        sale_id = sale.id

        result = auto_link_unlinked_sales(session, workspace_id)
        assert result["linked"] == 1
        assert result["remaining"] == 0
        linked = session.get(models.Sale, sale_id)
        assert linked.inventory_item_id == item_id
        assert linked.extra["auto_link_reason"] == "exact_unique_title"


def test_auto_link_leaves_duplicate_exact_titles_ambiguous():
    workspace_id = _workspace()
    with db.session_scope() as session:
        first = models.InventoryItem(
            workspace_id=workspace_id,
            sku="COPY-1",
            title="Stoner",
            category="book",
            quantity=1,
            status="active",
            attributes={},
        )
        second = models.InventoryItem(
            workspace_id=workspace_id,
            sku="COPY-2",
            title="Stoner",
            category="book",
            quantity=1,
            status="active",
            attributes={},
        )
        session.add_all([first, second])
        session.flush()
        _listing(session, workspace_id, first, "vinted", "COPY-V1")
        _listing(session, workspace_id, second, "vinted", "COPY-V2")
        sale = _sale(session, workspace_id, None)
        sale.title = "STONER"
        sale_id = sale.id

        result = auto_link_unlinked_sales(session, workspace_id)
        assert result == {
            "linked": 0,
            "remaining": 1,
            "ambiguous": 1,
            "unmatched": 0,
            "actions_created": 0,
        }
        assert session.get(models.Sale, sale_id).inventory_item_id is None


def test_vinted_sync_retries_historical_unlinked_sale_after_listing_arrives():
    workspace_id = _workspace()
    with db.session_scope() as session:
        sale = _sale(session, workspace_id, None)
        sale.title = "Late arriving listing"
        sale_id = sale.id

    result = record_workspace_snapshot(
        workspace_id,
        {
            "collected_at": NOW.timestamp(),
            "current_user": {},
            "listings": [
                {
                    "id": "LATE-1",
                    "title": "Late arriving listing",
                    "status": "active",
                    "price_cents": 1200,
                    "currency": "EUR",
                }
            ],
            "notifications": [],
            "orders": [],
        },
    )
    assert result["sales_auto_linked"] == 1
    assert result["sales_remaining_unlinked"] == 0

    with db.session_scope() as session:
        linked = session.get(models.Sale, sale_id)
        assert linked.inventory_item_id is not None
        item = session.get(models.InventoryItem, linked.inventory_item_id)
        assert item.title == "Late arriving listing"
        assert item.status == "sold"



def test_auto_link_does_not_attach_old_sale_to_newer_active_copy():
    workspace_id = _workspace()
    with db.session_scope() as session:
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="NEW-COPY",
            title="Repeated title",
            category="book",
            quantity=1,
            status="active",
            attributes={},
        )
        session.add(item)
        session.flush()
        _listing(session, workspace_id, item, "vinted", "NEW-COPY")
        sale = _sale(session, workspace_id, None)
        sale.title = "Repeated title"
        sale.occurred_at = NOW - timedelta(days=30)
        sale.first_seen_at = NOW - timedelta(days=30)
        sale.last_seen_at = NOW - timedelta(days=30)
        sale_id = sale.id

        result = auto_link_unlinked_sales(session, workspace_id)
        assert result["linked"] == 0
        assert result["unmatched"] == 1
        assert session.get(models.Sale, sale_id).inventory_item_id is None


def test_reconciliation_ui_classification_only_requires_ambiguous_sales():
    workspace_id = _workspace()
    with db.session_scope() as session:
        first = models.InventoryItem(
            workspace_id=workspace_id,
            sku="AMB-1",
            title="Duplicate title",
            category="book",
            quantity=0,
            status="sold",
            attributes={},
        )
        second = models.InventoryItem(
            workspace_id=workspace_id,
            sku="AMB-2",
            title="Duplicate title",
            category="book",
            quantity=0,
            status="sold",
            attributes={},
        )
        session.add_all([first, second])
        session.flush()
        ambiguous = _sale(session, workspace_id, None)
        ambiguous.title = "duplicate title"

        orphan = _sale(session, workspace_id, None)
        orphan.title = "No retained stock record"

        classified = unlinked_sale_reconciliation(session, workspace_id)
        assert classified["review_count"] == 1
        assert classified["historical_unmatched_count"] == 1
        assert classified["review"][0]["id"] == str(ambiguous.id)
        assert set(classified["review"][0]["candidate_item_ids"]) == {
            str(first.id),
            str(second.id),
        }



def test_auto_link_does_not_reuse_item_already_consumed_by_another_sale():
    workspace_id = _workspace()
    with db.session_scope() as session:
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="ONE-COPY",
            title="One retained copy",
            category="book",
            quantity=0,
            status="sold",
            attributes={},
        )
        session.add(item)
        session.flush()
        _listing(session, workspace_id, item, "vinted", "ONE-COPY", status="sold")
        existing = _sale(session, workspace_id, item)
        existing.title = "One retained copy"

        second = _sale(session, workspace_id, None)
        second.title = "One retained copy"
        second_id = second.id

        result = auto_link_unlinked_sales(session, workspace_id)
        assert result["linked"] == 0
        assert result["unmatched"] == 1
        assert session.get(models.Sale, second_id).inventory_item_id is None



def test_biblio_cross_channel_completion_commits_sold_state_and_signature_together(monkeypatch):
    workspace_id = _workspace()
    with db.session_scope() as session:
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="MASTER-SOLD",
            title="Sold Book",
            category="book",
            quantity=1,
            status="active",
            attributes={},
        )
        session.add(item)
        session.flush()
        _listing(session, workspace_id, item, "vinted", "V-SOLD", status="sold")
        biblio = _listing(session, workspace_id, item, "biblio", "REMOTE-SOLD")
        sale = _sale(session, workspace_id, item)
        created = plan_sale_reconciliation(session, sale)
        action = next(row for row in created if row.channel == "biblio")
        action_id = action.id
        listing_id = biblio.id

    monkeypatch.setattr(
        hosted,
        "close_biblio_workspace_listing",
        lambda workspace_id, listing_id: {
            "remote": "delete_uploaded",
            "external_id": "REMOTE-SOLD",
            "deletes_filename": "delete.txt",
            "inventory_signature": "sold-signature",
        },
    )

    result = execute_action(action_id)
    assert result["remote"] == "delete_uploaded"

    with db.session_scope() as session:
        listing = session.get(models.ChannelListing, listing_id)
        action = session.get(CrossChannelAction, action_id)
        assert listing.status == "sold"
        assert listing.quantity == 0
        assert listing.extra["inventory_sync_signature"] == "sold-signature"
        assert listing.extra["inventory_synced_at"]
        assert listing.extra["publish_state"] == "ftp_uploaded"
        assert action.status == "success"


def test_delayed_jobs_are_not_claimable_before_available_time():
    workspace_id = _workspace()
    job_id = jobs.enqueue(
        "biblio_sync",
        {"photos_only": True},
        workspace_id,
        delay_seconds=60,
    )
    assert jobs.claim_one() is None
    with db.session_scope() as session:
        row = session.get(BackgroundJob, job_id)
        assert row.status == "queued"
        assert row.available_at > NOW
