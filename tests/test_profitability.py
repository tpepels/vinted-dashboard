from __future__ import annotations

from datetime import datetime, timezone
import uuid

from fastapi.testclient import TestClient

from app import db, entry, models


NOW = datetime.now(timezone.utc)


def _register(client: TestClient, email: str) -> tuple[str, uuid.UUID]:
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "a-long-test-password",
            "workspace_name": "Profitability",
        },
    )
    assert response.status_code == 200, response.text
    csrf = response.json()["csrf_token"]
    with db.session_scope() as session:
        workspace = session.query(models.Workspace).one()
        return csrf, workspace.id


def _item(
    session,
    workspace_id: uuid.UUID,
    *,
    sku: str,
    title: str,
    cost_cents: int | None,
) -> models.InventoryItem:
    item = models.InventoryItem(
        workspace_id=workspace_id,
        sku=sku,
        title=title,
        category="book",
        quantity=0,
        status="sold",
        cost_cents=cost_cents,
        currency="EUR",
        attributes={},
    )
    session.add(item)
    session.flush()
    return item


def _sale(
    session,
    workspace_id: uuid.UUID,
    *,
    order_id: str,
    item: models.InventoryItem | None,
    total_cents: int | None,
    status: str = "completed",
    lifecycle: str = "completed",
) -> models.Sale:
    row = models.Sale(
        workspace_id=workspace_id,
        inventory_item_id=item.id if item else None,
        channel="vinted",
        external_order_id=order_id,
        direction="sell",
        title=item.title if item else order_id,
        total_cents=total_cents,
        currency="EUR",
        status=status,
        lifecycle_status=lifecycle,
        is_closed=True,
        occurred_at=NOW,
        first_seen_at=NOW,
        last_seen_at=NOW,
        extra={},
    )
    session.add(row)
    session.flush()
    return row


def test_analytics_profitability_excludes_cancelled_and_incomplete_revenue(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    _csrf, workspace_id = _register(client, "profitability@example.test")

    with db.session_scope() as session:
        profitable = _item(
            session,
            workspace_id,
            sku="PROFIT",
            title="Profitable",
            cost_cents=600,
        )
        _sale(
            session,
            workspace_id,
            order_id="PROFIT-SALE",
            item=profitable,
            total_cents=1000,
        )

        uncosted = _item(
            session,
            workspace_id,
            sku="NO-COST",
            title="No cost",
            cost_cents=None,
        )
        _sale(
            session,
            workspace_id,
            order_id="NO-COST-SALE",
            item=uncosted,
            total_cents=900,
        )

        no_revenue = _item(
            session,
            workspace_id,
            sku="NO-REV",
            title="No revenue",
            cost_cents=400,
        )
        _sale(
            session,
            workspace_id,
            order_id="NO-REV-SALE",
            item=no_revenue,
            total_cents=None,
        )

        cancelled = _item(
            session,
            workspace_id,
            sku="CANCELLED",
            title="Cancelled",
            cost_cents=100,
        )
        _sale(
            session,
            workspace_id,
            order_id="CANCELLED-SALE",
            item=cancelled,
            total_cents=5000,
            status="cancelled",
            lifecycle="cancelled",
        )

    response = client.get("/api/app/analytics")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["sales_ytd_count"] == 3
    assert body["sales_ytd_revenue_known_count"] == 2
    assert body["sales_ytd_cents"] == 1900
    assert body["sales_ytd_costed_count"] == 1
    assert body["sales_ytd_cost_coverage_pct"] == 50.0
    assert body["sales_ytd_cost_cents"] == 600
    assert body["sales_ytd_costed_revenue_cents"] == 1000
    assert body["sales_ytd_gross_profit_cents"] == 400
    assert body["sales_ytd_gross_margin_pct"] == 40.0
    assert body["sales_ytd_roi_pct"] == 66.7
