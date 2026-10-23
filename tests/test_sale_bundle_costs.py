"""Bundle quantities must multiply acquisition cost per book, not per order."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.sale_costs import sale_acquisition_cost, sale_book_quantity, record_sale_book_quantity
from app.workspace_ingest import record_workspace_snapshot


def _prepare():
    client = TestClient(entry.app)
    resp = client.post("/api/auth/register", json={
        "email": "book-bundles@example.test", "password": "a-long-test-password",
        "workspace_name": "Book bundles",
    })
    assert resp.status_code == 200, resp.text
    headers = {"X-CSRF-Token": resp.json()["csrf_token"]}
    sale_ids = {}
    workspace_id = None
    with db.session_scope() as session:
        workspace = session.execute(select(models.Workspace)).scalars().one()
        workspace_id = workspace.id
        item = models.InventoryItem(
            workspace_id=workspace_id, sku="OLD-STOCK", title="Stock",
            category="book", status="sold", quantity=0, cost_cents=300,
            currency="EUR", attributes={},
        )
        session.add(item)
        session.flush()
        now = datetime.now(timezone.utc) - timedelta(days=7)
        for oid, extra, linked, value in [
            ("bundle5", {"quantity": 5}, False, 2200),
            ("unknown", {}, False, 2500),
            ("linked3", {"quantity": 3}, True, 3600),
            ("old-bulk", {"quantity": 2, "acquisition_cost_source": "estimated_bulk",
                          "acquisition_cost_cents": 100}, False, 1200),
        ]:
            sale = models.Sale(
                workspace_id=workspace_id,
                inventory_item_id=item.id if linked else None,
                channel="vinted", external_order_id=oid, direction="sell",
                title=oid, total_cents=value, currency="EUR",
                status="completed", lifecycle_status="completed", is_closed=True,
                occurred_at=now, extra=extra,
            )
            session.add(sale)
            session.flush()
            sale_ids[oid] = sale.id
    return client, headers, sale_ids, workspace_id


def test_title_inference_and_estimated_costs():
    for title, expected in [
        ("Bundle 3 items", (3, "title_bundle")),
        ("Bundle 7 items", (7, "title_bundle")),
        ("bundle of 2 books", (2, "title_bundle")),
        ("Open City - Teju Cole", (1, "title_single")),
        ("Bundle without count", (None, "unknown")),
        ("Vinted order", (None, "unknown")),
        ("Bundle 0 items", (None, "unknown")),
        ("Bundle 1001 items", (None, "unknown")),
    ]:
        sale = SimpleNamespace(channel="vinted", title=title, extra={})
        assert sale_book_quantity(sale) == expected
    sale = SimpleNamespace(
        channel="vinted", title="Bundle 3 items",
        extra={"acquisition_cost_source": "estimated_bulk", "acquisition_cost_cents": 200},
    )
    assert sale_acquisition_cost(sale) == (600, "estimated_bulk")
    sale.extra["quantity"] = 2
    assert sale_book_quantity(sale) == (2, "marketplace")
    record_sale_book_quantity(sale, 4, "test")
    assert sale_book_quantity(sale) == (4, "manual")
    assert sale_acquisition_cost(sale) == (800, "estimated_bulk")
    record_sale_book_quantity(sale, None, "test")
    assert sale_book_quantity(sale) == (2, "marketplace")
    assert sale_book_quantity(SimpleNamespace(
        channel="ebay", title="Bundle 3 items", extra={},
    )) == (None, "unknown")


def test_historical_sales_api_infers_counts_without_vinted_resync():
    client, headers, _, workspace_id = _prepare()
    with db.session_scope() as session:
        for order_id, title in [
            ("title-bundle", "Bundle 7 items"),
            ("title-single", "Open City - Teju Cole"),
            ("title-ambiguous", "Bundle without count"),
        ]:
            session.add(models.Sale(
                workspace_id=workspace_id, channel="vinted",
                external_order_id=order_id, direction="sell", title=title,
                total_cents=1500, currency="EUR", status="completed",
                lifecycle_status="completed", is_closed=True,
                occurred_at=datetime.now(timezone.utc), extra={},
            ))
    rows = {row["external_order_id"]: row
            for row in client.get("/api/app/sales").json()["sales"]}
    assert (rows["title-bundle"]["book_quantity"],
            rows["title-bundle"]["quantity_source"]) == (7, "title_bundle")
    assert (rows["title-single"]["book_quantity"],
            rows["title-single"]["quantity_source"]) == (1, "title_single")
    assert rows["title-ambiguous"]["book_quantity"] is None
    response = client.post("/api/app/sales/average-cost", headers=headers, json={
        "currency": "EUR", "cost_cents": 200, "scope": "all",
    })
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["eligible_books"] == 13
    assert preview["inferred_quantity_sales"] == 2
    assert preview["skipped_unknown_quantity_sales"] == 2


def test_per_book_bulk_price_and_manual_quantity_repricing():
    client, headers, ids, _ = _prepare()
    endpoint = "/api/app/sales/average-cost"
    payload = {"currency": "EUR", "cost_cents": 100, "scope": "all"}
    preview = client.post(endpoint, json=payload, headers=headers)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["eligible_sales"] == 1  # unknown skipped, linked has cost, legacy estimate counted
    assert body["eligible_books"] == 5
    assert body["unknown_quantity_sales"] == 1
    assert body["skipped_unknown_quantity_sales"] == 1
    assert body["estimated_cost_cents"] == 500

    applied = client.post(endpoint, json={**payload, "preview": False}, headers=headers)
    assert applied.status_code == 200, applied.text
    rows = {sale["external_order_id"]: sale for sale in client.get("/api/app/sales").json()["sales"]}
    assert rows["bundle5"]["book_quantity"] == 5
    assert rows["bundle5"]["acquisition_cost_cents"] == 500
    assert rows["bundle5"]["acquisition_unit_cost_cents"] == 100
    assert rows["linked3"]["acquisition_cost_cents"] == 900
    assert rows["old-bulk"]["acquisition_cost_cents"] == 200

    summary = client.get("/api/app/analytics?period=30").json()
    assert summary["sales_period_unknown_quantity_count"] == 1
    assert summary["sales_period_costed_count"] == 4  # Unknown count temporarily uses €1 default
    assert summary["sales_period_cost_cents"] == 1700
    assert summary["sales_period_gross_profit_cents"] == 7800

    set_qty = client.patch(f"/api/app/sales/{ids['unknown']}/book-quantity",
                           json={"quantity": 4}, headers=headers)
    assert set_qty.status_code == 200, set_qty.text
    assert set_qty.json()["book_quantity"] == 4
    after = client.post(endpoint, json=payload, headers=headers).json()
    assert after["eligible_books"] == 4
    assert after["skipped_unknown_quantity_sales"] == 0
    assert after["estimated_cost_cents"] == 400
    client.post(endpoint, json={**payload, "preview": False}, headers=headers)
    summary = client.get("/api/app/analytics?period=30").json()
    assert summary["sales_period_economics_complete"] is True
    assert summary["sales_period_cost_cents"] == 2000
    assert summary["sales_period_gross_profit_cents"] == 7500

    # Correct a bulk backfill from 5 to 6 books: per-book estimate reprices.
    change = client.patch(f"/api/app/sales/{ids['bundle5']}/book-quantity",
                          json={"quantity": 6}, headers=headers)
    assert change.status_code == 200, change.text
    assert change.json()["cost_cents"] == 600
    # Existing order-total manual overrides are never multiplied again.
    manual = client.patch(f"/api/app/sales/{ids['bundle5']}/acquisition-cost",
                          json={"cost_cents": 250}, headers=headers)
    assert manual.status_code == 200
    assert client.patch(f"/api/app/sales/{ids['bundle5']}/book-quantity",
                        json={"quantity": 7}, headers=headers).json()["cost_cents"] == 250

    assert client.patch(f"/api/app/sales/{ids['unknown']}/book-quantity",
                        json={"quantity": 0}, headers=headers).status_code == 422
    assert client.patch(f"/api/app/sales/{ids['unknown']}/book-quantity",
                        json={"quantity": 1.5}, headers=headers).status_code == 422


def test_unknown_bundle_requires_explicit_one_book_assumption():
    client, headers, ids, _ = _prepare()
    res = client.post("/api/app/sales/average-cost", headers=headers, json={
        "cost_cents": 100, "currency": "EUR",
        "assume_one_when_unknown": True,
    }).json()
    assert res["unknown_quantity_sales"] == 1
    assert res["assumed_one_quantity_sales"] == 1
    assert res["eligible_sales"] == 2
    assert res["eligible_books"] == 6
    result = client.post("/api/app/sales/average-cost", headers=headers, json={
        "cost_cents": 100, "currency": "EUR",
        "assume_one_when_unknown": True, "preview": False,
    })
    assert result.status_code == 200
    assert result.json()["updated"] == 2
    change = client.patch(f"/api/app/sales/{ids['unknown']}/book-quantity",
                          headers=headers, json={"quantity": 4})
    assert change.status_code == 200
    assert change.json()["cost_cents"] == 400
    # Legacy per-order estimates still reprice once the true count is known.
    legacy = client.patch(f"/api/app/sales/{ids['old-bulk']}/book-quantity",
                          headers=headers, json={"quantity": 3})
    assert legacy.status_code == 200
    assert legacy.json()["cost_cents"] == 300


def test_vinted_resync_preserves_estimates_and_corrected_book_counts():
    client, headers, ids, workspace_id = _prepare()
    target = ids["bundle5"]
    client.patch(f"/api/app/sales/{target}/book-quantity", headers=headers,
                 json={"quantity": 6})
    client.patch(f"/api/app/sales/{target}/acquisition-cost", headers=headers,
                 json={"cost_cents": 800})
    snapshot = {
        "collected_at": datetime.now(timezone.utc).timestamp(),
        "current_user": {"id": "seller-1", "username": "seller"},
        "listings": [], "notifications": [],
        "orders": [{
            "id": "bundle5", "direction": "sell", "title": "Bundled books",
            "status": "completed", "lifecycle_status": "completed",
            "is_closed": True, "total_cents": 2200, "currency": "EUR",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "quantity": 5,
        }],
    }
    record_workspace_snapshot(workspace_id, snapshot)
    with db.session_scope() as session:
        sale = session.get(models.Sale, target)
        assert sale.extra["book_quantity_override"] == 6
        assert sale.extra["quantity"] == 5
        assert sale.extra["acquisition_cost_cents"] == 800
        assert len(sale.extra["acquisition_cost_history"]) == 2
        assert sale_acquisition_cost(sale)[0] == 800

    # A later Vinted response without quantity does not reset it.
    snapshot["collected_at"] += 60
    snapshot["orders"][0].pop("quantity")
    record_workspace_snapshot(workspace_id, snapshot)
    with db.session_scope() as session:
        sale = session.get(models.Sale, target)
        assert sale.extra["quantity"] == 5
        assert sale.extra["book_quantity_override"] == 6
        assert sale.extra["acquisition_cost_cents"] == 800
