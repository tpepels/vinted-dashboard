"""Shopify stock updates are SKU-bound, single-location and readback-verified."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.connectors import hosted
from app.constants import ItemCategory, ItemStatus, ListingStatus
from app.product_models import MarketplaceOperation
from app.stock_relations import record_physical_quantity


VARIANT = "gid://shopify/ProductVariant/42"
INVENTORY = "gid://shopify/InventoryItem/52"
LOCATION = "gid://shopify/Location/62"


def stock_node(qty=2, sku="BOOK-42", tracked=True, locations=1):
    return {"productVariant": {
        "id": VARIANT, "sku": sku, "product": {"status": "ACTIVE"},
        "inventoryItem": {
            "id": INVENTORY, "tracked": tracked,
            "inventoryLevels": {"nodes": [
                {"location": {"id": LOCATION},
                 "quantities": [{"name": "available", "quantity": qty}]}
                for _ in range(locations)
            ]},
        },
    }}


def mock_graphql(monkeypatch, *responses):
    calls = []
    iterator = iter(responses)
    monkeypatch.setattr(hosted, "_credentials", lambda *args: {
        "store_domain": "test-shop.myshopify.com", "access_token": "test",
    })
    def graphql(values, query, *, variables=None):
        calls.append((query, variables))
        return next(iterator)
    monkeypatch.setattr(hosted, "_shopify_graphql", graphql)
    return calls


def test_shopify_compare_and_set_matches_exact_location_and_readback(monkeypatch):
    calls = mock_graphql(
        monkeypatch, stock_node(3),
        {"inventorySetQuantities": {"userErrors": [],
                                    "inventoryAdjustmentGroup": {"referenceDocumentUri": "x"}}},
        stock_node(1),
    )
    key = str(uuid.uuid4())
    result = hosted.update_shopify_workspace_stock(
        uuid.uuid4(), external_id=VARIANT, expected_sku="BOOK-42",
        quantity=1, idempotency_key=key,
    )
    assert result["remote_verified"] and not result["already_complete"]
    assert result["quantity"] == 1
    assert len(calls) == 3
    query, variables = calls[1]
    assert "@idempotent" in query
    assert variables["idempotencyKey"] == key
    assert variables["input"]["name"] == "available"
    assert variables["input"]["quantities"] == [{
        "inventoryItemId": INVENTORY, "locationId": LOCATION,
        "quantity": 1, "changeFromQuantity": 3,
    }]


def test_shopify_noop_avoids_a_write(monkeypatch):
    calls = mock_graphql(monkeypatch, stock_node(2))
    result = hosted.update_shopify_workspace_stock(
        uuid.uuid4(), external_id=VARIANT, expected_sku="BOOK-42",
        quantity=2, idempotency_key=str(uuid.uuid4()),
    )
    assert result["already_complete"] and result["remote_verified"]
    assert len(calls) == 1


@pytest.mark.parametrize("remote,match", [
    (stock_node(sku="OTHER"), "SKU differs"),
    (stock_node(tracked=False), "tracking is disabled"),
    (stock_node(locations=0), "exactly one location"),
    (stock_node(locations=2), "exactly one location"),
])
def test_shopify_preflight_rejects_unsafe_stock_without_write(monkeypatch, remote, match):
    calls = mock_graphql(monkeypatch, remote)
    with pytest.raises(ValueError, match=match):
        hosted.update_shopify_workspace_stock(
            uuid.uuid4(), external_id=VARIANT, expected_sku="BOOK-42",
            quantity=0, idempotency_key=str(uuid.uuid4()),
        )
    assert len(calls) == 1


def test_shopify_rejects_unconfirmed_identity_without_network(monkeypatch):
    calls = mock_graphql(monkeypatch)
    with pytest.raises(ValueError, match="ProductVariant"):
        hosted.read_shopify_workspace_stock(
            uuid.uuid4(), external_id="gid://shopify/Product/42", expected_sku="BOOK-42",
        )
    with pytest.raises(ValueError, match="confirmed linked SKU"):
        hosted.read_shopify_workspace_stock(
            uuid.uuid4(), external_id=VARIANT, expected_sku="",
        )
    assert not calls


def test_shopify_mutation_conflict_is_not_verified(monkeypatch):
    calls = mock_graphql(
        monkeypatch, stock_node(3),
        {"inventorySetQuantities": {"userErrors": [
            {"code": "CHANGE_FROM_QUANTITY_STALE", "message": "Concurrent sale"}
        ], "inventoryAdjustmentGroup": None}},
    )
    with pytest.raises(ValueError, match="Concurrent sale"):
        hosted.update_shopify_workspace_stock(
            uuid.uuid4(), external_id=VARIANT, expected_sku="BOOK-42",
            quantity=1, idempotency_key=str(uuid.uuid4()),
        )
    assert len(calls) == 2


def test_shopify_readback_failure_never_claims_success(monkeypatch):
    calls = mock_graphql(
        monkeypatch, stock_node(3),
        {"inventorySetQuantities": {"userErrors": [],
                                    "inventoryAdjustmentGroup": {"referenceDocumentUri": "x"}}},
        stock_node(3),
    )
    with pytest.raises(RuntimeError, match="did not match"):
        hosted.update_shopify_workspace_stock(
            uuid.uuid4(), external_id=VARIANT, expected_sku="BOOK-42",
            quantity=0, idempotency_key=str(uuid.uuid4()),
        )
    assert len(calls) == 3


def _setup_item(client):
    response = client.post("/api/auth/register", json={
        "email": "shopify-stock-test@example.test",
        "password": "a-long-test-password",
        "workspace_name": "Shopify stock check",
    })
    assert response.status_code == 200, response.text
    csrf = response.json()["csrf_token"]
    with db.session_scope() as session:
        workspace_id = session.execute(select(models.Membership)).scalar_one().workspace_id
        item = models.InventoryItem(
            workspace_id=workspace_id, sku="BOOK-42", title="One copy",
            category=ItemCategory.BOOK, status=ItemStatus.ACTIVE,
            quantity=1, attributes={},
        )
        session.add(item)
        session.flush()
        record_physical_quantity(session, item, 1)
        listing = models.ChannelListing(
            workspace_id=workspace_id, inventory_item_id=item.id, channel="shopify",
            external_id=VARIANT, external_sku="BOOK-42", title=item.title,
            status=ListingStatus.ACTIVE, quantity=3, extra={},
        )
        session.add(listing)
        session.flush()
        return csrf, workspace_id, item.id, listing.id


def test_shopify_endpoint_tracks_operation_and_preserves_master_stock(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    csrf, workspace_id, item_id, listing_id = _setup_item(client)
    seen = []
    def update(wid, *, external_id, expected_sku, quantity, idempotency_key):
        seen.append((wid, external_id, expected_sku, quantity, idempotency_key))
        return {
            "remote_verified": True, "quantity": quantity,
            "external_id": external_id, "status": ListingStatus.ACTIVE,
            "already_complete": False,
        }
    monkeypatch.setattr("app.product_api.update_shopify_workspace_stock", update)
    response = client.post(
        f"/api/app/inventory/{item_id}/marketplaces/shopify/stock",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    assert len(seen) == 1
    assert seen[0][:4] == (workspace_id, VARIANT, "BOOK-42", 1)
    assert uuid.UUID(seen[0][4])
    with db.session_scope() as session:
        listing = session.get(models.ChannelListing, listing_id)
        assert listing.quantity == 1
        assert listing.extra["stock_remote_readback_verified"]
        assert session.get(models.InventoryItem, item_id).quantity == 1
        op = session.execute(select(MarketplaceOperation)).scalar_one()
        assert op.status == "succeeded"
        assert op.verification == "remote_verified"
        assert str(op.id) == seen[0][4]


def test_shopify_check_resolves_attention_only_after_matching_remote_read(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    csrf, workspace_id, item_id, listing_id = _setup_item(client)
    with db.session_scope() as session:
        op = MarketplaceOperation(
            workspace_id=workspace_id, channel="shopify", operation_type="update",
            target_key=str(listing_id), inventory_item_id=item_id,
            channel_listing_id=listing_id, status="attention",
            verification="not_checked", active_key=None, attempts=1,
            job_payload={}, result={},
        )
        session.add(op)
        session.flush()
        operation_id = op.id
    monkeypatch.setattr("app.product_api.read_shopify_workspace_stock", lambda *args, **kwargs: {
        "external_id": VARIANT, "quantity": 1, "manage_stock": True,
        "status": ListingStatus.ACTIVE, "stock_status": "instock",
    })
    result = client.post(
        f"/api/app/inventory/{item_id}/marketplaces/shopify/check-stock",
        headers={"X-CSRF-Token": csrf},
    )
    assert result.status_code == 200, result.text
    assert result.json()["matches"] is True
    with db.session_scope() as session:
        op = session.get(MarketplaceOperation, operation_id)
        # Old attempts without a stored original quantity may match today's
        # stock but must not be attributed as a verified historical write.
        assert op.status == "attention"
        assert op.verification == "not_checked"


def test_shopify_discrepancy_is_visible_without_changing_master_quantity(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    csrf, workspace_id, item_id, listing_id = _setup_item(client)
    monkeypatch.setattr("app.product_api.read_shopify_workspace_stock", lambda *args, **kwargs: {
        "external_id": VARIANT, "quantity": 3, "manage_stock": True,
        "status": ListingStatus.ACTIVE, "stock_status": "instock",
    })
    result = client.post(
        f"/api/app/inventory/{item_id}/marketplaces/shopify/check-stock",
        headers={"X-CSRF-Token": csrf},
    )
    assert result.status_code == 200, result.text
    assert result.json()["matches"] is False
    overview = client.get(f"/api/app/inventory/{item_id}/marketplace-status")
    assert overview.status_code == 200, overview.text
    listing = overview.json()["listings"][0]
    assert listing["verification"] == "stock_mismatch"
    assert listing["attention"] is True
    with db.session_scope() as session:
        assert session.get(models.InventoryItem, item_id).quantity == 1
        assert session.get(models.ChannelListing, listing_id).quantity == 3
