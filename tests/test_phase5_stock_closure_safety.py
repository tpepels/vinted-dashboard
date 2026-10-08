"""Phase 5: cross-market stock protection under multiple sales and refunds."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app import db, models
from app.constants import ItemStatus, ListingStatus
from app.cross_channel import execute_action, plan_sale_reconciliation, reconcile_sale_state
from app.product_models import BackgroundJob, CrossChannelAction, MarketplaceOperation
from app.stock_relations import record_physical_quantity


def _stock():
    with db.session_scope() as session:
        workspace = models.Workspace(name="Phase 5", slug="phase5", settings={})
        session.add(workspace)
        session.flush()
        item = models.InventoryItem(
            workspace_id=workspace.id, sku="PHYSICAL-2", title="Two identical copies",
            category="book", status=ItemStatus.ACTIVE, quantity=2, attributes={},
        )
        session.add(item)
        session.flush()
        record_physical_quantity(session, item, 2)
        for channel, external in (("vinted", "V-1"), ("biblio", "B-1")):
            session.add(models.ChannelListing(
                workspace_id=workspace.id, inventory_item_id=item.id,
                channel=channel, external_id=external, title=item.title,
                status=ListingStatus.ACTIVE, quantity=2, extra={},
            ))
        session.flush()
        sales = []
        for number in (1, 2):
            sale = models.Sale(
                workspace_id=workspace.id, inventory_item_id=item.id,
                channel="vinted", external_order_id=f"ORDER-{number}",
                direction="sell", title=item.title, status="sold",
                lifecycle_status="sold", extra={"quantity": 1},
            )
            session.add(sale)
            sales.append(sale)
        session.flush()
        return workspace.id, item.id, sales[0].id, sales[1].id


def test_refund_of_one_of_two_sales_cancels_another_sales_pending_close():
    workspace_id, item_id, first_id, second_id = _stock()
    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        # Reconcile after only one consuming sale to simulate incoming order
        # notifications reaching the dashboard at different times.
        first = session.get(models.Sale, first_id)
        second = session.get(models.Sale, second_id)
        second.status = "cancelled"
        second.lifecycle_status = "cancelled"
        assert plan_sale_reconciliation(session, first) == []
        assert item.quantity == 1
        second.status = "sold"
        second.lifecycle_status = "sold"
        created = plan_sale_reconciliation(session, second)
        assert len(created) == 1
        assert item.quantity == 0
        action = created[0]
        operation = session.execute(
            select(MarketplaceOperation).where(
                MarketplaceOperation.workspace_id == workspace_id,
                MarketplaceOperation.channel_listing_id == action.channel_listing_id,
            )
        ).scalar_one()
        old_job = session.get(BackgroundJob, operation.job_id)
        assert old_job.status == "queued"
        action_id, op_id, job_id = action.id, operation.id, old_job.id

        # A *different* sale was refunded. The close belongs to second.
        first.status = "refunded"
        first.lifecycle_status = "refunded"
        reconcile_sale_state(session, first)
        assert item.quantity == 1
        assert session.get(CrossChannelAction, action_id).status == "cancelled"
        assert session.get(MarketplaceOperation, op_id).status == "cancelled"
        assert session.get(BackgroundJob, job_id).status == "cancelled"

    # Even if an old task gets dispatched, it must not close the BIBLIO book.
    result = execute_action(action_id)
    assert result["already_complete"] is True
    with db.session_scope() as session:
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.channel == "biblio",
            )
        ).scalar_one()
        assert listing.status == ListingStatus.ACTIVE


def test_worker_checks_authoritative_stock_before_initiating_remote_close(monkeypatch):
    workspace_id, item_id, first_id, second_id = _stock()
    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        first = session.get(models.Sale, first_id)
        second = session.get(models.Sale, second_id)
        created = plan_sale_reconciliation(session, second)
        assert created
        action_id = created[0].id
        # Human physical stock recount restores stock after close was queued.
        record_physical_quantity(session, item, 1)
        assert item.quantity == 1

    def forbidden(*args, **kwargs):
        raise AssertionError("Remote close must not run when stock exists")

    monkeypatch.setattr("app.connectors.hosted.close_biblio_workspace_listing", forbidden)
    result = execute_action(action_id)
    assert result.get("skipped")
    with db.session_scope() as session:
        action = session.get(CrossChannelAction, action_id)
        assert action.status == "cancelled"
        listing = session.get(models.ChannelListing, action.channel_listing_id)
        assert listing.status == ListingStatus.ACTIVE


def test_completed_close_that_becomes_incorrect_is_flagged_for_manual_reopening(monkeypatch):
    workspace_id, item_id, first_id, second_id = _stock()
    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        first = session.get(models.Sale, first_id)
        second = session.get(models.Sale, second_id)
        created = plan_sale_reconciliation(session, second)
        action_id = created[0].id

    monkeypatch.setattr(
        "app.connectors.hosted.close_biblio_workspace_listing",
        lambda *_args: {"remote": "delete_uploaded", "inventory_signature": "delete-signature"},
    )
    outcome = execute_action(action_id)
    assert outcome["remote"] == "delete_uploaded"
    with db.session_scope() as session:
        first = session.get(models.Sale, first_id)
        item = session.get(models.InventoryItem, item_id)
        first.status = "cancelled"
        first.lifecycle_status = "cancelled"
        reconcile_sale_state(session, first)
        action = session.get(CrossChannelAction, action_id)
        assert action.status == "success"
        assert action.detail["needs_reopen"] is True
        assert item.quantity == 1
        listing = session.get(models.ChannelListing, action.channel_listing_id)
        assert listing.status == ListingStatus.SOLD
        # Returning stock locally is not proof the remote listing was reopened.
