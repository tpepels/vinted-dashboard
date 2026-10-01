from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.import_export import apply_inventory_import, parse_table, preview_inventory_import, suggest_mapping


def _register(client: TestClient, email: str) -> str:
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "a-long-test-password",
            "workspace_name": email,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def test_import_default_category_applies_when_file_has_no_category_signals():
    with db.session_scope() as session:
        workspace = models.Workspace(name="Import", slug="phase4-import", settings={})
        session.add(workspace)
        session.flush()
        workspace_id = workspace.id

    table = parse_table("stock.csv", b"SKU,Title,Quantity\nB-1,Plain row,1\n")
    mapping = suggest_mapping(table.headers)

    with db.session_scope() as session:
        preview = preview_inventory_import(
            session,
            workspace_id,
            table.rows,
            mapping,
            default_category="book",
        )
        assert preview["can_apply"] is True
        assert preview["preview"][0]["payload"]["category"] == "book"
        apply_inventory_import(
            session,
            workspace_id,
            table.rows,
            mapping,
            default_category="book",
        )

    with db.session_scope() as session:
        item = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == workspace_id,
                models.InventoryItem.sku == "B-1",
            )
        ).scalar_one()
        assert item.category == "book"


def test_fresh_onboarding_requires_explicit_category_choice(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    csrf = _register(client, "onboarding@example.test")

    first = client.get("/api/app/onboarding")
    assert first.status_code == 200
    body = first.json()
    assert body["completed"] is False
    assert body["primary_category"] == "general"
    assert body["steps"]["choose_category"] is False
    assert body["steps"]["stock_loaded"] is False

    updated = client.put(
        "/api/app/onboarding",
        headers=_headers(csrf),
        json={"primary_category": "book"},
    )
    assert updated.status_code == 200, updated.text
    body = client.get("/api/app/onboarding").json()
    assert body["primary_category"] == "book"
    assert body["steps"]["choose_category"] is True
    assert body["completed"] is False

    finished = client.put(
        "/api/app/onboarding",
        headers=_headers(csrf),
        json={"completed": True},
    )
    assert finished.status_code == 200
    assert client.get("/api/app/onboarding").json()["completed"] is True


def test_bulk_inventory_edit_updates_only_selected_workspace_items(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    first = TestClient(entry.app)
    second = TestClient(entry.app)
    first_csrf = _register(first, "bulk-first@example.test")
    second_csrf = _register(second, "bulk-second@example.test")

    def create(client, csrf, sku, title):
        response = client.post(
            "/api/app/inventory",
            headers=_headers(csrf),
            json={
                "sku": sku,
                "title": title,
                "category": "general",
                "quantity": 1,
                "currency": "EUR",
            },
        )
        assert response.status_code == 200, response.text
        return response.json()["item"]

    a = create(first, first_csrf, "A", "First")
    b = create(first, first_csrf, "B", "Second")
    foreign = create(second, second_csrf, "X", "Foreign")

    response = first.post(
        "/api/app/inventory/bulk",
        headers=_headers(first_csrf),
        json={
            "item_ids": [a["id"], b["id"]],
            "category": "book",
            "location": "Shelf 3",
            "cost_cents": 400,
            "default_price_cents": 1000,
            "currency": "eur",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["updated"] == 2

    items = {row["sku"]: row for row in first.get("/api/app/inventory").json()["items"]}
    for sku in ("A", "B"):
        assert items[sku]["category"] == "book"
        assert items[sku]["location"] == "Shelf 3"
        assert items[sku]["cost_cents"] == 400
        assert items[sku]["default_price_cents"] == 1000
        assert items[sku]["potential_margin_cents"] == 600
        assert items[sku]["currency"] == "EUR"

    rejected = first.post(
        "/api/app/inventory/bulk",
        headers=_headers(first_csrf),
        json={
            "item_ids": [a["id"], foreign["id"]],
            "location": "Should not write",
        },
    )
    assert rejected.status_code == 404
    unchanged = {row["sku"]: row for row in first.get("/api/app/inventory").json()["items"]}
    assert unchanged["A"]["location"] == "Shelf 3"


def test_bulk_inventory_rejects_null_nonnullable_fields(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    csrf = _register(client, "bulk-validation@example.test")
    item = client.post(
        "/api/app/inventory",
        headers=_headers(csrf),
        json={"sku": "ONE", "title": "One", "category": "general", "quantity": 1},
    ).json()["item"]

    for field in ("category", "status", "currency"):
        response = client.post(
            "/api/app/inventory/bulk",
            headers=_headers(csrf),
            json={"item_ids": [item["id"]], field: None},
        )
        assert response.status_code == 400, (field, response.text)


def test_analytics_reports_costed_sales_gross_profit(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    csrf = _register(client, "profit@example.test")
    created = client.post(
        "/api/app/inventory",
        headers=_headers(csrf),
        json={
            "sku": "PROFIT-1",
            "title": "Costed item",
            "category": "general",
            "quantity": 1,
            "cost_cents": 500,
            "currency": "EUR",
            "attributes": {"default_price_cents": 1500},
        },
    )
    assert created.status_code == 200, created.text
    item_id = uuid.UUID(created.json()["item"]["id"])

    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        sale = models.Sale(
            workspace_id=item.workspace_id,
            inventory_item_id=item.id,
            channel="vinted",
            external_order_id="PROFIT-SALE",
            direction="sell",
            title=item.title,
            total_cents=1200,
            currency="EUR",
            status="completed",
            lifecycle_status="completed",
            is_closed=True,
            occurred_at=datetime.now(timezone.utc),
            first_seen_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
            extra={},
        )
        session.add(sale)

    analytics = client.get("/api/app/analytics")
    assert analytics.status_code == 200
    body = analytics.json()
    assert body["sales_ytd_costed_count"] == 1
    assert body["sales_ytd_cost_cents"] == 500
    assert body["sales_ytd_costed_revenue_cents"] == 1200
    assert body["sales_ytd_gross_profit_cents"] == 700



def test_analytics_uses_active_marketplace_price_without_manual_default(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    csrf = _register(client, "market-ask@example.test")
    created = client.post(
        "/api/app/inventory",
        headers=_headers(csrf),
        json={
            "sku": "ASK-1",
            "title": "Marketplace-priced item",
            "category": "general",
            "quantity": 1,
            "currency": "EUR",
        },
    )
    assert created.status_code == 200, created.text
    item_id = uuid.UUID(created.json()["item"]["id"])

    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        account = models.ChannelAccount(
            workspace_id=item.workspace_id,
            channel="vinted",
            display_name="Vinted",
            status="connected",
            config={},
        )
        session.add(account)
        session.flush()
        session.add(
            models.ChannelListing(
                workspace_id=item.workspace_id,
                inventory_item_id=item.id,
                channel_account_id=account.id,
                channel="vinted",
                external_id="ASK-1",
                title=item.title,
                price_cents=1750,
                currency="EUR",
                status="active",
                quantity=1,
                first_seen_at=datetime.now(timezone.utc),
                last_seen_at=datetime.now(timezone.utc),
                extra={},
            )
        )

    inventory = client.get("/api/app/inventory")
    assert inventory.status_code == 200
    row = inventory.json()["items"][0]
    assert row["default_price_cents"] is None
    assert row["effective_ask_cents"] == 1750
    assert row["effective_ask_source"] == "marketplace"
    assert row["potential_margin_cents"] is None

    analytics = client.get("/api/app/analytics")
    assert analytics.status_code == 200
    body = analytics.json()
    assert body["priced_inventory_count"] == 1
    assert body["market_priced_inventory_count"] == 1
    assert body["manual_priced_inventory_count"] == 0
    assert body["inventory_ask_cents"] == 1750
    assert body["costed_inventory_count"] == 0
    assert body["margin_inventory_count"] == 0
    assert body["inventory_cost_cents"] == 0
    assert body["inventory_potential_margin_cents"] == 0


def test_today_uses_vinted_behavior_thresholds_for_stale_listing(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    csrf = _register(client, "today-action@example.test")
    created = client.post(
        "/api/app/inventory",
        headers=_headers(csrf),
        json={
            "sku": "STALE-1",
            "title": "Very stale listing",
            "category": "general",
            "quantity": 1,
            "currency": "EUR",
        },
    )
    assert created.status_code == 200
    item_id = uuid.UUID(created.json()["item"]["id"])
    now = datetime.now(timezone.utc)

    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        account = models.ChannelAccount(
            workspace_id=item.workspace_id,
            channel="vinted",
            display_name="Vinted",
            status="connected",
            config={},
        )
        session.add(account)
        session.flush()
        session.add(
            models.ChannelListing(
                workspace_id=item.workspace_id,
                inventory_item_id=item.id,
                channel_account_id=account.id,
                channel="vinted",
                external_id="STALE-1",
                title=item.title,
                price_cents=900,
                currency="EUR",
                status="active",
                quantity=1,
                first_seen_at=now - timedelta(days=100),
                last_seen_at=now,
                extra={"listed_at": (now - timedelta(days=100)).isoformat()},
            )
        )

    response = client.get("/api/app/today")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["actions"]
    assert body["actions"][0]["title"] == "Very stale listing"
    assert body["actions"][0]["action"] == "Refresh listing"
    assert body["actions"][0]["age_days"] >= 99
