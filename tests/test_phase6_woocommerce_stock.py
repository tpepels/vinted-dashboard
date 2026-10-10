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
    for remote_id in ("0", "http://evil.example", "42:0", "0:7", "42:7:8"):
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
        # Old attempts without a stored original quantity may match today's
        # stock but must not be attributed as a verified historical write.
        assert op.status == "attention"
        assert op.verification == "not_checked"


def _variation_shop(monkeypatch, *, managed=True, parent_type="variable",
                    sku="BOOK-77", before=3, after=0, backorders="no"):
    calls = []
    reads = {"variation": 0}
    monkeypatch.setattr(hosted, "_credentials", lambda *args: {
        "store_url": "https://shop.example", "consumer_key": "key", "consumer_secret": "secret",
    })
    monkeypatch.setattr(hosted, "_woocommerce_base", lambda values: "https://shop.example")
    def get(values, path, **kwargs):
        calls.append(("GET", path))
        if path == "products/22":
            return {"id": 22, "type": parent_type, "status": "publish"}
        assert path == "products/22/variations/77"
        reads["variation"] += 1
        quantity = before if reads["variation"] == 1 else after
        return {"id": 77, "sku": sku, "manage_stock": managed,
                "stock_quantity": quantity, "backorders": backorders,
                "stock_status": "instock" if quantity else "outofstock"}
    def put(url, *, headers, json, timeout):
        calls.append(("PUT", url, json))
        return SimpleNamespace(status_code=200)
    monkeypatch.setattr(hosted, "_woo_get", get)
    monkeypatch.setattr(hosted.requests, "put", put)
    return calls


def test_variation_stock_updates_only_the_exact_variation_and_checks_readback(monkeypatch):
    calls = _variation_shop(monkeypatch)
    result = hosted.update_woocommerce_workspace_stock(
        __import__("uuid").uuid4(), external_id="22:77", expected_sku="BOOK-77", quantity=0,
    )
    assert result["remote_verified"] and result["quantity"] == 0
    assert [c[0] for c in calls] == ["GET", "GET", "PUT", "GET", "GET"]
    assert calls[2][1].endswith("/products/22/variations/77")
    assert calls[2][2] == {
        "manage_stock": True, "stock_quantity": 0, "stock_status": "outofstock",
    }


def test_variation_stock_noop_requires_matching_quantity_and_status(monkeypatch):
    calls = _variation_shop(monkeypatch, before=2)
    result = hosted.update_woocommerce_workspace_stock(
        __import__("uuid").uuid4(), external_id="22:77", expected_sku="BOOK-77", quantity=2,
    )
    assert result["already_complete"] and result["remote_verified"]
    assert [c[0] for c in calls] == ["GET", "GET"]


@pytest.mark.parametrize("kwargs,expected", [
    ({"managed": False}, "manages stock at parent level"),
    ({"managed": "parent"}, "manages stock at parent level"),
    ({"backorders": "yes"}, "allows backorders"),
    ({"sku": "CHANGED"}, "SKU no longer matches"),
    ({"parent_type": "simple"}, "no longer variable"),
])
def test_variation_rejects_unsafe_stock_edits(monkeypatch, kwargs, expected):
    calls = _variation_shop(monkeypatch, **kwargs)
    with pytest.raises(ValueError, match=expected):
        hosted.update_woocommerce_workspace_stock(
            __import__("uuid").uuid4(), external_id="22:77",
            expected_sku="BOOK-77", quantity=0,
        )
    assert all(c[0] != "PUT" for c in calls)


def test_variation_requires_a_known_sku_before_its_first_remote_write(monkeypatch):
    calls = _variation_shop(monkeypatch)
    with pytest.raises(ValueError, match="confirmed linked SKU"):
        hosted.update_woocommerce_workspace_stock(
            __import__("uuid").uuid4(), external_id="22:77",
            expected_sku=None, quantity=0,
        )
    assert all(c[0] != "PUT" for c in calls)


def test_variation_after_write_mismatch_requires_attention(monkeypatch):
    calls = _variation_shop(monkeypatch, before=3, after=3)
    with pytest.raises(RuntimeError, match="differs after write"):
        hosted.update_woocommerce_workspace_stock(
            __import__("uuid").uuid4(), external_id="22:77",
            expected_sku="BOOK-77", quantity=0,
        )
    assert [c[0] for c in calls].count("PUT") == 1


def test_variation_item_panel_allows_only_explicit_linked_stock_write(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    r = client.post("/api/auth/register", json={
        "email": "woo-variation-stock@example.test", "password": "a-long-test-password",
        "workspace_name": "Woo variation",
    })
    assert r.status_code == 200, r.text
    csrf = r.json()["csrf_token"]
    with db.session_scope() as session:
        wid = session.execute(select(models.Membership)).scalar_one().workspace_id
        item = models.InventoryItem(
            workspace_id=wid, sku="BOOK-77", title="One variation copy",
            category=ItemCategory.BOOK, status=ItemStatus.ACTIVE,
            quantity=1, attributes={},
        )
        session.add(item)
        session.flush()
        record_physical_quantity(session, item, 1)
        session.add(models.ChannelListing(
            workspace_id=wid, inventory_item_id=item.id, channel="woocommerce",
            external_id="22:77", external_sku="BOOK-77", title=item.title,
            status=ListingStatus.ACTIVE, quantity=3, extra={},
        ))
        item_id = item.id
    overview = client.get(f"/api/app/inventory/{item_id}/marketplace-status")
    assert overview.status_code == 200, overview.text
    assert overview.json()["listings"][0]["can_sync_woocommerce_stock"] is True
    called = []
    def update(wid, *, external_id, expected_sku, quantity):
        called.append((external_id, expected_sku, quantity))
        return {
            "remote_verified": True, "quantity": quantity,
            "external_id": external_id, "status": ListingStatus.ACTIVE,
            "already_complete": False,
        }
    monkeypatch.setattr("app.product_api.update_woocommerce_workspace_stock", update)
    response = client.post(
        f"/api/app/inventory/{item_id}/marketplaces/woocommerce/stock",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    assert called == [("22:77", "BOOK-77", 1)]
