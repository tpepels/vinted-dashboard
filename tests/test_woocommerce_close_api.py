"""Authenticated WooCommerce closure workflow after a confirmed physical sale."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.constants import ItemStatus, ListingStatus
from app.cross_channel import plan_sale_reconciliation
from app.product_models import CrossChannelAction, MarketplaceOperation
from app.stock_relations import record_physical_quantity


def setup():
    client = TestClient(entry.app)
    response = client.post("/api/auth/register", json={
        "email": "close-editor@example.test",
        "password": "a-long-test-password",
        "workspace_name": "Closure test",
    })
    assert response.status_code == 200, response.text
    csrf = response.json()["csrf_token"]
    with db.session_scope() as session:
        workspace_id = session.execute(select(models.Membership)).scalar_one().workspace_id
        item = models.InventoryItem(
            workspace_id=workspace_id, sku="PH-1", title="Sold book",
            category="book", status=ItemStatus.ACTIVE, quantity=1, attributes={},
        )
        session.add(item)
        session.flush()
        record_physical_quantity(session, item, 1)
        listing = models.ChannelListing(
            workspace_id=workspace_id, inventory_item_id=item.id,
            channel="woocommerce", external_id="12", external_sku="PH-1",
            title=item.title, status=ListingStatus.ACTIVE, quantity=1, extra={},
        )
        session.add(listing)
        sale = models.Sale(
            workspace_id=workspace_id, inventory_item_id=item.id,
            channel="vinted", external_order_id="S-1", direction="sell",
            title=item.title, status="sold", lifecycle_status="sold",
            extra={"quantity": 1},
        )
        session.add(sale)
        session.flush()
        created = plan_sale_reconciliation(session, sale)
        assert len(created) == 1 and created[0].mode == "manual"
        assert item.quantity == 0
        return client, {"X-CSRF-Token": csrf}, workspace_id, item.id, listing.id


def remote(status="publish"):
    return {
        "external_id": "12", "sku": "PH-1", "status": status,
        "fingerprint": "snapshot-of-remote-product",
        "can_unpublish": status == "publish",
    }


def test_checked_unpublish_records_verified_result(monkeypatch):
    client, headers, wid, item_id, listing_id = setup()
    path = f"/api/app/inventory/{item_id}/marketplaces/woocommerce"
    monkeypatch.setattr("app.product_api.read_woocommerce_workspace_publication",
                        lambda *_args, **_kw: remote())
    calls = []
    def unpublish(workspace_id, **kwargs):
        calls.append((workspace_id, kwargs))
        return {"remote_verified": True, "status": "draft"}
    monkeypatch.setattr("app.product_api.unpublish_woocommerce_workspace_product", unpublish)
    initial = client.get(f"/api/app/inventory/{item_id}/marketplace-status").json()
    woo = next(row for row in initial["listings"] if row["channel"] == "woocommerce")
    assert woo["can_check_woocommerce_close"]
    assert woo["close_remote_status"] is None
    assert client.post(path + "/close", headers=headers).status_code == 409
    assert client.post(path + "/check-close", headers=headers).status_code == 200
    checked = client.get(f"/api/app/inventory/{item_id}/marketplace-status").json()
    woo = next(row for row in checked["listings"] if row["channel"] == "woocommerce")
    assert woo["close_remote_status"] == "publish"
    result = client.post(path + "/close", headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["remote_verified"]
    assert calls == [(wid, {"external_id": "12", "expected_sku": "PH-1",
                            "expected_fingerprint": "snapshot-of-remote-product"})]
    with db.session_scope() as session:
        listing = session.get(models.ChannelListing, listing_id)
        action = session.execute(select(CrossChannelAction)).scalar_one()
        op = session.execute(select(MarketplaceOperation)).scalar_one()
        assert listing.status == ListingStatus.ENDED
        assert listing.quantity == 0
        assert action.status == "success" and action.mode == "remote"
        assert op.status == "succeeded" and op.verification == "remote_verified"
    assert client.post(path + "/close", headers=headers).status_code == 409


def test_uncertain_write_blocks_retry_until_another_remote_check(monkeypatch):
    client, headers, wid, item_id, _listing_id = setup()
    path = f"/api/app/inventory/{item_id}/marketplaces/woocommerce"
    monkeypatch.setattr("app.product_api.read_woocommerce_workspace_publication",
                        lambda *_args, **_kw: remote())
    assert client.post(path + "/check-close", headers=headers).status_code == 200
    calls = []
    def uncertain(*args, **kwargs):
        calls.append("sent")
        raise RuntimeError("Connection lost after request")
    monkeypatch.setattr("app.product_api.unpublish_woocommerce_workspace_product", uncertain)
    assert client.post(path + "/close", headers=headers).status_code == 502
    assert client.post(path + "/close", headers=headers).status_code == 409
    assert calls == ["sent"]
    with db.session_scope() as session:
        op = session.execute(select(MarketplaceOperation)).scalar_one()
        assert op.status == "attention"
        assert session.execute(select(CrossChannelAction)).scalar_one().status == "error"
    assert client.post(path + "/check-close", headers=headers).status_code == 200
    with db.session_scope() as session:
        op = session.execute(select(MarketplaceOperation)).scalar_one()
        assert op.status == "failed" and op.verification == "remote_mismatch"
        assert session.execute(select(CrossChannelAction)).scalar_one().status == "attention"


def test_read_only_check_can_verify_already_drafted_product(monkeypatch):
    client, headers, wid, item_id, listing_id = setup()
    monkeypatch.setattr("app.product_api.read_woocommerce_workspace_publication",
                        lambda *_args, **_kw: remote("draft"))
    result = client.post(f"/api/app/inventory/{item_id}/marketplaces/woocommerce/check-close",
                         headers=headers)
    assert result.status_code == 200 and result.json()["verified_closed"]
    with db.session_scope() as session:
        assert session.get(models.ChannelListing, listing_id).status == ListingStatus.ENDED
        assert session.execute(select(CrossChannelAction)).scalar_one().status == "success"
        assert not session.execute(select(MarketplaceOperation)).scalars().all()


def test_restock_blocks_remote_close_and_other_workspace_cannot_access(monkeypatch):
    client, headers, wid, item_id, _listing_id = setup()
    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        record_physical_quantity(session, item, 1)
    monkeypatch.setattr("app.product_api.read_woocommerce_workspace_publication",
                        lambda *_args, **_kw: remote())
    assert client.post(f"/api/app/inventory/{item_id}/marketplaces/woocommerce/check-close",
                       headers=headers).status_code == 409
    stranger = TestClient(entry.app)
    response = stranger.post("/api/auth/register", json={
        "email": "other-close-editor@example.test",
        "password": "a-long-test-password", "workspace_name": "Other",
    })
    other = {"X-CSRF-Token": response.json()["csrf_token"]}
    assert stranger.post(f"/api/app/inventory/{item_id}/marketplaces/woocommerce/check-close",
                         headers=other).status_code == 404
    assert stranger.post(f"/api/app/inventory/{item_id}/marketplaces/woocommerce/close",
                         headers=other).status_code == 404
