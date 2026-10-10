"""Read-only reconciliation for uncertain remote writes and their causal limits."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.constants import ItemCategory, ItemStatus, ListingStatus
from app.product_models import MarketplaceOperation
from app.stock_relations import record_physical_quantity
from app import remote_reconciliation as rr


def fixture(channel="woocommerce", scope="stock", *,
            quantity=2, snapshot=True, status="attention", identity=True):
    email = "remote-inspection-" + uuid.uuid4().hex + "@example.test"
    client = TestClient(entry.app)
    result = client.post("/api/auth/register", json={
        "email": email, "password": "a-long-test-password",
        "workspace_name": "Inspect uncertain marketplace results",
    })
    assert result.status_code == 200, result.text
    headers = {"X-CSRF-Token": result.json()["csrf_token"]}
    with db.session_scope() as session:
        user = session.execute(select(models.User).where(models.User.email == email)).scalar_one()
        wid = session.execute(select(models.Membership.workspace_id).where(
            models.Membership.user_id == user.id,
        )).scalar_one()
        item = models.InventoryItem(
            workspace_id=wid, sku="ONE-COPY", title="One physical item",
            category=ItemCategory.BOOK, quantity=quantity,
            status=ItemStatus.ACTIVE, currency="EUR", attributes={},
        )
        session.add(item)
        session.flush()
        record_physical_quantity(session, item, quantity)
        external = "gid://shopify/ProductVariant/123" if channel == "shopify" else (
            f"{uuid.uuid4()}:{uuid.uuid4()}" if channel == "wix" else "123"
        )
        listing = models.ChannelListing(
            workspace_id=wid, inventory_item_id=item.id,
            channel=channel, external_id=external, external_sku="ONE-COPY",
            title="One physical item", status=ListingStatus.ACTIVE,
            quantity=quantity, price_cents=2000, extra={},
        )
        session.add(listing)
        session.flush()
        target_suffix = {
            "stock": "", "price": ":price", "content": ":content", "close": ":unpublish",
        }[scope]
        intent = {
            "stock": {"scope": "stock", "quantity": quantity},
            "price": {"scope": "price", "price_cents": 2500, "currency": "EUR"},
            "content": {"scope": "content", "changes": {"title": "New title"}},
            "close": {"scope": "unpublish", "expected_status": "draft"},
        }[scope].copy()
        if identity:
            intent.update({"external_id": external, "sku": "ONE-COPY"})
        if not snapshot:
            intent = {}
        op = MarketplaceOperation(
            workspace_id=wid, inventory_item_id=item.id,
            channel_listing_id=listing.id, channel=channel,
            operation_type="close" if scope == "close" else "update",
            target_key=str(listing.id)+target_suffix,
            job_payload=intent, status=status, verification="manual_required",
            started_at=datetime.now(timezone.utc)-timedelta(minutes=1),
            created_at=datetime.now(timezone.utc)-timedelta(minutes=2),
        )
        session.add(op)
        session.flush()
        return client, headers, wid, item.id, listing.id, op.id


def inspect_api(client, headers, op_id):
    return client.post(f"/api/app/marketplace-operations/{op_id}/inspect-remote",
                       headers=headers)


@pytest.mark.parametrize("channel", ["woocommerce", "shopify", "wix"])
def test_stock_inspection_finds_original_intent_without_mutating_stock(channel, monkeypatch):
    client, headers, wid, item_id, listing_id, op_id = fixture(channel)
    observed = []
    def read(workspace_id, *, external_id, expected_sku):
        observed.append((workspace_id, external_id, expected_sku))
        return {"quantity":2,"manage_stock":True,"stock_status":"instock","status":"active"}
    monkeypatch.setitem(rr.STOCK_READERS, channel, read)
    result = inspect_api(client, headers, op_id)
    assert result.status_code == 200, result.text
    assert result.json()["resolved"] is True
    assert result.json()["matched_now"] is True
    assert result.json()["remote_write"] is False
    assert result.json()["outcome"] == "matched"
    assert len(observed) == 1 and observed[0][0] == wid
    with db.session_scope() as session:
        item = session.get(models.InventoryItem,item_id)
        listing = session.get(models.ChannelListing,listing_id)
        op = session.get(MarketplaceOperation,op_id)
        assert item.quantity == 2
        assert listing.quantity == 2
        assert op.status == "succeeded"
        assert op.verification == "remote_verified"
        assert op.result["inspection"]["expected_quantity"] == 2
        assert op.result["inspection"]["observed_quantity"] == 2


def test_remote_stock_difference_does_not_prove_old_write_failed(monkeypatch):
    client, headers, wid, item_id, listing_id, op_id = fixture()
    monkeypatch.setitem(rr.STOCK_READERS,"woocommerce",lambda *_args,**_kwargs:{
        "quantity":1,"manage_stock":True,"stock_status":"instock",
    })
    result = inspect_api(client, headers, op_id)
    assert result.status_code == 200, result.text
    assert result.json()["outcome"] == "different"
    assert not result.json()["resolved"]
    with db.session_scope() as session:
        op = session.get(MarketplaceOperation,op_id)
        assert op.status == "attention", "Mismatch must not be called a failed write"
        assert op.verification == "remote_mismatch"
        assert session.get(models.InventoryItem,item_id).quantity == 2


def test_old_stock_operation_without_original_quantity_requires_manual_review(monkeypatch):
    client, headers, wid, item_id, listing_id, op_id = fixture(snapshot=False)
    def forbidden(*_args,**_kwargs):
        raise AssertionError("No remote GET should happen without original intent")
    monkeypatch.setitem(rr.STOCK_READERS,"woocommerce",forbidden)
    result = inspect_api(client, headers, op_id)
    assert result.status_code == 409
    assert "original write snapshot" in result.json()["detail"]
    op = client.get(f"/api/app/marketplace-operations/{op_id}").json()
    assert not op["can_inspect_remote"]
    assert op["status"] == "attention"


def test_changed_link_during_read_aborts_without_tampering_with_record(monkeypatch):
    client, headers, wid, item_id, listing_id, op_id = fixture()
    def read(*_args,**_kwargs):
        with db.session_scope() as session:
            listing = session.get(models.ChannelListing,listing_id)
            listing.external_id = "999"
        return {"quantity":2,"manage_stock":True,"stock_status":"instock"}
    monkeypatch.setitem(rr.STOCK_READERS,"woocommerce",read)
    result = inspect_api(client, headers, op_id)
    assert result.status_code == 409
    with db.session_scope() as session:
        op = session.get(MarketplaceOperation,op_id)
        assert op.status == "attention"
        assert op.result == {}


def test_original_identity_not_saved_cannot_attribute_a_matching_price(monkeypatch):
    client, headers, wid, item_id, listing_id, op_id = fixture(scope="price",identity=False)
    monkeypatch.setitem(rr.PRICE_READERS,"woocommerce",lambda *_args,**_kwargs:{
        "regular_price_cents":2500,
    })
    outcome=inspect_api(client,headers,op_id)
    assert outcome.status_code == 200
    assert outcome.json()["outcome"] == "link_unproven"
    assert outcome.json()["matched_now"]
    assert not outcome.json()["resolved"]
    with db.session_scope() as session:
        assert session.get(MarketplaceOperation,op_id).status == "attention"


@pytest.mark.parametrize("channel",["woocommerce","shopify","wix"])
def test_price_readback_uses_recorded_amount_not_current_listing_price(channel,monkeypatch):
    client, headers, wid, item_id, listing_id, op_id = fixture(channel,scope="price")
    calls=[]
    def read(*_args,**kwargs):
        calls.append(kwargs)
        return {"regular_price_cents":2500}
    monkeypatch.setitem(rr.PRICE_READERS,channel,read)
    result=inspect_api(client,headers,op_id)
    assert result.status_code == 200, result.text
    assert result.json()["resolved"]
    assert calls[0]["expected_currency"] == "EUR"
    with db.session_scope() as session:
        assert session.get(models.ChannelListing,listing_id).price_cents == 2000
        op=session.get(MarketplaceOperation,op_id)
        assert op.result["inspection"]["expected_price_cents"] == 2500


def test_content_comparison_keeps_original_text_out_of_reconciliation_evidence(monkeypatch):
    client,headers,wid,item_id,listing_id,op_id=fixture(scope="content")
    monkeypatch.setattr(rr,"read_woocommerce_workspace_content",lambda *_args,**_kwargs:{
        "fields":{"title":"New title","description":"Seller's private description"},
    })
    response=inspect_api(client,headers,op_id)
    assert response.status_code == 200, response.text
    assert response.json()["resolved"] is True
    assert "private description" not in response.text
    with db.session_scope() as session:
        assert "private description" not in str(session.get(MarketplaceOperation,op_id).result)


@pytest.mark.parametrize("channel",["woocommerce","shopify"])
def test_close_reconciliation_does_not_mark_inventory_out_of_stock(channel,monkeypatch):
    client,headers,wid,item_id,listing_id,op_id=fixture(channel,scope="close",quantity=0)
    monkeypatch.setitem(rr.CLOSE_READERS,channel,lambda *_args,**_kwargs:{"status":"draft"})
    response=inspect_api(client,headers,op_id)
    assert response.status_code == 200, response.text
    assert response.json()["resolved"] is True
    with db.session_scope() as session:
        assert session.get(models.InventoryItem,item_id).quantity == 0
        assert session.get(models.ChannelListing,listing_id).status == ListingStatus.ACTIVE


def test_later_attempt_prevents_claiming_causality(monkeypatch):
    client,headers,wid,item_id,listing_id,op_id=fixture(scope="price")
    with db.session_scope() as session:
        session.add(MarketplaceOperation(
            workspace_id=wid,inventory_item_id=item_id,channel_listing_id=listing_id,
            channel="woocommerce",operation_type="update",
            target_key=f"{listing_id}:price",job_payload={"scope":"price","price_cents":2500},
            status="succeeded",verification="remote_verified",
            created_at=datetime.now(timezone.utc),
        ))
    monkeypatch.setitem(rr.PRICE_READERS,"woocommerce",lambda *_args,**_kwargs:{
        "regular_price_cents":2500,
    })
    response=inspect_api(client,headers,op_id)
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "later_attempt"
    assert not response.json()["resolved"]


def test_remote_read_error_keeps_uncertain_operation_and_sanitizes_token(monkeypatch):
    client,headers,wid,item_id,listing_id,op_id=fixture()
    def read(*_args,**_kwargs):
        raise RuntimeError("private-token-123 response contained credentials")
    monkeypatch.setitem(rr.STOCK_READERS,"woocommerce",read)
    result=inspect_api(client,headers,op_id)
    assert result.status_code == 502
    assert "private-token" not in result.text
    with db.session_scope() as session:
        assert session.get(MarketplaceOperation,op_id).status == "attention"


def test_workspace_isolation_blocks_foreign_remote_inspection(monkeypatch):
    client,headers,wid,item_id,listing_id,op_id=fixture()
    other,other_headers,_,_,_,_=fixture()
    def forbidden(*_args,**_kwargs):
        raise AssertionError("Cross-workspace operation must not dispatch a reader")
    monkeypatch.setitem(rr.STOCK_READERS,"woocommerce",forbidden)
    response=inspect_api(other,other_headers,op_id)
    assert response.status_code == 404


def test_cannot_inspect_publish_or_biblio_upload(monkeypatch):
    client,headers,wid,item_id,listing_id,op_id=fixture()
    with db.session_scope() as session:
        op=session.get(MarketplaceOperation,op_id)
        op.operation_type="publish"
        op.target_key=str(item_id)
    response=inspect_api(client,headers,op_id)
    assert response.status_code == 409


def test_stock_intent_is_saved_before_an_uncertain_network_write(monkeypatch):
    client,headers,wid,item_id,listing_id,old_op_id=fixture()
    # The fixture creates an old uncertain history row. Remove it so this
    # real API write can reserve its own operation.
    with db.session_scope() as session:
        session.delete(session.get(MarketplaceOperation,old_op_id))
    seen=[]
    def uncertain(workspace_id, *, external_id, expected_sku, quantity):
        with db.session_scope() as session:
            op = session.execute(select(MarketplaceOperation)).scalar_one()
            assert op.status == "running"
            assert op.job_payload == {
                "scope":"stock","quantity":quantity,
                "external_id":external_id,"sku":expected_sku,
            }
            seen.append(op.id)
        raise RuntimeError("network dropped after request")
    monkeypatch.setattr("app.product_api.update_woocommerce_workspace_stock",uncertain)
    response=client.post(
        f"/api/app/inventory/{item_id}/marketplaces/woocommerce/stock",
        headers=headers,
    )
    assert response.status_code == 502, response.text
    assert len(seen)==1
    with db.session_scope() as session:
        assert session.get(MarketplaceOperation,seen[0]).status == "attention"
        assert session.get(models.InventoryItem,item_id).quantity==2
    monkeypatch.setitem(rr.STOCK_READERS,"woocommerce",lambda *_args,**_kwargs:{
        "quantity":2,"manage_stock":True,"stock_status":"instock",
    })
    inspected=inspect_api(client,headers,seen[0])
    assert inspected.status_code==200, inspected.text
    assert inspected.json()["resolved"] is True
