"""Check-first Shopify price API, operation isolation and race tests."""
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.constants import ItemCategory, ItemStatus, ListingStatus
from app.product_models import MarketplaceOperation
from app.stock_relations import record_physical_quantity

VARIANT = "gid://shopify/ProductVariant/42"


def setup():
    client = TestClient(entry.app)
    response = client.post("/api/auth/register", json={
        "email": "shopify-pricing@example.test",
        "password": "a-long-test-password",
        "workspace_name": "Shopify pricing",
    })
    assert response.status_code == 200, response.text
    csrf = response.json()["csrf_token"]
    with db.session_scope() as session:
        wid = session.execute(select(models.Membership)).scalar_one().workspace_id
        item = models.InventoryItem(
            workspace_id=wid, sku="SKU-42", title="A paperback",
            category=ItemCategory.BOOK, status=ItemStatus.ACTIVE,
            quantity=1, currency="EUR",
            attributes={"default_price_cents": 1125},
        )
        session.add(item)
        session.flush()
        record_physical_quantity(session, item, 1)
        listing = models.ChannelListing(
            workspace_id=wid, inventory_item_id=item.id, channel="shopify",
            external_id=VARIANT, external_sku="SKU-42",
            title=item.title, price_cents=850, currency="EUR",
            quantity=1, status=ListingStatus.ACTIVE, extra={},
        )
        session.add(listing)
        session.flush()
        item_id, listing_id = item.id, listing.id
    return client, csrf, wid, item_id, listing_id


def check(client, item_id, csrf):
    return client.post(
        f"/api/app/inventory/{item_id}/marketplaces/shopify/check-price",
        headers={"X-CSRF-Token": csrf},
    )


def update(client, item_id, csrf):
    return client.post(
        f"/api/app/inventory/{item_id}/marketplaces/shopify/price",
        headers={"X-CSRF-Token": csrf},
    )


def test_price_check_mismatch_then_explicit_verified_update(monkeypatch):
    client, csrf, wid, item_id, listing_id = setup()
    calls = []
    monkeypatch.setattr("app.product_api.read_shopify_workspace_price",
                        lambda *a, **k: {"regular_price_cents": 850})
    def write(workspace, **kwargs):
        calls.append((workspace, kwargs))
        return {"remote_verified": True, "already_complete": False,
                "price_cents": 1125, "currency": "EUR",
                "external_id": VARIANT}
    monkeypatch.setattr("app.product_api.update_shopify_workspace_price", write)

    status = client.get(f"/api/app/inventory/{item_id}/marketplace-status").json()
    assert status["listings"][0]["can_sync_shopify_price"]
    assert status["listings"][0]["price_verification"] == "not_checked"
    assert update(client, item_id, csrf).status_code == 409
    observed = check(client, item_id, csrf)
    assert observed.status_code == 200, observed.text
    assert observed.json()["remote_price_cents"] == 850
    assert not observed.json()["matches"]
    status = client.get(f"/api/app/inventory/{item_id}/marketplace-status").json()
    assert status["listings"][0]["price_verification"] == "price_mismatch"

    written = update(client, item_id, csrf)
    assert written.status_code == 200, written.text
    assert len(calls) == 1
    assert calls[0] == (wid, {
        "external_id": VARIANT, "expected_sku": "SKU-42",
        "expected_currency": "EUR", "old_price_cents": 850,
        "new_price_cents": 1125,
    })
    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        listing = session.get(models.ChannelListing, listing_id)
        assert item.quantity == 1
        assert item.attributes["default_price_cents"] == 1125
        assert listing.quantity == 1
        assert listing.price_cents == 1125
        op = session.execute(select(MarketplaceOperation)).scalar_one()
        assert op.target_key == f"{listing_id}:price"
        assert op.status == "succeeded"
        assert op.verification == "remote_verified"


def test_uncertain_update_cannot_be_cleared_by_stock_check(monkeypatch):
    client, csrf, wid, item_id, listing_id = setup()
    monkeypatch.setattr("app.product_api.read_shopify_workspace_price",
                        lambda *a, **k: {"regular_price_cents": 850})
    assert check(client, item_id, csrf).status_code == 200
    def uncertain(*args, **kwargs):
        raise RuntimeError("unknown remote result")
    monkeypatch.setattr("app.product_api.update_shopify_workspace_price", uncertain)
    assert update(client, item_id, csrf).status_code == 502

    with db.session_scope() as session:
        op = session.execute(select(MarketplaceOperation)).scalar_one()
        assert op.status == "attention"
        assert op.job_payload["price_cents"] == 1125

    monkeypatch.setattr("app.product_api.read_shopify_workspace_stock",
                        lambda *a, **k: {
                            "quantity": 1, "manage_stock": True,
                            "stock_status": "instock", "status": ListingStatus.ACTIVE,
                        })
    stock = client.post(
        f"/api/app/inventory/{item_id}/marketplaces/shopify/check-stock",
        headers={"X-CSRF-Token": csrf},
    )
    assert stock.status_code == 200, stock.text
    with db.session_scope() as session:
        assert session.execute(select(MarketplaceOperation)).scalar_one().status == "attention"

    monkeypatch.setattr("app.product_api.read_shopify_workspace_price",
                        lambda *a, **k: {"regular_price_cents": 1125})
    resolved = check(client, item_id, csrf)
    assert resolved.status_code == 200
    with db.session_scope() as session:
        op = session.execute(select(MarketplaceOperation)).scalar_one()
        assert op.status == "succeeded" and op.verification == "remote_verified"


def test_checked_price_cannot_be_reused_after_remote_link_change(monkeypatch):
    client, csrf, wid, item_id, listing_id = setup()
    monkeypatch.setattr("app.product_api.read_shopify_workspace_price",
                        lambda *a, **k: {"regular_price_cents": 850})
    assert check(client, item_id, csrf).status_code == 200
    with db.session_scope() as session:
        listing = session.get(models.ChannelListing, listing_id)
        listing.external_id = "gid://shopify/ProductVariant/99"
    assert update(client, item_id, csrf).status_code == 409
    with db.session_scope() as session:
        assert not session.execute(select(MarketplaceOperation)).scalars().all()


def test_new_price_check_refuses_unconfirmed_stock(monkeypatch):
    client, csrf, wid, item_id, listing_id = setup()
    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        attrs = dict(item.attributes or {})
        attrs["stock_authority"] = "provisional"
        item.attributes = attrs
    assert check(client, item_id, csrf).status_code == 409


def test_cannot_check_price_in_other_workspace():
    client, csrf, wid, item_id, listing_id = setup()
    stranger = TestClient(entry.app)
    response = stranger.post("/api/auth/register", json={
        "email": "stranger-price@example.test",
        "password": "a-long-test-password",
        "workspace_name": "Different",
    })
    assert response.status_code == 200
    other_csrf = response.json()["csrf_token"]
    assert check(stranger, item_id, other_csrf).status_code == 404
    assert update(stranger, item_id, other_csrf).status_code == 404
