"""Cross-market field provenance and check-first WooCommerce content endpoints."""
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.constants import ItemCategory, ItemStatus, ListingStatus
from app.product_models import MarketplaceOperation
from app.stock_relations import record_physical_quantity
from app.listing_content import fingerprint


def setup():
    client = TestClient(entry.app)
    response = client.post("/api/auth/register", json={
        "email": "content-editor@example.test", "password": "a-long-test-password",
        "workspace_name": "Content editing",
    })
    assert response.status_code == 200, response.text
    csrf = response.json()["csrf_token"]
    with db.session_scope() as session:
        wid = session.execute(select(models.Membership)).scalar_one().workspace_id
        item = models.InventoryItem(
            workspace_id=wid, sku="COPY-10", title="Master title",
            category=ItemCategory.BOOK, status=ItemStatus.ACTIVE,
            quantity=1, currency="EUR", condition="Good",
            attributes={"description": "Master description",
                        "isbn": "9781111111111", "author": "Writer"},
        )
        session.add(item)
        session.flush()
        record_physical_quantity(session, item, 1)
        woo = models.ChannelListing(
            workspace_id=wid, inventory_item_id=item.id,
            channel="woocommerce", external_id="10", external_sku="COPY-10",
            title="Old title", status=ListingStatus.ACTIVE,
            quantity=1, price_cents=950, extra={"description": "Old description"},
        )
        vinted = models.ChannelListing(
            workspace_id=wid, inventory_item_id=item.id,
            channel="vinted", external_id="vinted-11",
            title="Old Vinted snapshot", status=ListingStatus.ACTIVE,
            quantity=1, price_cents=800, extra={"author": "Writer"},
        )
        session.add_all([woo, vinted])
        session.flush()
        item_id, listing_id = item.id, woo.id
    return client, csrf, wid, item_id, listing_id


def remote(title="Old title", description="Old description"):
    fields = {
        "title": title, "description": description, "condition": "Good",
        "isbn": "9781111111111", "author": "Writer",
        "publisher": None, "edition": None, "language": None,
    }
    return {
        "external_id": "10", "sku": "COPY-10", "path": "products/10",
        "variation": False, "fields": fields,
        "writable_fields": ["title", "description"],
        "revision": "2026-10-09T09:00:00",
        "fingerprint": fingerprint({
            "external_id": "10", "sku": "COPY-10", "path": "products/10",
            "variation": False, "fields": fields,
            "revision": "2026-10-09T09:00:00",
        }),
    }


def test_comparison_provenance_live_check_and_selected_update(monkeypatch):
    client, csrf, wid, item_id, listing_id = setup()
    headers = {"X-CSRF-Token": csrf}
    initial = client.get(f"/api/app/inventory/{item_id}/content-comparison")
    assert initial.status_code == 200, initial.text
    entries = initial.json()["listings"]
    woo = next(x for x in entries if x["channel"] == "woocommerce")
    vinted = next(x for x in entries if x["channel"] == "vinted")
    assert woo["source"] == "imported_snapshot"
    assert woo["can_check_live"]
    assert vinted["source"] == "imported_snapshot"
    assert not vinted["can_check_live"]
    assert all(not field["writable"] for field in vinted["fields"])
    assert next(field for field in woo["fields"] if field["key"] == "description")["status"] == "differs"
    calls = []
    monkeypatch.setattr("app.product_api.read_woocommerce_workspace_content",
                        lambda *args, **kw: remote())
    def update(workspace, **kwargs):
        calls.append((workspace, kwargs))
        return {"remote_verified": True, "already_complete": False}
    monkeypatch.setattr("app.product_api.update_woocommerce_workspace_content", update)
    no_check = client.post(f"/api/app/inventory/{item_id}/marketplaces/woocommerce/content",
                           headers=headers, json={"fields": ["title"]})
    assert no_check.status_code == 409
    checked = client.post(f"/api/app/inventory/{item_id}/marketplaces/woocommerce/check-content",
                          headers=headers)
    assert checked.status_code == 200, checked.text
    comparison = client.get(f"/api/app/inventory/{item_id}/content-comparison").json()
    woo = next(x for x in comparison["listings"] if x["channel"] == "woocommerce")
    assert woo["source"] == "previous_live_check"
    editable = [x["key"] for x in woo["fields"] if x["writable"]]
    assert editable == ["title", "description"]
    bad = client.post(f"/api/app/inventory/{item_id}/marketplaces/woocommerce/content",
                      headers=headers, json={"fields": ["isbn"]})
    assert bad.status_code == 422
    result = client.post(f"/api/app/inventory/{item_id}/marketplaces/woocommerce/content",
                         headers=headers, json={"fields": ["title"]})
    assert result.status_code == 200, result.text
    assert result.json()["updated_fields"] == ["title"]
    assert calls == [(wid, {
        "external_id": "10", "expected_sku": "COPY-10",
        "expected_fingerprint": remote()["fingerprint"],
        "changes": {"title": "Master title"},
    })]
    with db.session_scope() as session:
        listing = session.get(models.ChannelListing, listing_id)
        item = session.get(models.InventoryItem, item_id)
        assert listing.title == "Master title"
        assert listing.quantity == item.quantity == 1
        assert listing.price_cents == 950
        assert listing.extra["description"] == "Old description"
        assert "content_check" not in listing.extra
        op = session.execute(select(MarketplaceOperation)).scalar_one()
        assert op.status == "succeeded"
        assert op.target_key == f"{listing_id}:content"


def test_item_change_invalidates_remote_check_and_prevents_write(monkeypatch):
    client, csrf, wid, item_id, listing_id = setup()
    headers = {"X-CSRF-Token": csrf}
    monkeypatch.setattr("app.product_api.read_woocommerce_workspace_content",
                        lambda *args, **kw: remote())
    assert client.post(f"/api/app/inventory/{item_id}/marketplaces/woocommerce/check-content",
                       headers=headers).status_code == 200
    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        item.title = "New master title"
    result = client.post(f"/api/app/inventory/{item_id}/marketplaces/woocommerce/content",
                         headers=headers, json={"fields": ["title"]})
    assert result.status_code == 409
    with db.session_scope() as session:
        assert not session.execute(select(MarketplaceOperation)).scalars().all()


def test_uncertain_content_write_requires_specific_live_check(monkeypatch):
    client, csrf, wid, item_id, listing_id = setup()
    headers = {"X-CSRF-Token": csrf}
    monkeypatch.setattr("app.product_api.read_woocommerce_workspace_content",
                        lambda *args, **kw: remote())
    assert client.post(f"/api/app/inventory/{item_id}/marketplaces/woocommerce/check-content",
                       headers=headers).status_code == 200
    def uncertain(*args, **kwargs):
        raise RuntimeError("remote result unknown")
    monkeypatch.setattr("app.product_api.update_woocommerce_workspace_content", uncertain)
    response = client.post(f"/api/app/inventory/{item_id}/marketplaces/woocommerce/content",
                           headers=headers, json={"fields": ["title"]})
    assert response.status_code == 502
    with db.session_scope() as session:
        op = session.execute(select(MarketplaceOperation)).scalar_one()
        assert op.status == "attention"
        assert op.job_payload["changes"] == {"title": "Master title"}
    assert client.post(f"/api/app/inventory/{item_id}/marketplaces/woocommerce/content",
                       headers=headers, json={"fields": ["title"]}).status_code == 409
    monkeypatch.setattr("app.product_api.read_woocommerce_workspace_content",
                        lambda *args, **kw: remote(title="Master title"))
    checked = client.post(f"/api/app/inventory/{item_id}/marketplaces/woocommerce/check-content",
                          headers=headers)
    assert checked.status_code == 200
    with db.session_scope() as session:
        op = session.execute(select(MarketplaceOperation)).scalar_one()
        assert op.status == "succeeded"
        assert op.verification == "remote_verified"


def test_other_workspace_cannot_view_or_write_content(monkeypatch):
    client, csrf, wid, item_id, listing_id = setup()
    stranger = TestClient(entry.app)
    response = stranger.post("/api/auth/register", json={
        "email": "other-content-editor@example.test",
        "password": "a-long-test-password", "workspace_name": "Other",
    })
    assert response.status_code == 200
    other = {"X-CSRF-Token": response.json()["csrf_token"]}
    assert stranger.get(f"/api/app/inventory/{item_id}/content-comparison").status_code == 404
    assert stranger.post(f"/api/app/inventory/{item_id}/marketplaces/woocommerce/check-content",
                         headers=other).status_code == 404
    assert stranger.post(f"/api/app/inventory/{item_id}/marketplaces/woocommerce/content",
                         headers=other,json={"fields": ["title"]}).status_code == 404
