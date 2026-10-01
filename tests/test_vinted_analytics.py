from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app import db, models
from app.strategy import DEFAULT_STRATEGY
from app.vinted_analytics import build_vinted_analytics, daily_snapshot_series


NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _workspace(slug: str = "vinted-analytics"):
    with db.session_scope() as session:
        workspace = models.Workspace(name="Analytics", slug=slug, settings={})
        session.add(workspace)
        session.flush()
        return workspace.id


def _item(session, workspace_id, sku, title, category="book"):
    row = models.InventoryItem(
        workspace_id=workspace_id,
        sku=sku,
        title=title,
        category=category,
        quantity=1,
        status="active",
        currency="EUR",
        attributes={},
    )
    session.add(row)
    session.flush()
    return row


def _listing(
    session,
    workspace_id,
    item,
    external_id,
    *,
    age_days,
    status="active",
    price_cents=1500,
):
    row = models.ChannelListing(
        workspace_id=workspace_id,
        inventory_item_id=item.id,
        channel="vinted",
        external_id=external_id,
        title=item.title,
        price_cents=price_cents,
        currency="EUR",
        status=status,
        quantity=1 if status == "active" else 0,
        first_seen_at=NOW - timedelta(days=age_days),
        last_seen_at=NOW,
        extra={"listed_at": (NOW - timedelta(days=age_days)).isoformat()},
    )
    session.add(row)
    session.flush()
    return row


def _snap(session, listing, when, *, views, favourites, price=1500, status=None):
    row = models.ListingSnapshot(
        channel_listing_id=listing.id,
        captured_at=when,
        price_cents=price,
        status=status or listing.status,
        views=views,
        favourites=favourites,
        raw={},
    )
    session.add(row)
    session.flush()
    return row


def test_daily_snapshot_series_keeps_pre_window_baseline_and_last_point_per_day():
    workspace_id = _workspace()
    since = NOW - timedelta(days=10)
    with db.session_scope() as session:
        item = _item(session, workspace_id, "A", "Daily")
        listing = _listing(session, workspace_id, item, "A", age_days=30)

        _snap(
            session,
            listing,
            since - timedelta(days=1, hours=4),
            views=5,
            favourites=0,
        )
        _snap(
            session,
            listing,
            since - timedelta(days=1),
            views=7,
            favourites=1,
        )
        _snap(
            session,
            listing,
            since + timedelta(days=1, hours=2),
            views=10,
            favourites=1,
        )
        _snap(
            session,
            listing,
            since + timedelta(days=1, hours=8),
            views=15,
            favourites=2,
        )
        _snap(
            session,
            listing,
            since + timedelta(days=2, hours=2),
            views=20,
            favourites=3,
        )

        series = daily_snapshot_series(session, [listing.id], since)[listing.id]

    assert len(series) == 3
    assert series[0].views == 7
    assert series[0].captured_at < since
    assert series[1].views == 15
    assert series[2].views == 20


def test_vinted_behavior_segments_rates_price_changes_and_time_to_sale():
    workspace_id = _workspace()
    with db.session_scope() as session:
        momentum_item = _item(session, workspace_id, "M", "Momentum book", "book")
        momentum = _listing(
            session,
            workspace_id,
            momentum_item,
            "M",
            age_days=20,
            price_cents=1300,
        )
        _snap(
            session,
            momentum,
            NOW - timedelta(days=8),
            views=20,
            favourites=1,
            price=1500,
        )
        _snap(
            session,
            momentum,
            NOW - timedelta(days=6),
            views=40,
            favourites=2,
            price=1400,
        )
        _snap(
            session,
            momentum,
            NOW - timedelta(days=2),
            views=70,
            favourites=3,
            price=1400,
        )
        _snap(
            session,
            momentum,
            NOW,
            views=100,
            favourites=5,
            price=1300,
        )

        low_item = _item(session, workspace_id, "L", "Old ignored book", "book")
        low = _listing(session, workspace_id, low_item, "L", age_days=100)
        _snap(
            session,
            low,
            NOW - timedelta(days=8),
            views=8,
            favourites=0,
        )
        _snap(session, low, NOW, views=10, favourites=0)

        high_item = _item(session, workspace_id, "H", "Old liked book", "book")
        high = _listing(session, workspace_id, high_item, "H", age_days=60)
        _snap(
            session,
            high,
            NOW - timedelta(days=8),
            views=90,
            favourites=6,
        )
        _snap(session, high, NOW, views=100, favourites=6)

        sold_item = _item(session, workspace_id, "S", "Sold coat", "clothing")
        sold = _listing(
            session,
            workspace_id,
            sold_item,
            "S",
            age_days=11,
            status="sold",
            price_cents=2000,
        )
        sale = models.Sale(
            workspace_id=workspace_id,
            inventory_item_id=sold_item.id,
            channel="vinted",
            external_order_id="SALE-1",
            direction="sell",
            title=sold_item.title,
            total_cents=2000,
            currency="EUR",
            status="completed",
            lifecycle_status="completed",
            is_closed=True,
            occurred_at=NOW - timedelta(days=6),
            first_seen_at=NOW - timedelta(days=6),
            last_seen_at=NOW - timedelta(days=6),
            extra={},
        )
        session.add(sale)

        for index in range(2):
            session.add(
                models.FavoriteEvent(
                    notification_id=f"fav-{index}",
                    workspace_id=workspace_id,
                    channel_listing_id=momentum.id,
                    item_external_id="M",
                    item_title=momentum.title,
                    actor=f"buyer-{index}",
                    occurred_at=NOW - timedelta(days=index + 1),
                    first_seen_at=NOW - timedelta(days=index + 1),
                )
            )

        # SessionLocal intentionally uses autoflush=False. Flush the fixture
        # rows before querying them through the analytics function.
        session.flush()

        data = build_vinted_analytics(
            session,
            workspace_id,
            days=30,
            strategy=dict(DEFAULT_STRATEGY),
            now=NOW,
        )

    rows = {row["listing_id"]: row for row in data["listings"]}
    assert rows[str(momentum.id)]["segment"] == "momentum"
    assert rows[str(momentum.id)]["views_gain_7d"] == 60
    assert rows[str(momentum.id)]["favourites_gain_7d"] == 3
    assert rows[str(momentum.id)]["favourites_per_100_views"] == 5.0
    assert rows[str(momentum.id)]["views_per_day"] == 5.0
    assert rows[str(momentum.id)]["price_changes_30d"] == 2
    assert rows[str(momentum.id)]["price_delta_30d"] == -200
    assert rows[str(momentum.id)]["favourite_events_window"] == 2

    assert rows[str(low.id)]["segment"] == "low_interest_stale"
    assert rows[str(high.id)]["segment"] == "high_interest_stale"
    assert str(sold.id) not in rows

    segments = {row["segment"]: row["count"] for row in data["segments"]}
    assert segments["momentum"] == 1
    assert segments["low_interest_stale"] == 1
    assert segments["high_interest_stale"] == 1
    assert segments["steady"] == 0

    summary = data["summary"]
    assert summary["active_listings"] == 3
    assert summary["tracked_active_listings"] == 3
    assert summary["favourite_events"] == 2
    assert summary["linked_sales"] == 1
    assert summary["median_days_to_sale"] == 5.0
    assert summary["price_changes_30d"] == 2

    assert len(data["sold_stock"]) == 1
    sold_row = data["sold_stock"][0]
    assert sold_row["title"] == "Sold coat"
    assert sold_row["category"] == "clothing"
    assert sold_row["days_online"] == 5.0
    assert sold_row["listed_at_source"] == "vinted"
    assert sold_row["sold_at_source"] == "order"
    assert sold_row["sale_total_cents"] == 2000
    assert sold_row["external_order_id"] == "SALE-1"

    categories = {row["category"]: row for row in data["categories"]}
    assert categories["book"]["active_listings"] == 3
    assert categories["clothing"]["active_listings"] == 0
    assert categories["clothing"]["linked_sales"] == 1


def test_vinted_behavior_ignores_cancelled_sales_for_time_to_sale():
    workspace_id = _workspace("vinted-cancelled")
    with db.session_scope() as session:
        item = _item(session, workspace_id, "C", "Cancelled sale")
        listing = _listing(
            session,
            workspace_id,
            item,
            "C",
            age_days=20,
            status="sold",
        )
        session.add(
            models.Sale(
                workspace_id=workspace_id,
                inventory_item_id=item.id,
                channel="vinted",
                external_order_id="CANCELLED",
                direction="sell",
                title=item.title,
                total_cents=1000,
                currency="EUR",
                status="cancelled",
                lifecycle_status="cancelled",
                is_closed=True,
                occurred_at=NOW - timedelta(days=5),
                first_seen_at=NOW - timedelta(days=5),
                last_seen_at=NOW - timedelta(days=5),
                extra={},
            )
        )
        data = build_vinted_analytics(
            session,
            workspace_id,
            days=30,
            strategy=dict(DEFAULT_STRATEGY),
            now=NOW,
        )

    assert data["summary"]["linked_sales"] == 0
    assert data["summary"]["median_days_to_sale"] is None


def test_vinted_analytics_endpoint_is_workspace_scoped(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    from fastapi.testclient import TestClient
    from app import entry

    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "vinted-analytics@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Analytics API",
        },
    )
    assert registered.status_code == 200
    response = client.get("/api/app/analytics/vinted?days=30")
    assert response.status_code == 200
    body = response.json()
    assert body["days"] == 30
    assert body["summary"]["active_listings"] == 0
    assert "strategy" in body



def test_existing_analytics_history_uses_downsampled_snapshot_points(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    from fastapi.testclient import TestClient
    from app import entry

    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "history-downsample@example.test",
            "password": "a-long-test-password",
            "workspace_name": "History",
        },
    )
    assert registered.status_code == 200

    with db.session_scope() as session:
        workspace = session.execute(select(models.Workspace)).scalars().one()
        item = _item(session, workspace.id, "HIST", "History listing")
        listing = models.ChannelListing(
            workspace_id=workspace.id,
            inventory_item_id=item.id,
            channel="vinted",
            external_id="HIST",
            title=item.title,
            price_cents=1000,
            currency="EUR",
            status="active",
            quantity=1,
            first_seen_at=datetime.now(timezone.utc) - timedelta(days=20),
            last_seen_at=datetime.now(timezone.utc),
            extra={},
        )
        session.add(listing)
        session.flush()

        today = datetime.now(timezone.utc)
        _snap(
            session,
            listing,
            today - timedelta(days=8),
            views=10,
            favourites=1,
            price=1000,
        )
        _snap(
            session,
            listing,
            today - timedelta(days=1, hours=8),
            views=20,
            favourites=2,
            price=1000,
        )
        _snap(
            session,
            listing,
            today - timedelta(days=1, hours=1),
            views=25,
            favourites=3,
            price=1000,
        )

    response = client.get("/api/app/analytics/history?days=30")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["top_listings"][0]["views"] == 25
    assert body["top_listings"][0]["favourites"] == 3
    assert sum(row["views_gained"] for row in body["daily"]) == 15
    assert sum(row["favourites_gained"] for row in body["daily"]) == 2
