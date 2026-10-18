"""Historical sold orders can be costed even without physical stock links."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.vinted_analytics import _sale_economics


def _user(email):
    client = TestClient(entry.app)
    response = client.post("/api/auth/register", json={
        "email": email, "password": "a-long-test-password",
        "workspace_name": email,
    })
    assert response.status_code == 200, response.text
    return client, {"X-CSRF-Token": response.json()["csrf_token"]}


def _seed():
    client, headers = _user("historic-sale-costs@example.test")
    ids = {}
    with db.session_scope() as session:
        workspace = session.execute(select(models.Workspace)).scalars().one()
        item = models.InventoryItem(
            workspace_id=workspace.id, sku="EXISTING-COST",
            title="Book with linked cost", category="book", quantity=0,
            status="sold", currency="EUR", cost_cents=300, attributes={},
        )
        session.add(item)
        session.flush()
        now = datetime.now(timezone.utc) - timedelta(days=5)
        for key, order_currency, price, order_status, linked in [
            ("linked", "EUR", 1200, "completed", True),
            ("unlinked", "EUR", 1000, "completed", False),
            ("missing-amount", "EUR", None, "completed", False),
            ("usd", "USD", 1500, "completed", False),
            ("cancelled", "EUR", 9000, "cancelled", False),
        ]:
            sale = models.Sale(
                workspace_id=workspace.id, inventory_item_id=item.id if linked else None,
                channel="vinted", external_order_id=key, direction="sell",
                title=key, status=order_status, lifecycle_status=order_status,
                total_cents=price, currency=order_currency, is_closed=True,
                occurred_at=now, extra={"quantity": 1},
            )
            session.add(sale)
            session.flush()
            ids[key] = sale.id
    return client, headers, ids


def test_bulk_historical_cost_preview_preserves_recorded_and_manual_override():
    client, headers, ids = _seed()
    url = "/api/app/sales/average-cost"
    payload = {"cost_cents": 100, "currency": "EUR", "scope": "all"}
    previous = client.get("/api/app/analytics?period=30").json()
    assert previous["sales_period_count"] == 4
    assert previous["sales_period_costed_count"] == 1
    assert previous["sales_period_economics_complete"] is False
    response = client.post(url, headers=headers, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["eligible_sales"] == 2
    assert response.json()["already_costed"] == 1
    assert response.json()["other_currency"] == 1
    assert response.json()["with_sale_amount"] == 1
    rows = {x["external_order_id"]: x for x in client.get("/api/app/sales").json()["sales"]}
    assert rows["unlinked"]["acquisition_cost_cents"] is None

    applied = client.post(url, headers=headers, json={**payload, "preview": False})
    assert applied.status_code == 200, applied.text
    assert applied.json()["updated"] == 2
    rows = {x["external_order_id"]: x for x in client.get("/api/app/sales").json()["sales"]}
    assert rows["linked"]["acquisition_cost_cents"] == 300
    assert rows["unlinked"]["acquisition_cost_cents"] == 100
    assert rows["unlinked"]["acquisition_cost_source"] == "estimated_bulk"
    assert rows["missing-amount"]["acquisition_cost_cents"] == 100
    assert rows["usd"]["acquisition_cost_cents"] is None
    assert rows["cancelled"]["acquisition_cost_cents"] is None
    analytics = client.get("/api/app/analytics?period=30").json()
    assert analytics["sales_period_costed_count"] == 2
    assert analytics["sales_period_estimated_count"] == 1
    assert analytics["sales_period_gross_profit_cents"] == 1800
    assert not analytics["sales_period_economics_complete"]  # no sale price for one order

    preview_again = client.post(url, headers=headers, json=payload).json()
    assert preview_again["eligible_sales"] == 0
    assert preview_again["already_costed"] == 3

    manual = client.patch(
        f"/api/app/sales/{ids['unlinked']}/acquisition-cost",
        headers=headers, json={"cost_cents": 200},
    )
    assert manual.status_code == 200, manual.text
    assert manual.json()["cost_cents"] == 200
    assert client.get("/api/app/analytics?period=30").json()["sales_period_gross_profit_cents"] == 1700
    cleared = client.patch(
        f"/api/app/sales/{ids['unlinked']}/acquisition-cost",
        headers=headers, json={"cost_cents": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["cost_cents"] is None

    inventory_override = client.patch(
        f"/api/app/sales/{ids['linked']}/acquisition-cost",
        headers=headers, json={"cost_cents": 50},
    )
    assert inventory_override.status_code == 200
    assert inventory_override.json()["cost_cents"] == 50
    clear_inventory_override = client.patch(
        f"/api/app/sales/{ids['linked']}/acquisition-cost",
        headers=headers, json={"cost_cents": None},
    )
    assert clear_inventory_override.status_code == 200
    assert clear_inventory_override.json()["cost_cents"] == 300

    with db.session_scope() as session:
        sale = session.get(models.Sale, ids["linked"])
        assert len(sale.extra["acquisition_cost_history"]) == 2
        assert _sale_economics(sale, session.get(models.InventoryItem, sale.inventory_item_id))["cost_cents"] == 300


def test_historical_cost_authorization_scope_and_overwrite():
    client, headers, ids = _seed()
    outsider, outsider_headers = _user("outsider-sale-costs@example.test")
    target = f"/api/app/sales/{ids['unlinked']}/acquisition-cost"
    assert outsider.patch(target, headers=outsider_headers, json={"cost_cents": 100}).status_code == 404
    assert client.patch(target, headers=headers, json={"cost_cents": -1}).status_code == 422
    assert client.patch(
        f"/api/app/sales/{ids['cancelled']}/acquisition-cost",
        headers=headers, json={"cost_cents": 100},
    ).status_code == 404
    assert client.post(
        "/api/app/sales/average-cost", headers=headers,
        json={"cost_cents": 100, "scope": "invalid"},
    ).status_code == 400
    assert client.post(
        "/api/app/sales/average-cost", headers=headers,
        json={"cost_cents": -100},
    ).status_code == 422
    assert outsider.post(
        "/api/app/sales/average-cost", headers=outsider_headers,
        json={"cost_cents": 100, "preview": False},
    ).json()["updated"] == 0

    replacement = client.post("/api/app/sales/average-cost", headers=headers, json={
        "cost_cents": 120, "scope": "30", "currency": "EUR",
        "overwrite_existing": True, "preview": False,
    })
    assert replacement.status_code == 200, replacement.text
    assert replacement.json()["updated"] == 3
    sales = {r["external_order_id"]:r for r in client.get("/api/app/sales").json()["sales"]}
    assert sales["linked"]["acquisition_cost_cents"] == 120
    assert sales["unlinked"]["acquisition_cost_cents"] == 120
    assert sales["usd"]["acquisition_cost_cents"] is None
