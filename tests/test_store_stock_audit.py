"""Read-only batch stock audit regression tests."""
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, jobs, models, store_stock_audit, worker
from app.constants import ItemCategory, ItemStatus, ListingStatus
from app.product_models import BackgroundJob
from app.stock_relations import record_physical_quantity


def prepare():
    client = TestClient(entry.app)
    r = client.post("/api/auth/register", json={
        "email": "batch-audit@example.test", "password": "a-long-test-password",
        "workspace_name": "Stock audits",
    })
    assert r.status_code == 200, r.text
    csrf = r.json()["csrf_token"]
    with db.session_scope() as session:
        wid = session.execute(select(models.Membership)).scalar_one().workspace_id
        ids = {}
        for channel, quantity in [("woocommerce", 2), ("shopify", 1), ("wix", 0)]:
            item = models.InventoryItem(
                workspace_id=wid, sku="SKU-" + channel,
                title="Book " + channel, category=ItemCategory.BOOK,
                status=ItemStatus.ACTIVE, quantity=quantity, attributes={},
            )
            session.add(item)
            session.flush()
            record_physical_quantity(session, item, quantity)
            listing = models.ChannelListing(
                workspace_id=wid, inventory_item_id=item.id, channel=channel,
                external_id=channel + "-id", external_sku=item.sku,
                title=item.title, status=ListingStatus.ACTIVE, quantity=5, extra={},
            )
            session.add(listing)
            session.flush()
            ids[channel] = (item.id, listing.id)
    return client, csrf, wid, ids


def queue(client, csrf):
    r = client.post("/api/app/inventory/store-stock-audit",
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    return r.json()["job_id"]


def test_audit_checks_all_and_preserves_master_stock(monkeypatch):
    client, csrf, wid, ids = prepare()
    channels = []
    def remote(workspace_id, target):
        assert workspace_id == wid
        channels.append(target["channel"])
        if target["channel"] == "wix":
            raise ValueError("Tracking disabled")
        return {
            "quantity": 2 if target["channel"] == "woocommerce" else 4,
            "manage_stock": True, "stock_status": "instock",
        }
    monkeypatch.setattr(store_stock_audit, "_read", remote)
    job_id = queue(client, csrf)
    assert queue(client, csrf) == job_id
    job = jobs.claim_one()
    assert job["id"] == job_id and job["job_type"] == "store_stock_audit"
    worker.handle(job)
    jobs.complete(job_id)
    report = client.get("/api/app/inventory/store-stock-audit").json()
    assert report["job"]["status"] == "success"
    assert report["counts"] == {"matched": 1, "mismatch": 1, "error": 1}
    assert [row["state"] for row in report["results"]] == ["mismatch", "error", "matched"]
    assert sorted(channels) == ["shopify", "wix", "woocommerce"]
    with db.session_scope() as session:
        for channel, (item_id, listing_id) in ids.items():
            assert session.get(models.InventoryItem, item_id).quantity == {
                "woocommerce": 2, "shopify": 1, "wix": 0,
            }[channel]
            listing = session.get(models.ChannelListing, listing_id)
            assert listing.quantity == 5
            assert listing.extra["store_stock_audit"]["run_id"] == job_id


def test_duplicated_and_provisional_links_are_not_queried(monkeypatch):
    client, csrf, wid, ids = prepare()
    with db.session_scope() as session:
        listing = session.get(models.ChannelListing, ids["shopify"][1])
        session.add(models.ChannelListing(
            workspace_id=wid, inventory_item_id=listing.inventory_item_id,
            channel="shopify", external_id="other-id", external_sku="different",
            title="Second link", status=ListingStatus.ACTIVE, extra={},
        ))
        item = models.InventoryItem(
            workspace_id=wid, sku="PROVISIONAL", title="Unconfirmed",
            category=ItemCategory.BOOK, quantity=3, status=ItemStatus.ACTIVE,
            attributes={"stock_authority": "provisional"},
        )
        session.add(item)
        session.flush()
        session.add(models.ChannelListing(
            workspace_id=wid, inventory_item_id=item.id, channel="wix",
            external_id="provisional", external_sku="provisional",
            title="Unconfirmed", status=ListingStatus.ACTIVE, extra={},
        ))
    called = []
    def read(wid2, target):
        called.append(target["channel"])
        return {"quantity": target["local_quantity"], "manage_stock": True,
                "stock_status": "instock" if target["local_quantity"] else "outofstock"}
    monkeypatch.setattr(store_stock_audit, "_read", read)
    job_id = queue(client, csrf)
    store_stock_audit.run(wid, job_id)
    report = client.get("/api/app/inventory/store-stock-audit").json()
    assert report["counts"]["skipped"] == 2
    assert sorted(called) == ["wix", "woocommerce"]
    assert len(report["results"]) == 4
    assert not any(row["sku"] == "PROVISIONAL" for row in report["results"])


def test_stale_local_quantity_during_remote_read_cannot_verify(monkeypatch):
    client, csrf, wid, ids = prepare()
    def read(wid2, target):
        if target["channel"] == "woocommerce":
            with db.session_scope() as session:
                item = session.get(models.InventoryItem, uuid.UUID(target["item_id"]))
                record_physical_quantity(session, item, 7)
        return {"quantity": target["local_quantity"], "manage_stock": True,
                "stock_status": "instock" if target["local_quantity"] else "outofstock"}
    monkeypatch.setattr(store_stock_audit, "_read", read)
    job_id = queue(client, csrf)
    store_stock_audit.run(wid, job_id)
    report = client.get("/api/app/inventory/store-stock-audit").json()
    assert report["counts"]["changed"] == 1
    with db.session_scope() as session:
        listing = session.get(models.ChannelListing, ids["woocommerce"][1])
        assert listing.extra["store_stock_audit"]["state"] == "changed"
        assert listing.extra["store_stock_audit"]["remote_quantity"] is None
        assert session.get(models.InventoryItem, ids["woocommerce"][0]).quantity == 7


def test_workspace_isolation_and_bound(monkeypatch):
    client, csrf, wid, ids = prepare()
    other = TestClient(entry.app)
    r = other.post("/api/auth/register", json={
        "email": "other-audit@example.test", "password": "a-long-test-password",
        "workspace_name": "Other",
    })
    assert r.status_code == 200
    assert other.get("/api/app/inventory/store-stock-audit").json()["eligible"] == 0
    assert other.post("/api/app/inventory/store-stock-audit",
                      headers={"X-CSRF-Token": r.json()["csrf_token"]}).status_code == 409
    monkeypatch.setattr(store_stock_audit, "MAX_TARGETS", 2)
    assert client.post("/api/app/inventory/store-stock-audit",
                       headers={"X-CSRF-Token": csrf}).status_code == 409
    with db.session_scope() as session:
        assert not session.execute(select(BackgroundJob).where(
            BackgroundJob.workspace_id == wid,
            BackgroundJob.job_type == store_stock_audit.JOB_TYPE,
        )).scalars().all()
