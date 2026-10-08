"""Phase 1: physical stock authority and marketplace relation safety."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.connectors.workspace_sync import (
    record_workspace_channel_snapshot, record_workspace_channel_orders,
    recompute_inventory_item,
)
from app.constants import Channel, ItemCategory, ItemStatus, ListingStatus
from app.cross_channel import reconcile_sale_state
from app.reconciliation import apply_reconciliation_merges
from app.stock_relations import record_physical_quantity, is_physical, is_provisional
from app.workspace_ingest import record_workspace_snapshot
from app.product_models import CrossChannelAction


def _workspace():
    with db.session_scope() as session:
        row = models.Workspace(name="Phase 1", slug="phase1", settings={})
        session.add(row)
        session.flush()
        return row.id


def _physical(session, workspace, sku="SHARED-SKU", quantity=3):
    row = models.InventoryItem(
        workspace_id=workspace,
        sku=sku,
        title="One particular physical copy",
        category=ItemCategory.BOOK,
        quantity=quantity,
        status=ItemStatus.ACTIVE,
        attributes={"isbn": "9780140328721"},
    )
    session.add(row)
    session.flush()
    record_physical_quantity(session, row, quantity)
    return row


def _listing(session, workspace, item, channel, external_id):
    row = models.ChannelListing(
        workspace_id=workspace, inventory_item_id=item.id, channel=channel,
        external_id=external_id, external_sku=item.sku, title="Advertised item",
        quantity=1, status=ListingStatus.ACTIVE, extra={},
    )
    session.add(row)
    session.flush()
    return row


def test_same_marketplace_sku_across_channels_does_not_auto_merge_stock():
    workspace = _workspace()
    with db.session_scope() as session:
        physical = _physical(session, workspace)
        physical_id = physical.id

    now = datetime.now(timezone.utc)
    for channel, remote_id in ((Channel.EBAY, "E-1"), (Channel.ETSY, "T-1")):
        record_workspace_channel_snapshot(
            workspace, channel,
            [{"source_id": remote_id, "sku": "SHARED-SKU", "title": "Same book",
              "quantity": 1, "status": "active", "price_cents": 800}],
            synced_at=now, full_snapshot=True,
        )
    with db.session_scope() as session:
        stock = session.execute(select(models.InventoryItem)).scalars().all()
        assert len(stock) == 3
        original = session.get(models.InventoryItem, physical_id)
        assert original.quantity == 3
        assert is_physical(original)
        imported = [item for item in stock if item.id != physical_id]
        assert all(is_provisional(item) for item in imported)
        assert {item.attributes["connector_import_channel"] for item in imported} == {"ebay", "etsy"}
        rows = session.execute(select(models.ChannelListing)).scalars().all()
        assert len({row.inventory_item_id for row in rows}) == 2
        assert all(row.external_sku == "SHARED-SKU" for row in rows)
        original_ids = {(row.channel, row.external_id): row.inventory_item_id for row in rows}
    record_workspace_channel_snapshot(
        workspace, Channel.EBAY,
        [{"source_id": "E-1", "sku": "SHARED-SKU", "title": "Edited",
          "quantity": 9, "status": "active"}],
        synced_at=now, full_snapshot=False,
    )
    with db.session_scope() as session:
        original = session.get(models.InventoryItem, physical_id)
        assert original.quantity == 3
        refreshed = session.execute(
            select(models.ChannelListing).where(models.ChannelListing.channel == "ebay")
        ).scalar_one()
        assert refreshed.inventory_item_id == original_ids[("ebay", "E-1")]
        assert refreshed.title == "Edited"


def test_vinted_import_keeps_master_separate_and_protects_confirmed_title_and_quantity():
    workspace = _workspace()
    with db.session_scope() as session:
        physical = _physical(session, workspace, sku="BOOK-COPY", quantity=3)
        physical_id = physical.id
    snapshot = {
        "collected_at": 1_800_000_000,
        "current_user": {},
        "listings": [{
            "id": "991", "sku": "BOOK-COPY", "title": "Marketplace title",
            "status": "active", "price_cents": 700, "currency": "EUR",
            "metadata": {"category": "Books"},
        }],
        "notifications": [], "orders": [],
    }
    record_workspace_snapshot(workspace, snapshot)
    with db.session_scope() as session:
        listing = session.execute(select(models.ChannelListing)).scalar_one()
        assert listing.inventory_item_id != physical_id
        assert is_provisional(session.get(models.InventoryItem, listing.inventory_item_id))
        listing.inventory_item_id = physical_id
        session.flush()
    snapshot["listings"][0]["title"] = "Renamed on Vinted"
    snapshot["collected_at"] = 1_800_000_100
    record_workspace_snapshot(workspace, snapshot)
    with db.session_scope() as session:
        physical = session.get(models.InventoryItem, physical_id)
        listing = session.execute(select(models.ChannelListing)).scalar_one()
        assert listing.inventory_item_id == physical_id
        assert listing.title == "Renamed on Vinted"
        assert physical.title == "One particular physical copy"
        assert physical.quantity == 3


def test_explicit_link_archives_orphan_placeholder_and_protects_master_from_later_snapshots(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={"email": "phase1-link@example.test", "password": "a-long-test-password",
              "workspace_name": "Explicit physical links"},
    )
    assert registered.status_code == 200, registered.text
    csrf = registered.json()["csrf_token"]
    with db.session_scope() as session:
        workspace = session.execute(select(models.Membership)).scalar_one().workspace_id
        master = _physical(session, workspace, sku="MASTER-BOOK", quantity=2)
        master_id = master.id
    record_workspace_channel_snapshot(
        workspace, "ebay", [{"source_id": "E-1", "sku": "MASTER-BOOK",
                             "title": "Remote copy", "status": "active", "quantity": 1}],
        synced_at=datetime.now(timezone.utc), full_snapshot=True,
    )
    with db.session_scope() as session:
        listing = session.execute(select(models.ChannelListing)).scalar_one()
        old_id = listing.inventory_item_id
        listing_id = str(listing.id)
    response = client.post(
        f"/api/app/inventory/{master_id}/link",
        headers={"X-CSRF-Token": csrf}, json={"listing_id": listing_id},
    )
    assert response.status_code == 200, response.text
    with db.session_scope() as session:
        assert is_physical(session.get(models.InventoryItem, master_id))
        previous = session.get(models.InventoryItem, old_id)
        assert previous.quantity == 0
        assert previous.status == ItemStatus.ARCHIVED
        assert session.get(models.ChannelListing, __import__("uuid").UUID(listing_id)).extra["master_link_source"] == "user"
    record_workspace_channel_snapshot(
        workspace, "ebay", [{"source_id": "E-1", "sku": "MASTER-BOOK",
                             "title": "Remote price changed", "status": "active", "quantity": 15}],
        synced_at=datetime.now(timezone.utc), full_snapshot=True,
    )
    with db.session_scope() as session:
        master = session.get(models.InventoryItem, master_id)
        assert master.quantity == 2
        assert master.title == "One particular physical copy"
        assert master.status == ItemStatus.ACTIVE


def test_multi_unit_physical_stock_sale_is_once_per_order_and_closes_only_at_zero():
    workspace = _workspace()
    with db.session_scope() as session:
        item = _physical(session, workspace, quantity=3)
        source = _listing(session, workspace, item, "vinted", "V-1")
        other = _listing(session, workspace, item, "biblio", "B-1")
        sale = models.Sale(
            workspace_id=workspace, inventory_item_id=item.id, channel="vinted",
            external_order_id="sale-first", direction="sell", status="sold",
            lifecycle_status="sold", extra={"quantity": 2},
        )
        session.add(sale)
        session.flush()
        assert reconcile_sale_state(session, sale) == []
        assert item.quantity == 1
        assert item.status == ItemStatus.ACTIVE
        assert reconcile_sale_state(session, sale) == []
        assert item.quantity == 1

        final = models.Sale(
            workspace_id=workspace, inventory_item_id=item.id, channel="vinted",
            external_order_id="sale-final", direction="sell", status="sold",
            lifecycle_status="sold", extra={"quantity": 1},
        )
        session.add(final)
        session.flush()
        actions = reconcile_sale_state(session, final)
        assert len(actions) == 1
        assert actions[0].channel_listing_id == other.id
        assert item.quantity == 0
        assert item.status == ItemStatus.SOLD
        assert reconcile_sale_state(session, final) == []
        assert session.execute(select(CrossChannelAction)).scalars().all() == actions
        final.status = "cancelled"
        final.lifecycle_status = "cancelled"
        reconcile_sale_state(session, final)
        assert item.quantity == 1
        assert item.status == ItemStatus.ACTIVE
        assert actions[0].status == "cancelled"

        # A manual physical stock count is a remaining-quantity reading, not
        # a new historical starting quantity; repeated sales stay idempotent.
        record_physical_quantity(session, item, 5)
        recompute_inventory_item(session, item)
        assert item.quantity == 5
        assert item.attributes["stock_base_quantity"] == 7


def test_order_sku_alone_does_not_consume_physical_master():
    workspace = _workspace()
    with db.session_scope() as session:
        master = _physical(session, workspace, sku="SKU-ALONE", quantity=2)
        master_id = master.id
    result = record_workspace_channel_orders(
        workspace, "etsy",
        [{"external_order_id": "O-1", "sku": "SKU-ALONE", "title": "Different title",
          "quantity": 1, "status": "paid", "lifecycle_status": "paid"}],
        synced_at=datetime.now(timezone.utc),
    )
    assert result["orders"] == 1
    with db.session_scope() as session:
        sale = session.execute(select(models.Sale)).scalar_one()
        assert sale.inventory_item_id is None
        assert session.get(models.InventoryItem, master_id).quantity == 2


def test_explicit_merge_marks_verified_stock_and_does_not_sum_advertised_quantities():
    workspace = _workspace()
    now = datetime.now(timezone.utc)
    for channel, external_id in (("ebay", "E-1"), ("etsy", "T-1")):
        record_workspace_channel_snapshot(
            workspace, channel, [{"source_id": external_id, "sku": "DUPLICATE-PRODUCT",
                                  "title": "Same listing", "quantity": 4, "status": "active"}],
            synced_at=now, full_snapshot=False,
        )
    with db.session_scope() as session:
        items = session.execute(select(models.InventoryItem)).scalars().all()
        assert len(items) == 2
        target, source = items
        apply_reconciliation_merges(session, workspace, [(target.id, source.id)])
        merged = session.get(models.InventoryItem, target.id)
        assert merged.quantity == 4
        assert is_physical(merged)
        assert merged.attributes["stock_base_quantity"] == 4
        assert not merged.attributes.get("connector_import_placeholder")
        assert session.get(models.InventoryItem, source.id) is None
        assert len(session.execute(
            select(models.ChannelListing).where(models.ChannelListing.inventory_item_id == target.id)
        ).scalars().all()) == 2


def test_relationship_audit_is_read_only_workspace_scoped_and_shows_sku_conflicts(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    first = TestClient(entry.app)
    second = TestClient(entry.app)
    for client, address in ((first, "phase1-audit-one@example.test"), (second, "phase1-audit-two@example.test")):
        response = client.post("/api/auth/register", json={
            "email": address, "password": "a-long-test-password",
            "workspace_name": address,
        })
        assert response.status_code == 200, response.text
    with db.session_scope() as session:
        first_email = session.execute(
            select(models.User).where(models.User.email == "phase1-audit-one@example.test")
        ).scalar_one()
        workspace = session.execute(
            select(models.Membership).where(models.Membership.user_id == first_email.id)
        ).scalar_one().workspace_id
        master = _physical(session, workspace, sku="REPEATED-SKU", quantity=1)
        master_id = master.id
        _listing(session, workspace, master, "vinted", "V-1")
        # The remote SKU matches even though its physical identity has not
        # been confirmed. A safe audit must show a warning, not auto-merge.
    record_workspace_channel_snapshot(
        workspace, "ebay", [{"source_id": "E-1", "sku": "REPEATED-SKU",
                              "title": "One particular physical copy",
                              "quantity": 1, "status": "active"}],
        synced_at=datetime.now(timezone.utc), full_snapshot=True,
    )
    anonymous = TestClient(entry.app)
    assert anonymous.get("/api/app/inventory/relationship-audit").status_code == 401
    result = first.get("/api/app/inventory/relationship-audit")
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["physical"] == 1
    assert len(data["provisional"]) == 1
    assert len(data["shared_marketplace_skus"]) == 1
    assert len(data["shared_marketplace_skus"][0]["item_ids"]) == 2
    assert master_id in {
        __import__("uuid").UUID(value)
        for value in data["shared_marketplace_skus"][0]["item_ids"]
    }
    other = second.get("/api/app/inventory/relationship-audit")
    assert other.status_code == 200, other.text
    assert other.json()["stock_items"] == 0
    assert other.json()["shared_marketplace_skus"] == []
    with db.session_scope() as session:
        item = session.get(models.InventoryItem, master_id)
        assert item.quantity == 1
        assert len(session.execute(select(models.InventoryItem).where(
            models.InventoryItem.workspace_id == workspace,
        )).scalars().all()) == 2
