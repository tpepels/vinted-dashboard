from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import db, entry, models
from app.product_models import BackgroundJob


NOW = datetime.now(timezone.utc)


def _register(client: TestClient):
    response = client.post(
        "/api/auth/register",
        json={
            "email": "today-queue@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Today Queue",
        },
    )
    assert response.status_code == 200, response.text
    with db.session_scope() as session:
        return session.query(models.Workspace).one().id


def _item(session, workspace_id, sku, title, *, status="active"):
    item = models.InventoryItem(
        workspace_id=workspace_id,
        sku=sku,
        title=title,
        category="book",
        quantity=1 if status == "active" else 0,
        status=status,
        currency="EUR",
        attributes={},
    )
    session.add(item)
    session.flush()
    return item


def test_today_unifies_operational_work_types(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    workspace_id = _register(client)

    with db.session_scope() as session:
        stale = _item(session, workspace_id, "STALE", "Stale listing")
        session.add(
            models.ChannelListing(
                workspace_id=workspace_id,
                inventory_item_id=stale.id,
                channel="vinted",
                external_id="STALE",
                title=stale.title,
                price_cents=1000,
                currency="EUR",
                status="active",
                quantity=1,
                first_seen_at=NOW - timedelta(days=120),
                last_seen_at=NOW,
                extra={"listed_at": (NOW - timedelta(days=120)).isoformat()},
            )
        )
        session.add(
            models.Sale(
                workspace_id=workspace_id,
                inventory_item_id=None,
                channel="vinted",
                external_order_id="BUY-1",
                direction="buy",
                title=stale.title,
                total_cents=500,
                currency="EUR",
                status="completed",
                lifecycle_status="completed",
                is_closed=True,
                occurred_at=NOW - timedelta(days=20),
                first_seen_at=NOW - timedelta(days=20),
                last_seen_at=NOW - timedelta(days=20),
                extra={},
            )
        )

        duplicate_one = _item(session, workspace_id, "DUP-1", "Duplicate sale", status="sold")
        duplicate_two = _item(session, workspace_id, "DUP-2", "Duplicate sale", status="sold")
        session.add(
            models.Sale(
                workspace_id=workspace_id,
                inventory_item_id=None,
                channel="vinted",
                external_order_id="AMB-SALE",
                direction="sell",
                title="Duplicate sale",
                total_cents=900,
                currency="EUR",
                status="completed",
                lifecycle_status="completed",
                is_closed=True,
                occurred_at=NOW - timedelta(days=5),
                first_seen_at=NOW - timedelta(days=5),
                last_seen_at=NOW - timedelta(days=5),
                extra={},
            )
        )

        open_item = _item(session, workspace_id, "OPEN", "Open sold order", status="sold")
        session.add(
            models.Sale(
                workspace_id=workspace_id,
                inventory_item_id=open_item.id,
                channel="vinted",
                external_order_id="OPEN-SALE",
                direction="sell",
                title=open_item.title,
                total_cents=1200,
                currency="EUR",
                status="shipped",
                lifecycle_status="shipped",
                is_closed=False,
                occurred_at=NOW - timedelta(days=1),
                first_seen_at=NOW - timedelta(days=1),
                last_seen_at=NOW - timedelta(days=1),
                extra={},
            )
        )

        session.add(
            BackgroundJob(
                workspace_id=workspace_id,
                job_type="ebay_sync",
                payload={},
                status="error",
                available_at=NOW,
                completed_at=NOW,
                last_error="OAuth token expired",
                created_at=NOW,
            )
        )
        session.flush()

    response = client.get("/api/app/today")
    assert response.status_code == 200, response.text
    body = response.json()
    kinds = {row["kind"] for row in body["work_queue"]}

    assert "listing" in kinds
    assert "purchase_cost" in kinds
    assert "reconcile" in kinds
    assert "open_sales" in kinds
    assert "connector_error" in kinds
    assert body["purchase_cost_suggestion_count"] == 1
    assert body["unlinked_sell_count"] == 1
    assert body["open_sell_order_count"] == 1
    assert body["work_queue_count"] >= 5

    titles = [row["title"] for row in body["work_queue"]]
    assert any("ambiguous sold order" in title for title in titles)
    assert any("purchase cost match" in title for title in titles)
    assert any("Ebay" in title and "attention" in title for title in titles)


def test_today_ignores_old_connector_error_after_later_success(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    workspace_id = _register(client)

    with db.session_scope() as session:
        session.add(
            BackgroundJob(
                workspace_id=workspace_id,
                job_type="biblio_sync",
                payload={},
                status="error",
                available_at=NOW - timedelta(hours=2),
                completed_at=NOW - timedelta(hours=2),
                last_error="Old failure",
                created_at=NOW - timedelta(hours=2),
            )
        )
        session.add(
            BackgroundJob(
                workspace_id=workspace_id,
                job_type="biblio_sync",
                payload={},
                status="success",
                available_at=NOW - timedelta(hours=1),
                completed_at=NOW - timedelta(hours=1),
                created_at=NOW - timedelta(hours=1),
            )
        )

    response = client.get("/api/app/today")
    assert response.status_code == 200
    assert all(row["kind"] != "connector_error" for row in response.json()["work_queue"])
