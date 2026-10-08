"""Phase 3 item-level marketplace view: relationships, proof, and isolation."""
from datetime import datetime, timezone
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.constants import ItemCategory, ItemStatus, ListingStatus
from app.product_models import MarketplaceOperation


def register(email: str):
    client = TestClient(entry.app)
    response = client.post("/api/auth/register", json={
        "email": email, "password": "long-enough-password",
        "workspace_name": email,
    })
    assert response.status_code == 200, response.text
    with db.session_scope() as session:
        user = session.execute(select(models.User).where(models.User.email == email)).scalar_one()
        workspace_id = session.execute(
            select(models.Membership.workspace_id).where(models.Membership.user_id == user.id)
        ).scalar_one()
    return client, workspace_id


def test_item_marketplace_status_is_workspace_scoped_read_only(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *a, **k: None)
    client, workspace = register("phase3-owner@example.test")
    other, unrelated_workspace = register("phase3-stranger@example.test")
    with db.session_scope() as session:
        item = models.InventoryItem(
            workspace_id=workspace, sku="ONE-COPY", title="One real book",
            category=ItemCategory.BOOK, quantity=1, status=ItemStatus.ACTIVE,
            attributes={},
        )
        session.add(item)
        session.flush()
        listing = models.ChannelListing(
            workspace_id=workspace, inventory_item_id=item.id, channel="biblio",
            external_id="ONE-COPY", external_sku="ONE-COPY", title="One real book",
            quantity=1, status=ListingStatus.ACTIVE,
            extra={
                "remote_verified":True, "remote_matches_local":False,
                "remote_verified_at":"2026-10-07T11:00:00Z",
                "photo_sync_state":"error","photo_sync_error":"one missing cover",
                "image_urls":["https://example.com/book.jpg"],
            }
        )
        session.add(listing)
        session.flush()
        operation = MarketplaceOperation(
            workspace_id=workspace, channel="biblio", operation_type="photos",
            target_key=str(listing.id), channel_listing_id=listing.id,
            inventory_item_id=item.id, job_type="biblio_sync",
            job_payload={}, status="attention", verification="manual_required",
            attempts=1, result={"photos_missing_or_failed":1},
            last_error="photo failed",
        )
        session.add(operation)
        session.flush()
        item_id, listing_id = item.id, listing.id
    before = {
        "listings": 1,
        "operations": 1,
    }
    response = client.get(f"/api/app/inventory/{item_id}/marketplace-status")
    assert response.status_code == 200, response.text
    info = response.json()
    assert info["item"]["title"] == "One real book"
    assert info["item"]["quantity"] == 1
    assert len(info["listings"]) == before["listings"]
    status = info["listings"][0]
    assert status["listing_id"] == str(listing_id)
    assert status["verification"] == "differs"
    assert status["photo_error"] == "one missing cover"
    assert status["attention"] is True
    assert status["last_operation"]["status"] == "attention"
    assert status["can_inspect_photos"] is True
    assert len(info["operations"]) == before["operations"]
    assert "cannot" in info["notes"]["close"]
    assert other.get(f"/api/app/inventory/{item_id}/marketplace-status").status_code == 404
    assert client.get(f"/api/app/inventory/{uuid.uuid4()}/marketplace-status").status_code == 404
    with db.session_scope() as session:
        assert session.get(models.InventoryItem, item_id).quantity == 1
        assert session.get(models.ChannelListing, listing_id).extra["photo_sync_error"] == "one missing cover"


def test_marketplace_view_displays_unverified_non_biblio_without_invented_controls(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *a, **k: None)
    client, workspace = register("phase3-etsy@example.test")
    with db.session_scope() as session:
        item = models.InventoryItem(
            workspace_id=workspace, sku="PHYSICAL-2", title="Another book",
            category=ItemCategory.BOOK, quantity=2, status=ItemStatus.ACTIVE,
            attributes={},
        )
        session.add(item)
        session.flush()
        session.add(models.ChannelListing(
            workspace_id=workspace, inventory_item_id=item.id, channel="etsy",
            external_id="ET-2", title="Another book", quantity=2,
            status=ListingStatus.ACTIVE, extra={},
        ))
        item_id = item.id
    data = client.get(f"/api/app/inventory/{item_id}/marketplace-status").json()
    assert data["listings"][0]["verification"] == "not_checked"
    assert data["listings"][0]["can_inspect_photos"] is False
    assert data["listings"][0]["last_operation"] is None
    assert data["operations"] == []
