from app.main import _is_void_order, _order_year, _ytd_aggregate


def test_order_year_parses_vinted_timestamps():
    assert _order_year("2026-09-28T10:00:00Z") == 2026
    assert _order_year("2025-12-31T23:59:59+00:00") == 2025
    assert _order_year(None) is None


def test_ytd_aggregate_excludes_void_orders_and_other_years():
    orders = [
        {
            "updated_at": "2026-01-10T10:00:00Z",
            "total_cents": 1000,
            "currency": "EUR",
            "status": "completed",
            "lifecycle_status": "completed",
        },
        {
            "updated_at": "2026-09-27T10:00:00Z",
            "total_cents": 1300,
            "currency": "EUR",
            "status": "shipping label sent to seller.",
            "lifecycle_status": "in_progress",
        },
        {
            "updated_at": "2026-08-01T10:00:00Z",
            "total_cents": 500,
            "currency": "EUR",
            "status": "order canceled",
            "lifecycle_status": "canceled",
        },
        {
            "updated_at": "2025-12-31T10:00:00Z",
            "total_cents": 9999,
            "currency": "EUR",
            "status": "completed",
            "lifecycle_status": "completed",
        },
    ]

    assert _is_void_order(orders[2])
    result = _ytd_aggregate(orders, 2026)

    assert result == {
        "count": 2,
        "total_cents": 2300,
        "currency": "EUR",
    }
