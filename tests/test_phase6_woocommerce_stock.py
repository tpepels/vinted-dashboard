"""Phase 6: WooCommerce stock updates must be remote-ID-safe and readback-verified."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.connectors import hosted
from app.constants import ItemCategory, ItemStatus, ListingStatus
from app.marketplace_operations import MarketplaceOperation
from app.stock_relations import record_physical_quantity


def _woo_mock(monkeypatch, remote, result, *, status=200):
    states = iter([remote, result])
    monkeypatch.setattr(hosted, "_credentials", lambda *args: {
        "store_url": "https://shop.example", "consumer_key": "key", "consumer_secret": "secret",
    })
    monkeypatch.setattr(hosted, "_woo_get", lambda *args, **kwargs: next(states))
    monkeypatch.setattr(hosted, "_woocommerce_base", lambda values: "https://shop.example")
    writes = []
    def put(url, *, headers, json, timeout):
        writes.append((url, json))
        return SimpleNamespace(status_code=status)
    monkeypatch.setattr(hosted.requests, "put", put)
    return writes


def test_woo_simple_product_stock_update_matches_identity_and_readback(monkeypatch):
    writes = _woo_mock(
        monkeypatch,
        {"id": 42, "sku": "COPY-42", "type": "simple", "manage_stock": True,
         "stock_quantity": 5, "stock_status": "instock"},
        {"id": 42, "sku": "COPY-42", "type": "simple", "manage_stock": True,
         "stock_quantity": 0, "stock_status": "outofstock"},
    )
    result = hosted.update_woocommerce_workspace_stock(
        __import__("uuid").uuid4(), external_id="42", expected_sku="COPY-42", quantity=0,
    )
    assert result["remote_verified"] is True
    assert result["quantity"] == 0
    assert len(writes) == 1
    assert writes[0][0].endswith("/wp-json/wc/v3/products/42")
    assert writes[0][1] == {
        "manage_stock": True, "stock_quantity": 0, "stock_status": "outofstock",
    }


def test_woo_rejects_variants_sku_drift_and_invalid_remote_ids(monkeypatch):
    for remote_id in ("42:7", "0", "http://evil.example"):
        with pytest.raises(ValueError, match="numeric remote ID"):
            hosted.update_woocommerce_workspace_stock(
                __import__("uuid").uuid4(), external_id=remote_id,
                expected_sku="COPY-42", quantity=1,
            )
    writes = _woo_mock(
        monkeypatch,
        {"id": 42, "sku": "COPY-DIFFERENT", "type": "simple", "manage_stock": True,
         "stock_quantity": 1, "stock_status": "instock"},
        {},
    )
    with pytest.raises(ValueError, match="SKU no longer matches"):
        hosted.update_woocommerce_workspace_stock(
            __import__("uuid").uuid4(), external_id="42",
            expected_sku="COPY-42", quantity=0,
        )
    assert not writes


def test_woo_remote_readback_must_match_stock_even_after_accepted_put(monkeypatch):
    writes = _woo_mock(
        monkeypatch,
        {"id": 42, "sku": "COPY-42", "type": "simple", "manage_stock": True,
         "stock_quantity": 5, "stock_status": "instock"},
        {"id": 42, "sku": "COPY-42", "type": "simple", "manage_stock": True,
         "stock_quantity": 5, "stock_status": "instock"},
    )
    with pytest.raises(RuntimeError, match="differs after write"):
        hosted.update_woocommerce_workspace_stock(
            __import__("uuid").uuid4(), external_id="42",
            expected_sku="COPY-42", quantity=0,
        )
    assert len(writes) == 1


def test_woo_does_not_repeat_already_matching_remote_quantity(monkeypatch):
    writes = _woo_mock(
        monkeypatch,
        {"id": 42, "sku": "COPY-42", "type": "simple", "manage_stock": True,
         "stock_quantity": 1, "stock_status": "instock"},
        {},
    )
    result = hosted.update_woocommerce_workspace_stock(
        __import__("uuid").uuid4(), external_id="42", expected_sku="COPY-42", quantity=1,
    )
    assert result["already_complete"]
    assert not writes


def test_explicit_woo_api_cannot_write_unknown_or_other_workspace_stock(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    registered = client.post("/api/auth/register", json={
        "email": "woo-stock-phase6@example.test", "password": "a-long-test-password",
        "workspace_name": "Woo stock",
    })
    assert registered.status_code == 200, registered.text
    csrf = registered.json()["csrf_token"]
    with db.session_scope() as session:
        workspace_id = session.execute(select(models.Membership)).scalar_one().workspace_id
        item = models.InventoryItem(
            workspace_id=workspace_id, sku="BOOK-77", title="One book",
            category=ItemCategory.BOOK, status=ItemStatus.ACTIVE,
            quantity=2, attributes={},
        )
        session.add(item)
        session.flush()
        record_physical_quantity(session, item, 2)
        listing = models.ChannelListing(
            workspace_id=workspace_id, inventory_item_id=item.id,
            channel="woocommerce", external_id="77", external_sku="BOOK-77",
            title=item.title, status=ListingStatus.ACTIVE,
            quantity=1, extra={},
        )
        session.add(listing)
        session.flush()
        item_id, listing_id = item.id, listing.id
    called = []
    def update(wid, *, external_id, expected_sku, quantity):
        called.append((wid, external_id, expected_sku, quantity))
        return {"remote_verified": True, "quantity": quantity,
                "external_id": external_id, "status": ListingStatus.ACTIVE,
                "already_complete": False}
    monkeypatch.setattr("app.product_api.update_woocommerce_workspace_stock", update)
    response = client.post(
        f"/api/app/inventory/{item_id}/marketplaces/woocommerce/stock",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    assert called == [(workspace_id, "77", "BOOK-77", 2)]
    with db.session_scope() as session:
        listing = session.get(models.ChannelListing, listing_id)
        assert listing.quantity == 2
        op = session.execute(select(MarketplaceOperation)).scalar_one()
        assert op.status == "succeeded"
        assert op.verification == "remote_verified"
        assert session.get(models.InventoryItem, item_id).quantity == 2


def test_read_only_woo_check_observes_stock_without_put(monkeypatch):
    writes = _woo_mock(
        monkeypatch,
        {"id": 42, "sku": "COPY-42", "type": "simple", "manage_stock": True,
         "stock_quantity": 2, "stock_status": "instock"},
        {},
    )
    remote = hosted.read_woocommerce_workspace_stock(
        __import__("uuid").uuid4(), external_id="42", expected_sku="COPY-42",
    )
    assert remote["quantity"] == 2
    assert remote["manage_stock"] is True
    assert writes == []


def test_woo_readback_releases_ambiguous_write_when_stock_is_observed(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    r = client.post("/api/auth/register", json={
        "email": "woo-check-phase6@example.test", "password": "a-long-test-password",
        "workspace_name": "Woo readback",
    })
    assert r.status_code == 200
    csrf = r.json()["csrf_token"]
    with db.session_scope() as session:
        workspace_id = session.execute(select(models.Membership)).scalar_one().workspace_id
        item = models.InventoryItem(
            workspace_id=workspace_id, sku="BOOK-99", title="Checked book",
            category=ItemCategory.BOOK, status=ItemStatus.ACTIVE,
            quantity=1, attributes={},
        )
        session.add(item)
        session.flush()
        record_physical_quantity(session, item, 1)
        listing = models.ChannelListing(
            workspace_id=workspace_id, inventory_item_id=item.id,
            channel="woocommerce", external_id="99", external_sku="BOOK-99",
            title=item.title, status=ListingStatus.ACTIVE, quantity=2, extra={},
        )
        session.add(listing)
        session.flush()
        op = MarketplaceOperation(
            workspace_id=workspace_id, channel="woocommerce",
            operation_type="update", target_key=str(listing.id),
            inventory_item_id=item.id, channel_listing_id=listing.id,
            status="attention", verification="not_checked",
            active_key=None, attempts=1, job_payload={}, result={},
        )
        session.add(op)
        session.flush()
        item_id, op_id = item.id, op.id

    monkeypatch.setattr("app.product_api.read_woocommerce_workspace_stock", lambda *args, **kwargs: {
        "external_id": "99", "quantity": 1, "manage_stock": True,
        "status": ListingStatus.ACTIVE, "stock_status": "instock",
    })
    result = client.post(
        f"/api/app/inventory/{item_id}/marketplaces/woocommerce/check-stock",
        headers={"X-CSRF-Token": csrf},
    )
    assert result.status_code == 200, result.text
    assert result.json()["matches"] is True
    with db.session_scope() as session:
        op = session.get(MarketplaceOperation, op_id)
        assert op.status == "succeeded"
        assert op.verification == "remote_verified"
