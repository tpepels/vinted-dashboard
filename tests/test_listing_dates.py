from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.strategy import DEFAULT_STRATEGY
from app.vinted_analytics import build_vinted_analytics
from app.workspace_ingest import record_workspace_snapshot


NOW = datetime.now(timezone.utc)


def _workspace(slug: str = "listing-dates") -> uuid.UUID:
    with db.session_scope() as session:
        workspace = models.Workspace(name="Listing dates", slug=slug, settings={})
        session.add(workspace)
        session.flush()
        return workspace.id


def _snapshot(*, listed_at: str | None, collected_at: datetime) -> dict:
    return {
        "collected_at": collected_at.timestamp(),
        "current_user": {"id": "seller-1", "username": "seller"},
        "listings": [
            {
                "id": "V-DATE-1",
                "title": "Old Vinted listing",
                "status": "active",
                "price_cents": 1000,
                "currency": "EUR",
                "listed_at": listed_at,
                "favourites": 2,
                "views": 10,
                "metadata": {},
            }
        ],
        "notifications": [],
        "orders": [],
    }


def test_later_true_vinted_date_repairs_first_seen_age():
    workspace_id = _workspace()
    first_sync = NOW - timedelta(days=5)
    true_listed = NOW - timedelta(days=120)

    record_workspace_snapshot(
        workspace_id,
        _snapshot(listed_at=None, collected_at=first_sync),
    )

    with db.session_scope() as session:
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.external_id == "V-DATE-1",
            )
        ).scalar_one()
        assert listing.extra.get("listed_at") is None
        assert listing.first_seen_at == first_sync

        before = build_vinted_analytics(
            session,
            workspace_id,
            days=30,
            strategy=dict(DEFAULT_STRATEGY),
            now=NOW,
        )
        assert before["listings"][0]["age_days"] is None
        assert before["listings"][0]["listed_at"] is None
        assert before["listings"][0]["listed_at_source"] is None
        assert before["summary"]["actual_age_count"] == 0
        assert before["summary"]["unknown_age_count"] == 1

    record_workspace_snapshot(
        workspace_id,
        _snapshot(listed_at=true_listed.isoformat(), collected_at=NOW),
        extension_version="2.4.0",
    )

    with db.session_scope() as session:
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.external_id == "V-DATE-1",
            )
        ).scalar_one()
        assert listing.extra["listed_at"] == true_listed.isoformat()
        assert listing.first_seen_at == first_sync

        after = build_vinted_analytics(
            session,
            workspace_id,
            days=30,
            strategy=dict(DEFAULT_STRATEGY),
            now=NOW,
        )
        row = after["listings"][0]
        assert row["age_days"] == 120
        assert row["listed_at_source"] == "vinted"
        assert row["listed_at"] == true_listed.isoformat()
        assert after["summary"]["actual_age_count"] == 1


def test_listings_api_does_not_call_first_seen_a_listed_date(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "listing-date-api@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Listing Date API",
        },
    )
    assert registered.status_code == 200, registered.text

    with db.session_scope() as session:
        workspace = session.query(models.Workspace).one()
        item = models.InventoryItem(
            workspace_id=workspace.id,
            sku="DATE-API",
            title="Observed only",
            category="book",
            quantity=1,
            status="active",
            currency="EUR",
            attributes={},
        )
        session.add(item)
        session.flush()
        first_seen = NOW - timedelta(days=40)
        session.add(
            models.ChannelListing(
                workspace_id=workspace.id,
                inventory_item_id=item.id,
                channel="vinted",
                external_id="DATE-API",
                title=item.title,
                price_cents=900,
                currency="EUR",
                status="active",
                quantity=1,
                first_seen_at=first_seen,
                last_seen_at=NOW,
                extra={},
            )
        )

    response = client.get("/api/app/listings")
    assert response.status_code == 200
    row = response.json()["listings"][0]
    assert row["listed_at"] is None
    assert row["listed_at_source"] is None
    assert row["first_seen_at"] == first_seen.isoformat()


def test_listings_api_identifies_true_vinted_listed_date(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "listing-date-real@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Listing Date Real",
        },
    )
    assert registered.status_code == 200, registered.text

    true_listed = NOW - timedelta(days=90)
    with db.session_scope() as session:
        workspace = session.query(models.Workspace).one()
        item = models.InventoryItem(
            workspace_id=workspace.id,
            sku="DATE-REAL",
            title="Actual date",
            category="book",
            quantity=1,
            status="active",
            currency="EUR",
            attributes={},
        )
        session.add(item)
        session.flush()
        session.add(
            models.ChannelListing(
                workspace_id=workspace.id,
                inventory_item_id=item.id,
                channel="vinted",
                external_id="DATE-REAL",
                title=item.title,
                price_cents=1100,
                currency="EUR",
                status="active",
                quantity=1,
                first_seen_at=NOW - timedelta(days=3),
                last_seen_at=NOW,
                extra={"listed_at": true_listed.isoformat()},
            )
        )

    row = client.get("/api/app/listings").json()["listings"][0]
    assert row["listed_at"] == true_listed.isoformat()
    assert row["listed_at_source"] == "vinted"



def test_relative_or_invalid_vinted_dates_are_not_accepted():
    workspace_id = _workspace("listing-date-invalid")
    record_workspace_snapshot(
        workspace_id,
        _snapshot(listed_at="17 hours ago", collected_at=NOW - timedelta(days=20)),
        extension_version="2.4.0",
    )
    with db.session_scope() as session:
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.external_id == "V-DATE-1",
            )
        ).scalar_one()
        assert listing.extra.get("listed_at") is None
        data = build_vinted_analytics(
            session,
            workspace_id,
            days=30,
            strategy=dict(DEFAULT_STRATEGY),
            now=NOW,
        )
        assert data["listings"][0]["listed_at"] is None
        assert data["listings"][0]["age_days"] is None



def test_relative_vinted_age_is_persisted_separately_from_exact_listed_at(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "listing-relative-age@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Relative Age",
        },
    )
    assert registered.status_code == 200, registered.text

    with db.session_scope() as session:
        workspace = session.query(models.Workspace).one()
        workspace_id = workspace.id

    captured = datetime.now(timezone.utc)
    snapshot = _snapshot(listed_at=None, collected_at=captured)
    snapshot["listings"][0]["listed_age_seconds"] = 17 * 86400
    snapshot["listings"][0]["listed_age_source"] = "vinted_page_uploaded"
    snapshot["listings"][0]["listed_age_text"] = "17 days ago"
    record_workspace_snapshot(
        workspace_id,
        snapshot,
        extension_version="2.7.0",
    )

    with db.session_scope() as session:
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.external_id == "V-DATE-1",
            )
        ).scalar_one()
        assert listing.extra.get("listed_at") is None
        assert listing.extra["listed_age_seconds"] == 17 * 86400
        assert listing.extra["listed_age_source"] == "vinted_page_uploaded"
        assert listing.extra["listed_age_text"] == "17 days ago"
        assert listing.extra["listed_age_observed_at"] == captured.isoformat()

    row = client.get("/api/app/listings").json()["listings"][0]
    assert row["listed_at"] is None
    assert row["listed_at_source"] is None
    assert row["listed_age_source"] == "vinted_page_uploaded"
    assert row["listed_age_text"] == "17 days ago"
    assert 17 * 86400 <= row["listed_age_seconds"] < 18 * 86400


def test_exact_vinted_date_stays_distinct_from_relative_age():
    workspace_id = _workspace("listing-date-exact-plus-relative")
    exact = NOW - timedelta(days=40)
    snapshot = _snapshot(listed_at=exact.isoformat(), collected_at=NOW)
    snapshot["listings"][0]["listed_age_seconds"] = 39 * 86400
    snapshot["listings"][0]["listed_age_source"] = "vinted_page_uploaded"
    record_workspace_snapshot(
        workspace_id,
        snapshot,
        extension_version="2.7.0",
    )

    with db.session_scope() as session:
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.external_id == "V-DATE-1",
            )
        ).scalar_one()
        assert listing.extra["listed_at"] == exact.isoformat()
        assert listing.extra["listed_at_source"] == "vinted"
        assert listing.extra["listed_age_seconds"] == 39 * 86400



def test_bridge_27_clears_stale_pre_page_relative_age_when_page_age_is_missing():
    workspace_id = _workspace("listing-date-clear-stale-relative")
    old = _snapshot(listed_at=None, collected_at=NOW - timedelta(minutes=10))
    old["listings"][0]["listed_age_seconds"] = 0
    old["listings"][0]["listed_age_source"] = "vinted_relative"
    old["listings"][0]["listed_age_text"] = "Today"
    record_workspace_snapshot(
        workspace_id,
        old,
        extension_version="2.6.0",
    )

    fresh = _snapshot(listed_at=None, collected_at=NOW)
    record_workspace_snapshot(
        workspace_id,
        fresh,
        extension_version="2.7.0",
    )

    with db.session_scope() as session:
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.external_id == "V-DATE-1",
            )
        ).scalar_one()
        assert listing.extra["listed_age_seconds"] is None
        assert listing.extra["listed_age_source"] is None
        assert listing.extra["listed_age_text"] is None
        assert listing.extra["listed_age_observed_at"] is None


def test_bridge_27_replaces_bad_today_age_with_item_page_uploaded_age():
    workspace_id = _workspace("listing-date-replace-today")
    old = _snapshot(listed_at=None, collected_at=NOW - timedelta(minutes=10))
    old["listings"][0]["listed_age_seconds"] = 0
    old["listings"][0]["listed_age_source"] = "vinted_relative"
    old["listings"][0]["listed_age_text"] = "Today"
    record_workspace_snapshot(
        workspace_id,
        old,
        extension_version="2.6.0",
    )

    fresh = _snapshot(listed_at=None, collected_at=NOW)
    fresh["listings"][0]["listed_age_seconds"] = 5 * 7 * 86400
    fresh["listings"][0]["listed_age_source"] = "vinted_page_uploaded"
    fresh["listings"][0]["listed_age_text"] = "5 weeks ago"
    record_workspace_snapshot(
        workspace_id,
        fresh,
        extension_version="2.7.0",
    )

    with db.session_scope() as session:
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.external_id == "V-DATE-1",
            )
        ).scalar_one()
        assert listing.extra["listed_age_seconds"] == 5 * 7 * 86400
        assert listing.extra["listed_age_source"] == "vinted_page_uploaded"
        assert listing.extra["listed_age_text"] == "5 weeks ago"
