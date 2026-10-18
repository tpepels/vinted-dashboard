"""Period filters must agree across revenue cards, chart history and Vinted sales."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models


def _client_with_orders():
    client = TestClient(entry.app)
    response = client.post("/api/auth/register", json={
        "email": "analytics-periods@example.test",
        "password": "a-long-test-password",
        "workspace_name": "Analytics periods",
    })
    assert response.status_code == 200, response.text
    now = datetime.now(timezone.utc)
    previous_december = datetime(now.year - 1, 12, 15, tzinfo=timezone.utc)
    # Use previous December only if still within the last 365 days.
    # For Jan-Feb dates, this holds; for late December it may not.
    with db.session_scope() as session:
        workspace = session.execute(select(models.Workspace)).scalars().one()
        item = models.InventoryItem(
            workspace_id=workspace.id, sku="A-PERIOD", title="Period test book",
            category="book", quantity=1, status="active", currency="EUR",
            cost_cents=300, attributes={},
        )
        session.add(item)
        session.flush()
        listing = models.ChannelListing(
            workspace_id=workspace.id, inventory_item_id=item.id,
            channel="vinted", external_id="period-test", title=item.title,
            price_cents=1000, currency="EUR", status="active", quantity=1,
            first_seen_at=now - timedelta(days=500), last_seen_at=now,
            extra={"listed_at": (now - timedelta(days=500)).isoformat()},
        )
        session.add(listing)
        session.flush()

        def sale(order, age, cents, status="completed", occurred_at=None):
            session.add(models.Sale(
                workspace_id=workspace.id, inventory_item_id=item.id,
                channel="vinted", external_order_id=order, direction="sell",
                title=item.title, status=status, lifecycle_status=status,
                total_cents=cents, currency="EUR", is_closed=True,
                occurred_at=occurred_at or now - timedelta(days=age),
            ))

        sale("recent", 10, 1000)
        sale("missing-price", 8, None)
        sale("last-90", 45, 2000)
        sale("last-365", 120, 3000)
        sale("previous-year", 0, 4000, occurred_at=previous_december)
        sale("too-old", 400, 5000)
        sale("cancelled", 5, 9000, status="cancelled")
    return client, previous_december, now


def test_analytics_period_boundaries_and_profit_match_history():
    client, december, now = _client_with_orders()
    for period, expected in (
        ("30", (2, 1000, 1, 700)),
        ("90", (3, 3000, 2, 2400)),
        ("ytd", (4, 6000, 3, 5100)),
    ):
        response = client.get("/api/app/analytics", params={"period": period})
        assert response.status_code == 200, response.text
        data = response.json()
        count, revenue, costed, profit = expected
        assert data["sales_period_count"] == count
        assert data["sales_period_revenue_known_count"] == count - 1
        assert data["sales_period_totals"] == [{"currency": "EUR", "cents": revenue}]
        assert data["sales_period_costed_count"] == costed
        assert data["sales_period_gross_profit_cents"] == profit
        assert data["sales_period_cost_cents"] == costed * 300
        if period == "ytd":
            assert data["period_start"].startswith(str(now.year) + "-01-01")
        chart = client.get("/api/app/analytics/history", params={"period": period})
        assert chart.status_code == 200, chart.text
        daily = chart.json()["daily"]
        assert sum(day["revenue_cents"] for day in daily) == revenue
        assert sum(day["sales"] for day in daily) == count
        vinted = client.get("/api/app/analytics/vinted", params={"period": period})
        assert vinted.status_code == 200, vinted.text
        assert vinted.json()["summary"]["linked_sales"] == count

    lifetime = client.get("/api/app/analytics").json()
    assert lifetime["sales_all_time_count"] == 6
    assert lifetime["sales_all_time_totals"] == [{"currency": "EUR", "cents": 15000}]
    # The Sales page must continue to show lifetime numbers by default.
    assert lifetime["sales_ytd_count"] == 4


def test_analytics_rejects_unknown_period():
    client, _, _ = _client_with_orders()
    for endpoint in (
        "/api/app/analytics",
        "/api/app/analytics/history",
        "/api/app/analytics/vinted",
    ):
        response = client.get(endpoint, params={"period": "invalid"})
        assert response.status_code == 422
