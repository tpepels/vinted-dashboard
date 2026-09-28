from app.vinted import VintedClient, _listing_status, _money, is_closed_status


def test_money_shapes():
    assert _money({"amount": "12.79", "currency_code": "EUR"}) == (1279, "EUR")
    assert _money("3,50") == (350, "EUR")
    assert _money(None) == (None, "EUR")


def test_listing_status():
    assert _listing_status({"is_reserved": True}) == "reserved"
    assert _listing_status({"is_closed": True}) == "sold"
    assert _listing_status({"status": "active"}) == "active"
    assert _listing_status({}) == "active"


def test_closed_statuses():
    assert is_closed_status("completed")
    assert is_closed_status("transaction_cancelled")
    assert is_closed_status("refunded")
    assert not is_closed_status("shipped")
    assert not is_closed_status("awaiting_shipment")


def test_order_normalization_sell(monkeypatch):
    monkeypatch.setenv("VINTED_USER_ID", "58344842")
    client = VintedClient()
    thread = {
        "id": "thread-1",
        "updated_at": "2026-09-28T10:00:00Z",
        "transaction": {
            "id": "tx-1",
            "status": "shipped",
            "seller": {"id": 58344842, "login": "tom_waits"},
            "buyer": {"id": 99, "login": "buyer99"},
            "item": {"id": 123, "title": "Stoner", "price": {"amount": "8.00", "currency_code": "EUR"}},
        },
    }
    row = client._normalize_order(thread, thread["transaction"], "58344842")
    assert row["direction"] == "sell"
    assert row["counterparty"] == "buyer99"
    assert row["title"] == "Stoner"
    assert row["total_cents"] == 800
    assert row["status"] == "shipped"
    assert row["is_closed"] is False


def test_order_normalization_purchase(monkeypatch):
    monkeypatch.setenv("VINTED_USER_ID", "58344842")
    client = VintedClient()
    thread = {
        "id": "thread-2",
        "transaction": {
            "id": "tx-2",
            "status": "completed",
            "buyer": {"id": 58344842, "login": "tom_waits"},
            "seller": {"id": 88, "login": "seller88"},
            "item": {"title": "Pedro Paramo"},
            "total_price": {"amount": "9.50", "currency_code": "EUR"},
        },
    }
    row = client._normalize_order(thread, thread["transaction"], "58344842")
    assert row["direction"] == "buy"
    assert row["counterparty"] == "seller88"
    assert row["is_closed"] is True


def test_transaction_detection():
    client = VintedClient()
    transaction = {"id": "tx", "status": "paid"}
    assert client._transaction_from_thread({"transaction": transaction}) is transaction
    assert client._transaction_from_thread({"id": "plain-message"}) is None



def test_auth_headers_from_browser_cookie(monkeypatch, tmp_path):
    monkeypatch.delenv("VINTED_ACCESS_TOKEN_WEB", raising=False)
    monkeypatch.delenv("VINTED_ANON_ID", raising=False)
    monkeypatch.delenv("VINTED_CSRF_TOKEN", raising=False)
    monkeypatch.setenv(
        "VINTED_COOKIE",
        "access_token_web=access-123; refresh_token_web=refresh-456; "
        "anon_id=anon-789; csrf_token=csrf-abc",
    )
    client = VintedClient()
    client.session_file = tmp_path / "vinted-session.cookie"

    headers = client._headers()

    assert headers["Authorization"] == "Bearer access-123"
    assert headers["X-Anon-Id"] == "anon-789"
    assert headers["X-Csrf-Token"] == "csrf-abc"
    assert "access_token_web=access-123" in headers["Cookie"]


def test_notifications_use_direct_vinted_endpoint(monkeypatch):
    client = VintedClient()
    calls = []

    def fake_request(path, **kwargs):
        calls.append((path, kwargs))
        return {
            "notifications": [
                {
                    "id": "n1",
                    "is_read": False,
                    "updated_at": "2026-09-28T10:00:00Z",
                    "entry_type": 17,
                    "body": "Your parcel is ready for collection",
                    "link": "/inbox/123",
                }
            ]
        }

    monkeypatch.setattr(client, "_request", fake_request)
    rows = client.get_notifications()

    assert calls[0][0] == "/api/v2/notifications"
    assert rows[0]["id"] == "n1"
    assert rows[0]["read"] is False
    assert rows[0]["body"] == "Your parcel is ready for collection"
    assert rows[0]["url"] == "https://www.vinted.pt/inbox/123"
    assert client._notifications_source == "/api/v2/notifications"


def test_orders_use_direct_my_orders_endpoint(monkeypatch):
    client = VintedClient()
    calls = []

    def fake_request(path, *, params=None, **kwargs):
        calls.append((path, params))
        assert path == "/api/v2/my_orders"
        if params["type"] == "sold":
            return {
                "my_orders": [
                    {
                        "conversation_id": 1001,
                        "title": "Stoner - John Williams",
                        "price": {"amount": "8.00", "currency_code": "EUR"},
                        "date": "2026-09-28T10:00:00Z",
                        "transaction_user_status": "in_progress",
                    }
                ],
                "pagination": {"total_pages": 1},
            }
        return {
            "my_orders": [
                {
                    "conversation_id": 1002,
                    "title": "Pedro Paramo",
                    "price": {"amount": "9.50", "currency_code": "EUR"},
                    "date": "2026-09-27T10:00:00Z",
                    "transaction_user_status": "completed",
                }
            ],
            "pagination": {"total_pages": 1},
        }

    monkeypatch.setattr(client, "_request", fake_request)
    rows, source = client.get_orders()

    assert source == "/api/v2/my_orders"
    assert [c[1]["type"] for c in calls] == ["sold", "purchased"]
    assert rows[0]["direction"] == "sell"
    assert rows[0]["title"] == "Stoner - John Williams"
    assert rows[0]["status"] == "in_progress"
    assert rows[0]["is_closed"] is False
    assert rows[0]["vinted_url"] == "https://www.vinted.pt/inbox/1001"
    assert rows[1]["direction"] == "buy"
    assert rows[1]["status"] == "completed"
    assert rows[1]["is_closed"] is True


def test_my_orders_falls_back_to_conversations(monkeypatch):
    client = VintedClient()

    def fake_get_my_orders(order_type, direction):
        return [], False

    monkeypatch.setattr(client, "_get_my_orders", fake_get_my_orders)
    monkeypatch.setattr(client, "get_current_user", lambda: {"id": "58344842"})
    monkeypatch.setattr(
        client,
        "_get_threads",
        lambda: (
            [
                {
                    "id": "thread-3",
                    "transaction": {
                        "id": "tx-3",
                        "status": "shipped",
                        "seller": {"id": 58344842, "login": "tom_waits"},
                        "buyer": {"id": 77, "login": "buyer77"},
                        "item": {"title": "Fallback book"},
                    },
                }
            ],
            "/api/v2/conversations",
        ),
    )

    rows, source = client.get_orders()

    assert source == "/api/v2/conversations"
    assert rows[0]["direction"] == "sell"
    assert rows[0]["counterparty"] == "buyer77"
