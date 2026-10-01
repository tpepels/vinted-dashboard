from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.purchase_costs import apply_purchase_cost, purchase_cost_suggestions


NOW = datetime.now(timezone.utc)


def _workspace(slug: str = "purchase-costs") -> uuid.UUID:
    with db.session_scope() as session:
        workspace = models.Workspace(name="Purchase costs", slug=slug, settings={})
        session.add(workspace)
        session.flush()
        return workspace.id


def _item(
    session,
    workspace_id: uuid.UUID,
    *,
    sku: str,
    title: str,
    cost_cents: int | None = None,
):
    item = models.InventoryItem(
        workspace_id=workspace_id,
        sku=sku,
        title=title,
        category="book",
        quantity=1,
        status="active",
        cost_cents=cost_cents,
        currency="EUR",
        attributes={},
    )
    session.add(item)
    session.flush()
    return item


def _purchase(
    session,
    workspace_id: uuid.UUID,
    *,
    order_id: str,
    title: str,
    total_cents: int = 1000,
    item_id: uuid.UUID | None = None,
):
    row = models.Sale(
        workspace_id=workspace_id,
        inventory_item_id=item_id,
        channel="vinted",
        external_order_id=order_id,
        direction="buy",
        title=title,
        total_cents=total_cents,
        currency="EUR",
        status="completed",
        lifecycle_status="completed",
        is_closed=True,
        occurred_at=NOW,
        first_seen_at=NOW,
        last_seen_at=NOW,
        extra={},
    )
    session.add(row)
    session.flush()
    return row


def test_unique_exact_title_purchase_is_suggested():
    workspace_id = _workspace()
    with db.session_scope() as session:
        item = _item(session, workspace_id, sku="BOOK-1", title="Stoner")
        purchase = _purchase(
            session,
            workspace_id,
            order_id="BUY-1",
            title="  stoner ",
            total_cents=750,
        )

        result = purchase_cost_suggestions(session, workspace_id)

        assert result["count"] == 1
        assert result["ambiguous_count"] == 0
        assert result["unmatched_count"] == 0
        row = result["suggestions"][0]
        assert row["purchase"]["id"] == str(purchase.id)
        assert row["item"]["id"] == str(item.id)
        assert row["match_reason"] == "Unique exact title"
        assert row["purchase"]["total_cents"] == 750


def test_duplicate_stock_or_duplicate_purchases_stay_ambiguous():
    workspace_id = _workspace()
    with db.session_scope() as session:
        _item(session, workspace_id, sku="COPY-1", title="Same book")
        _item(session, workspace_id, sku="COPY-2", title="Same book")
        _purchase(session, workspace_id, order_id="BUY-1", title="Same book")

        _item(session, workspace_id, sku="ONE", title="Other book")
        _purchase(session, workspace_id, order_id="BUY-2", title="Other book")
        _purchase(session, workspace_id, order_id="BUY-3", title="Other book")

        result = purchase_cost_suggestions(session, workspace_id)

        assert result["count"] == 0
        assert result["ambiguous_count"] == 3
        assert result["unmatched_count"] == 0


def test_explicit_linked_purchase_is_suggested_even_without_title_match():
    workspace_id = _workspace()
    with db.session_scope() as session:
        item = _item(session, workspace_id, sku="LINKED", title="Inventory title")
        purchase = _purchase(
            session,
            workspace_id,
            order_id="BUY-LINKED",
            title="Different marketplace title",
            item_id=item.id,
        )

        result = purchase_cost_suggestions(session, workspace_id)

        assert result["count"] == 1
        row = result["suggestions"][0]
        assert row["purchase"]["id"] == str(purchase.id)
        assert row["item"]["id"] == str(item.id)
        assert row["match_reason"] == "Already linked purchase"


def test_apply_purchase_cost_links_purchase_and_records_provenance():
    workspace_id = _workspace()
    with db.session_scope() as session:
        item = _item(session, workspace_id, sku="BOOK-2", title="The Waves")
        purchase = _purchase(
            session,
            workspace_id,
            order_id="BUY-2",
            title="The Waves",
            total_cents=1200,
        )

        result = apply_purchase_cost(
            session,
            workspace_id,
            purchase_id=purchase.id,
            inventory_item_id=item.id,
            cost_cents=950,
        )

        assert result["cost_cents"] == 950
        assert result["adjusted"] is True
        assert purchase.inventory_item_id == item.id
        assert item.cost_cents == 950
        assert item.attributes["cost_source"] == "vinted_purchase"
        assert item.attributes["cost_source_order_id"] == "BUY-2"
        assert item.attributes["cost_source_order_amount_cents"] == 1200
        assert item.attributes["cost_source_applied_cents"] == 950
        assert item.attributes["cost_source_adjusted"] is True
        assert item.attributes["cost_recorded_at"]


def test_apply_purchase_cost_rejects_existing_cost_or_stale_ambiguous_pair():
    workspace_id = _workspace()
    with db.session_scope() as session:
        costed = _item(
            session,
            workspace_id,
            sku="COSTED",
            title="Costed",
            cost_cents=500,
        )
        costed_purchase = _purchase(
            session,
            workspace_id,
            order_id="BUY-COSTED",
            title="Costed",
            item_id=costed.id,
        )
        with pytest.raises(ValueError, match="already has"):
            apply_purchase_cost(
                session,
                workspace_id,
                purchase_id=costed_purchase.id,
                inventory_item_id=costed.id,
                cost_cents=500,
            )

        first = _item(session, workspace_id, sku="AMB-1", title="Ambiguous")
        _item(session, workspace_id, sku="AMB-2", title="Ambiguous")
        purchase = _purchase(
            session,
            workspace_id,
            order_id="BUY-AMB",
            title="Ambiguous",
        )
        with pytest.raises(ValueError, match="no longer an unambiguous match"):
            apply_purchase_cost(
                session,
                workspace_id,
                purchase_id=purchase.id,
                inventory_item_id=first.id,
                cost_cents=800,
            )


def test_purchase_cost_api_applies_cost(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "purchase-cost@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Purchase Cost API",
        },
    )
    assert registered.status_code == 200
    csrf = registered.json()["csrf_token"]

    created = client.post(
        "/api/app/inventory",
        headers={"X-CSRF-Token": csrf},
        json={
            "sku": "API-BOOK",
            "title": "API Book",
            "category": "book",
            "quantity": 1,
            "currency": "EUR",
        },
    )
    assert created.status_code == 200
    item_id = uuid.UUID(created.json()["item"]["id"])

    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        purchase = _purchase(
            session,
            item.workspace_id,
            order_id="BUY-API",
            title="API Book",
            total_cents=1100,
        )
        purchase_id = purchase.id

    suggestions = client.get("/api/app/purchase-cost-suggestions")
    assert suggestions.status_code == 200
    assert suggestions.json()["count"] == 1

    response = client.post(
        f"/api/app/purchases/{purchase_id}/apply-cost",
        headers={"X-CSRF-Token": csrf},
        json={
            "inventory_item_id": str(item_id),
            "cost_cents": 900,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["cost_cents"] == 900

    inventory = client.get("/api/app/inventory")
    row = inventory.json()["items"][0]
    assert row["cost_cents"] == 900
    assert row["attributes"]["cost_source"] == "vinted_purchase"
