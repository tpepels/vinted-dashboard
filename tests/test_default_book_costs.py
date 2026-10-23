"""No default until the owner configures one; explicit overrides always win."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models


def test_default_book_cost_follows_items_and_sales_without_per_sale_editing():
    client = TestClient(entry.app)
    response = client.post("/api/auth/register", json={
        "email": "default-book-cost@example.test",
        "password": "a-long-test-password",
        "workspace_name": "Book cost defaults",
    })
    assert response.status_code == 200, response.text
    headers = {"X-CSRF-Token": response.json()["csrf_token"]}
    now = datetime.now(timezone.utc)
    with db.session_scope() as session:
        workspace = session.execute(select(models.Workspace)).scalars().one()
        default_book = models.InventoryItem(
            workspace_id=workspace.id, sku="BOOK-DEFAULT", title="Default book",
            category="book", quantity=1, status="active", currency="EUR",
            cost_cents=None, attributes={},
        )
        exception_book = models.InventoryItem(
            workspace_id=workspace.id, sku="BOOK-EXCEPTION", title="Exception book",
            category="book", quantity=1, status="active", currency="EUR",
            cost_cents=625, attributes={"cost_source": "manual_item"},
        )
        non_book = models.InventoryItem(
            workspace_id=workspace.id, sku="GENERAL", title="Nonbook",
            category="general", quantity=1, status="active", currency="EUR",
            cost_cents=None, attributes={},
        )
        session.add_all([default_book, exception_book, non_book])
        session.flush()
        default_id = default_book.id
        exception_id = exception_book.id
        sales = [
            ("unlinked", None, "vinted", "My single book"),
            ("bundle", None, "vinted", "Bundle 3 items"),
            ("linked", default_book.id, "vinted", "Default book"),
            ("exception", exception_book.id, "vinted", "Exception book"),
            ("other", None, "ebay", "General item"),
        ]
        for external_id, item_id, channel, title in sales:
            session.add(models.Sale(
                workspace_id=workspace.id, inventory_item_id=item_id,
                channel=channel, external_order_id=external_id, direction="sell",
                title=title, total_cents=2000, currency="EUR", status="completed",
                lifecycle_status="completed", is_closed=True, occurred_at=now,
                first_seen_at=now, last_seen_at=now, extra={},
            ))

    def current_sales():
        reply = client.get("/api/app/sales")
        assert reply.status_code == 200, reply.text
        return {row["external_order_id"]: row for row in reply.json()["sales"]}

    initial = current_sales()
    assert initial["unlinked"]["acquisition_cost_cents"] is None
    assert initial["unlinked"]["acquisition_cost_source"] is None
    assert initial["bundle"]["acquisition_cost_cents"] is None
    assert initial["linked"]["acquisition_cost_cents"] is None
    assert initial["exception"]["acquisition_cost_cents"] == 625
    assert initial["other"]["acquisition_cost_cents"] is None

    inventory = client.get("/api/app/inventory").json()["items"]
    items = {item["sku"]: item for item in inventory}
    assert items["BOOK-DEFAULT"]["cost_cents"] is None
    assert items["BOOK-DEFAULT"]["recorded_cost_cents"] is None
    assert items["BOOK-DEFAULT"]["cost_source"] is None
    assert items["BOOK-EXCEPTION"]["cost_cents"] == 625
    assert items["GENERAL"]["cost_cents"] is None

    changed = client.patch("/api/app/settings", headers=headers, json={
        "default_book_cost_cents": 250,
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()["workspace"]["settings"]["default_book_cost_cents"] == 250
    assert client.get("/api/app/settings").json()["workspace"]["settings"]["default_book_cost_cents"] == 250
    after = current_sales()
    assert after["unlinked"]["acquisition_cost_cents"] == 250
    assert after["bundle"]["acquisition_cost_cents"] == 750
    assert after["linked"]["acquisition_cost_cents"] == 250
    assert after["exception"]["acquisition_cost_cents"] == 625

    # A book-level exception is independent of posting/reposting and sales.
    override = client.patch("/api/app/inventory/" + str(default_id),
        headers=headers, json={"cost_cents": 435})
    assert override.status_code == 200, override.text
    assert override.json()["item"]["cost_cents"] == 435
    assert current_sales()["linked"]["acquisition_cost_cents"] == 435
    assert current_sales()["unlinked"]["acquisition_cost_cents"] == 250

    # Sale-specific exceptions override both inventory and default amounts.
    sale_id = after["bundle"]["id"]
    override = client.patch(f"/api/app/sales/{sale_id}/acquisition-cost",
        headers=headers, json={"cost_cents": 99})
    assert override.status_code == 200, override.text
    assert current_sales()["bundle"]["acquisition_cost_cents"] == 99
    reset = client.patch(f"/api/app/sales/{sale_id}/acquisition-cost",
        headers=headers, json={"cost_cents": None})
    assert reset.status_code == 200, reset.text
    assert current_sales()["bundle"]["acquisition_cost_cents"] == 750

    assert client.patch("/api/app/settings", headers=headers,
        json={"default_book_cost_cents": -1}).status_code == 422

    # Explicitly clearing the workspace setting turns off default estimates.
    cleared_default = client.patch("/api/app/settings", headers=headers,
        json={"default_book_cost_cents": None})
    assert cleared_default.status_code == 200
    assert "default_book_cost_cents" not in cleared_default.json()["workspace"]["settings"]
    assert current_sales()["unlinked"]["acquisition_cost_cents"] is None
    assert current_sales()["linked"]["acquisition_cost_cents"] == 435
    assert current_sales()["exception"]["acquisition_cost_cents"] == 625


def test_unknown_vinted_bundle_quantity_is_explicitly_labeled_as_estimated():
    client = TestClient(entry.app)
    response = client.post("/api/auth/register", json={
        "email": "default-unknown-quantity@example.test",
        "password": "a-long-test-password",
        "workspace_name": "Unknown quantity",
    })
    assert response.status_code == 200
    headers = {"X-CSRF-Token": response.json()["csrf_token"]}
    with db.session_scope() as session:
        workspace = session.execute(select(models.Workspace)).scalars().one()
        row = models.Sale(
            workspace_id=workspace.id, channel="vinted",
            external_order_id="unknown-bundle", direction="sell",
            title="Bundle of mystery books", total_cents=2000,
            currency="EUR", status="completed", lifecycle_status="completed",
            is_closed=True, occurred_at=datetime.now(timezone.utc), extra={},
        )
        session.add(row)
        session.flush()
        sale_id = row.id
    rows = client.get("/api/app/sales").json()["sales"]
    assert rows[0]["acquisition_cost_cents"] is None
    configured = client.patch("/api/app/settings", headers=headers,
        json={"default_book_cost_cents": 100})
    assert configured.status_code == 200
    rows = client.get("/api/app/sales").json()["sales"]
    assert rows[0]["acquisition_cost_cents"] == 100
    assert rows[0]["acquisition_cost_source"] == "default_book_assumed_quantity"
    response = client.patch(f"/api/app/sales/{sale_id}/book-quantity",
        headers=headers, json={"quantity": 4})
    assert response.status_code == 200
    assert response.json()["cost_cents"] == 400
    assert response.json()["cost_source"] == "default_book"
