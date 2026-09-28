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
