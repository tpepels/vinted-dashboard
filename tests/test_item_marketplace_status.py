"""Phase 3 item-specific marketplace overview: scoped, truthful and read-only."""
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.constants import ItemCategory, ItemStatus, ListingStatus
from app.product_models import MarketplaceOperation


def _register(email):
    client = TestClient(entry.app)
    r = client.post("/api/auth/register", json={
        "email": email, "password": "a-long-test-password",
        "workspace_name": email,
    })
    assert r.status_code == 200, r.text
    return client


def test_item_marketplace_view_lists_scoped_links_accurately_without_writes(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    owner = _register("phase3-item-owner@example.test")
    other = _register("phase3-other-owner@example.test")
    with db.session_scope() as session:
        user = session.execute(
            select(models.User).where(models.User.email == "phase3-item-owner@example.test")
        ).scalar_one()
        workspace = session.execute(
            select(models.Membership).where(models.Membership.user_id == user.id)
        ).scalar_one().workspace_id
        item = models.InventoryItem(
            workspace_id=workspace, sku="BOOK-42", title="Test book",
            category=ItemCategory.BOOK, status=ItemStatus.ACTIVE,
            quantity=1, attributes={"isbn": "9780140328721"},
        )
        session.add(item)
        session.flush()
        biblio = models.ChannelListing(
            workspace_id=workspace, inventory_item_id=item.id,
            channel="biblio", external_id="BOOK-42", external_sku="BOOK-42",
            title="Test book", status=ListingStatus.ACTIVE, quantity=1,
            extra={"remote_verified": True, "remote_matches_local": False,
                   "remote_verified_at": "2026-10-07T17:00:00+00:00",
                   "remote_mismatch_fields": ["price_cents"],
                   "photo_sync_state": "error", "photo_sync_error": "image missing",
                   "image_urls": ["https://example.com/one.jpg"]},
        )
        vinted = models.ChannelListing(
            workspace_id=workspace, inventory_item_id=item.id,
            channel="vinted", external_id="V-42",
            title="Test book", status=ListingStatus.ACTIVE,
            quantity=1, extra={},
        )
        session.add_all([biblio, vinted])
        session.flush()
        op = MarketplaceOperation(
            workspace_id=workspace, channel="biblio", operation_type="photos",
            target_key=str(biblio.id), inventory_item_id=item.id,
            channel_listing_id=biblio.id, status="attention",
            verification="manual_required", attempts=1,
            last_error="Photo transfer failed",
            job_payload={}, result={},
        )
        session.add(op)
        session.flush()
        item_id, op_id = item.id, op.id

    endpoint = f"/api/app/inventory/{item_id}/marketplace-status"
    missing = other.get(endpoint)
    assert missing.status_code == 404
    result = owner.get(endpoint)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["item"]["id"] == str(item_id)
    assert body["item"]["quantity"] == 1
    assert len(body["listings"]) == 2
    biblio_row = next(row for row in body["listings"] if row["channel"] == "biblio")
    assert biblio_row["verification"] == "differs"
    assert biblio_row["attention"] is True
    assert biblio_row["photo_count"] == 1
    assert biblio_row["photo_error"] == "image missing"
    assert biblio_row["last_operation"]["id"] == str(op_id)
    vinted_row = next(row for row in body["listings"] if row["channel"] == "vinted")
    assert vinted_row["verification"] == "not_checked"
    assert vinted_row["attention"] is False
    assert len(body["operations"]) == 1

    with db.session_scope() as session:
        assert session.get(models.InventoryItem, item_id).quantity == 1
        assert session.get(MarketplaceOperation, op_id).status == "attention"


def test_unlinked_and_unknown_inventory_ids_are_not_disclosed(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = _register("phase3-empty@example.test")
    r = client.get(f"/api/app/inventory/{uuid.uuid4()}/marketplace-status")
    assert r.status_code == 404
