from app import channels


def test_parse_biblio_inventory_tab_delimited():
    text = (
        "Book ID\tAuthor\tTitle\tPrice\tISBN\tStatus\tQuantity\n"
        "ABC-1\tWilla Cather\tA Lost Lady\t9.50\t9780000000001\tFor sale\t1\n"
        "ABC-2\tJohn Williams\tStoner\t12,00\t9780000000002\tSold\t0\n"
    )
    rows = channels.parse_biblio_inventory(text, currency="EUR")

    assert len(rows) == 2
    assert rows[0]["source_id"] == "ABC-1"
    assert rows[0]["price_cents"] == 950
    assert rows[0]["status"] == "active"
    assert rows[1]["price_cents"] == 1200
    assert rows[1]["status"] == "sold"


def test_channel_snapshot_marks_missing_active_items_inactive(monkeypatch, tmp_path):
    monkeypatch.setattr(channels, "DB_PATH", tmp_path / "channels.sqlite3")

    channels.upsert_channel_snapshot(
        "biblio",
        [
            {
                "source_id": "A",
                "sku": "A",
                "title": "Book A",
                "status": "active",
                "quantity": 1,
                "price_cents": 500,
                "currency": "EUR",
            },
            {
                "source_id": "B",
                "sku": "B",
                "title": "Book B",
                "status": "active",
                "quantity": 1,
                "price_cents": 700,
                "currency": "EUR",
            },
        ],
        synced_at=100,
    )

    channels.upsert_channel_snapshot(
        "biblio",
        [
            {
                "source_id": "B",
                "sku": "B",
                "title": "Book B",
                "status": "active",
                "quantity": 1,
                "price_cents": 700,
                "currency": "EUR",
            }
        ],
        synced_at=200,
    )

    payload = channels.channel_inventory_payload()
    rows = {row["source_id"]: row for row in payload["items"]}

    assert rows["A"]["status"] == "inactive"
    assert rows["A"]["quantity"] == 0
    assert rows["B"]["status"] == "active"


def test_record_vinted_items(monkeypatch, tmp_path):
    monkeypatch.setattr(channels, "DB_PATH", tmp_path / "channels.sqlite3")

    channels.record_vinted_items(
        [
            {
                "id": "101",
                "title": "Stoner",
                "status": "active",
                "price_cents": 800,
                "currency": "EUR",
                "vinted_url": "https://www.vinted.pt/items/101",
            }
        ],
        synced_at=123,
    )

    payload = channels.channel_inventory_payload()
    vinted = next(row for row in payload["sources"] if row["source"] == "vinted")

    assert vinted["active"] == 1
    assert vinted["quantity"] == 1
