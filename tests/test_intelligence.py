from app import intelligence


def _snapshot(collected_at, favourites=0, market_results=None):
    return {
        "collected_at": collected_at,
        "current_user": {"id": "58344842", "username": "tom_waits"},
        "listings": [
            {
                "id": "101",
                "title": "A Lost Lady - Willa Cather",
                "status": "active",
                "price_cents": 900,
                "currency": "EUR",
                "favourites": favourites,
                "views": 20,
                "listed_at": "2026-01-01T12:00:00+00:00",
                "vinted_url": "https://www.vinted.pt/items/101",
            }
        ],
        "notifications": [
            {
                "id": "fav-1",
                "category": "favorite",
                "item_id": "101",
                "item_title": "A Lost Lady - Willa Cather",
                "actor": "reader",
                "occurred_at": "2026-09-20T12:00:00+00:00",
            }
        ],
        "orders": [
            {
                "id": "sale-1",
                "direction": "sell",
                "title": "Sold Book",
                "total_cents": 1200,
                "currency": "EUR",
                "status": "completed",
                "lifecycle_status": "completed",
                "is_closed": True,
                "updated_at": "2026-09-20T12:00:00+00:00",
            }
        ],
        "market_results": market_results or [],
    }


def test_history_favorites_and_stale_actions(monkeypatch, tmp_path):
    monkeypatch.setattr(intelligence, "DB_PATH", tmp_path / "history.sqlite3")

    intelligence.record_snapshot(_snapshot(1_790_000_000, favourites=0))
    intelligence.record_snapshot(_snapshot(1_790_700_000, favourites=3))

    payload = intelligence.intelligence_payload(_snapshot(1_790_700_000, favourites=3)["listings"])

    assert payload["favorites"]["events_30d"] == 1
    assert payload["sales"]["sales_count"] == 1
    assert payload["sales"]["sales_cents"] == 1200
    assert payload["stale"]["count_90d"] == 1
    assert payload["today"]["actions"]


def test_market_research_queue_and_result(monkeypatch, tmp_path):
    monkeypatch.setattr(intelligence, "DB_PATH", tmp_path / "history.sqlite3")

    intelligence.record_snapshot(_snapshot(1_790_000_000))
    job = intelligence.request_market_research(
        "101", "A Lost Lady - Willa Cather"
    )

    queued = intelligence.queued_market_jobs()
    assert [row["id"] for row in queued] == [job["id"]]

    result = {
        "job_id": job["id"],
        "results": [
            {
                "id": "201",
                "title": "A Lost Lady - Willa Cather",
                "price_cents": 700,
                "currency": "EUR",
                "url": "https://www.vinted.pt/items/201",
            },
            {
                "id": "202",
                "title": "A Lost Lady by Willa Cather",
                "price_cents": 900,
                "currency": "EUR",
                "url": "https://www.vinted.pt/items/202",
            },
            {
                "id": "203",
                "title": "Completely unrelated book",
                "price_cents": 200,
                "currency": "EUR",
                "url": "https://www.vinted.pt/items/203",
            },
        ],
    }
    intelligence.record_snapshot(_snapshot(1_790_100_000, market_results=[result]))

    payload = intelligence.intelligence_payload(_snapshot(1_790_100_000)["listings"])
    market = payload["listing_signals"][0]["market"]

    assert market["sample_count"] == 2
    assert market["min_cents"] == 700
    assert market["median_cents"] == 800
    assert market["max_cents"] == 900
    assert intelligence.queued_market_jobs() == []
