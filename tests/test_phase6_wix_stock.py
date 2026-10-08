"""Wix V3 stock writes must be revision checked and tied to one linked variant."""
from types import SimpleNamespace
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.connectors import wix_stock, hosted
from app.constants import ItemCategory, ItemStatus, ListingStatus
from app.product_models import MarketplaceOperation
from app.stock_relations import record_physical_quantity

PRODUCT = "babd2bcc-ea03-4b63-8053-0ec59c73fc36"
VARIANT = "590cef15-c81d-4ed7-970c-1ff879946306"
INVENTORY = "a83212bc-1c1e-4645-b391-a9fed7f930f6"
LOCATION = "d85fbb4d-e415-49b1-98bc-9d22ec338cb1"
EXTERNAL = f"{PRODUCT}:{VARIANT}"


def fixture_remote(monkeypatch, *, before=2, after=1, managed=True,
                   count=1, sku="BOOK-1", preorder=False, stale_revision=False):
    calls = []
    readings = 0
    monkeypatch.setattr(hosted, "_credentials", lambda *args: {
        "api_key": "test-token", "site_id": str(uuid.uuid4()),
    })
    monkeypatch.setattr(hosted, "_wix_query_variants", lambda values: [{
        "variantId": VARIANT, "sku": sku, "productData": {
            "productId": PRODUCT, "visible": True,
        }, "visible": True,
    }])
    monkeypatch.setattr(hosted, "_wix_next_cursor", lambda payload: None)

    def post(values, path, *, body=None):
        nonlocal readings
        assert path == "stores/v3/inventory-items/query"
        assert body["query"]["filter"] == {"variantId": {"$eq": VARIANT}}
        calls.append(("query", body))
        readings += 1
        quantity = before if readings == 1 else after
        revision = "3" if readings == 1 or stale_revision else "4"
        row = {
            "id": INVENTORY, "productId": PRODUCT, "variantId": VARIANT,
            "locationId": LOCATION, "revision": revision,
            "trackQuantity": managed, "quantity": quantity,
            "preorderInfo": {"enabled": preorder},
        }
        return {"inventoryItems": [dict(row) for _ in range(count)]}
    monkeypatch.setattr(hosted, "_wix_post", post)

    def patch(url, *, headers, json, timeout):
        calls.append(("PATCH", url, json))
        assert headers["Authorization"] == "test-token"
        return SimpleNamespace(status_code=200)
    monkeypatch.setattr(wix_stock.requests, "patch", patch)
    return calls


def test_wix_revision_checked_patch_and_remote_readback(monkeypatch):
    calls = fixture_remote(monkeypatch)
    result = wix_stock.update_wix_workspace_stock(
        uuid.uuid4(), external_id=EXTERNAL, expected_sku="BOOK-1", quantity=1,
    )
    assert result["remote_verified"] is True
    assert result["already_complete"] is False
    assert [row[0] for row in calls] == ["query", "PATCH", "query"]
    assert calls[1][1] == f"https://www.wixapis.com/stores/v3/inventory-items/{INVENTORY}"
    assert calls[1][2] == {"inventoryItem": {
        "id": INVENTORY, "revision": "3", "quantity": 1,
    }, "reason": "MANUAL"}


def test_wix_same_stock_is_read_only(monkeypatch):
    calls = fixture_remote(monkeypatch, before=2)
    result = wix_stock.update_wix_workspace_stock(
        uuid.uuid4(), external_id=EXTERNAL, expected_sku="BOOK-1", quantity=2,
    )
    assert result["already_complete"] is True
    assert len(calls) == 1 and calls[0][0] == "query"


@pytest.mark.parametrize("options,match", [
    ({"managed": False}, "tracking is disabled"),
    ({"count": 0}, "exactly one location"),
    ({"count": 2}, "exactly one location"),
    ({"preorder": True}, "preorders are enabled"),
    ({"sku": "OTHER"}, "SKU no longer matches"),
])
def test_wix_refuses_unsafe_writes(monkeypatch, options, match):
    calls = fixture_remote(monkeypatch, **options)
    with pytest.raises(ValueError, match=match):
        wix_stock.update_wix_workspace_stock(
            uuid.uuid4(), external_id=EXTERNAL, expected_sku="BOOK-1", quantity=1,
        )
    assert all(row[0] != "PATCH" for row in calls)


@pytest.mark.parametrize("external,sku", [
    ("../../inventory-items", "BOOK-1"),
    (PRODUCT, "BOOK-1"),
    (f"{PRODUCT}:{PRODUCT}:{VARIANT}", "BOOK-1"),
    (EXTERNAL, ""),
])
def test_wix_rejects_bad_links_before_network(monkeypatch, external, sku):
    monkeypatch.setattr(hosted, "_credentials",
                        lambda *args: pytest.fail("must reject before API call"))
    with pytest.raises(ValueError):
        wix_stock.read_wix_workspace_stock(
            uuid.uuid4(), external_id=external, expected_sku=sku,
        )


def test_wix_failed_readback_does_not_mark_success(monkeypatch):
    calls = fixture_remote(monkeypatch, before=2, after=2)
    with pytest.raises(RuntimeError, match="readback differs"):
        wix_stock.update_wix_workspace_stock(
            uuid.uuid4(), external_id=EXTERNAL, expected_sku="BOOK-1", quantity=1,
        )
    assert len([x for x in calls if x[0] == "PATCH"]) == 1


def test_wix_unchanged_revision_does_not_mark_success(monkeypatch):
    fixture_remote(monkeypatch, stale_revision=True)
    with pytest.raises(RuntimeError, match="readback differs"):
        wix_stock.update_wix_workspace_stock(
            uuid.uuid4(), external_id=EXTERNAL, expected_sku="BOOK-1", quantity=1,
        )


def setup_workspace(client):
    response = client.post("/api/auth/register", json={
        "email": "wix-stock-v3@example.test", "password": "a-long-test-password",
        "workspace_name": "Wix stock",
    })
    assert response.status_code == 200, response.text
    csrf = response.json()["csrf_token"]
    with db.session_scope() as session:
        wid = session.execute(select(models.Membership)).scalar_one().workspace_id
        item = models.InventoryItem(
            workspace_id=wid, sku="BOOK-1", title="One book",
            category=ItemCategory.BOOK, status=ItemStatus.ACTIVE,
            quantity=1, attributes={},
        )
        session.add(item)
        session.flush()
        record_physical_quantity(session, item, 1)
        listing = models.ChannelListing(
            workspace_id=wid, inventory_item_id=item.id, channel="wix",
            external_id=EXTERNAL, external_sku="BOOK-1", title=item.title,
            quantity=2, status=ListingStatus.ACTIVE, extra={},
        )
        session.add(listing)
        session.flush()
        item_id, listing_id = item.id, listing.id
    return csrf, wid, item_id, listing_id


def test_wix_item_endpoints_record_proof_and_do_not_modify_master_stock(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    csrf, wid, item_id, listing_id = setup_workspace(client)
    read_calls = []
    monkeypatch.setattr("app.product_api.read_wix_workspace_stock", lambda *args, **kwargs: (
        read_calls.append("read") or {
            "external_id": EXTERNAL, "quantity": 2,
            "manage_stock": True, "status": ListingStatus.ACTIVE, "stock_status": "instock",
        }
    ))
    check = client.post(
        f"/api/app/inventory/{item_id}/marketplaces/wix/check-stock",
        headers={"X-CSRF-Token": csrf},
    )
    assert check.status_code == 200, check.text
    assert check.json()["matches"] is False
    status = client.get(f"/api/app/inventory/{item_id}/marketplace-status").json()["listings"][0]
    assert status["verification"] == "stock_mismatch"
    assert status["can_sync_wix_stock"] is True

    seen = []
    def update(wid2, *, external_id, expected_sku, quantity):
        seen.append((wid2, external_id, expected_sku, quantity))
        return {"remote_verified": True, "quantity": quantity,
                "external_id": external_id, "status": ListingStatus.ACTIVE,
                "already_complete": False}
    monkeypatch.setattr("app.product_api.update_wix_workspace_stock", update)
    response = client.post(
        f"/api/app/inventory/{item_id}/marketplaces/wix/stock",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    assert seen == [(wid, EXTERNAL, "BOOK-1", 1)]
    with db.session_scope() as session:
        assert session.get(models.InventoryItem, item_id).quantity == 1
        assert session.get(models.ChannelListing, listing_id).quantity == 1
        op = session.execute(select(MarketplaceOperation)).scalar_one()
        assert op.status == "succeeded"
        assert op.verification == "remote_verified"
