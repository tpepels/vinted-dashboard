from app import channels


def test_parse_biblio_inventory_tab_delimited():
    text = (
        "Book ID\tAuthor\tTitle\tDescription\tPrice\tISBN\tStatus\tQuantity\n"
        "ABC-1\tWilla Cather\tA Lost Lady\tPaperback, good condition\t9.50\t9780000000001\tFor sale\t1\n"
        "ABC-2\tJohn Williams\tStoner\tPaperback\t12,00\t9780000000002\tSold\t0\n"
    )
    rows = channels.parse_biblio_inventory(text, currency="EUR")

    assert len(rows) == 2
    assert rows[0]["source_id"] == "ABC-1"
    assert rows[0]["price_cents"] == 950
    assert rows[0]["description"] == "Paperback, good condition"
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



def test_biblio_ftp_sync_uploads_inventory_and_pending_deletes(monkeypatch, tmp_path):
    monkeypatch.setattr(channels, "DB_PATH", tmp_path / "channels.sqlite3")
    monkeypatch.setenv("BIBLIO_FTP_USERNAME", "seller")
    monkeypatch.setenv("BIBLIO_FTP_PASSWORD", "secret")
    monkeypatch.setenv("BIBLIO_FTP_HOST", "ftp.biblio.com")
    monkeypatch.setenv("BIBLIO_FTP_AUTO_SYNC", "false")

    channels.upsert_channel_snapshot(
        "biblio",
        [
            {
                "source_id": "A",
                "sku": "A",
                "title": "Book A",
                "author": "Author A",
                "description": "Hardcover",
                "status": "active",
                "quantity": 1,
                "price_cents": 500,
                "currency": "EUR",
            },
            {
                "source_id": "B",
                "sku": "B",
                "title": "Book B",
                "author": "Author B",
                "description": "Paperback",
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
                "author": "Author B",
                "description": "Paperback",
                "status": "active",
                "quantity": 1,
                "price_cents": 700,
                "currency": "EUR",
            }
        ],
        synced_at=200,
    )

    class FakeFTP:
        uploads = {}

        def connect(self, host, timeout=20):
            assert host == "ftp.biblio.com"

        def login(self, username, password):
            assert username == "seller"
            assert password == "secret"

        def set_pasv(self, value):
            assert value is True

        def storbinary(self, command, stream):
            self.uploads[command.removeprefix("STOR ")] = stream.read()

        def quit(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(channels.ftplib, "FTP", FakeFTP)

    result = channels.sync_biblio_ftp()

    assert result["active"] == 1
    assert result["deletes"] == 1
    assert result["inventory_filename"] in FakeFTP.uploads
    assert result["deletes_filename"] in FakeFTP.uploads
    assert "deletes" in result["deletes_filename"]

    inventory = FakeFTP.uploads[result["inventory_filename"]].decode("utf-8")
    deletes = FakeFTP.uploads[result["deletes_filename"]].decode("utf-8")
    assert "Book ID\tAuthor\tTitle\tDescription\tPrice" in inventory
    assert "B\tAuthor B\tBook B\tPaperback\t7.00\tfor sale" in inventory
    assert "A\tAuthor A\tBook A\tHardcover\t5.00\tsold" in deletes

    preview = channels.preview_biblio_ftp_sync()
    assert preview["delete_count"] == 0


def test_biblio_ftp_blocks_incomplete_active_upload(monkeypatch, tmp_path):
    monkeypatch.setattr(channels, "DB_PATH", tmp_path / "channels.sqlite3")
    monkeypatch.setenv("BIBLIO_FTP_USERNAME", "seller")
    monkeypatch.setenv("BIBLIO_FTP_PASSWORD", "secret")
    monkeypatch.setenv("BIBLIO_FTP_AUTO_SYNC", "false")

    channels.upsert_channel_snapshot(
        "biblio",
        [
            {
                "source_id": "A",
                "sku": "A",
                "title": "Book A",
                "author": "Author A",
                "description": "",
                "status": "active",
                "quantity": 1,
                "price_cents": 500,
                "currency": "EUR",
            }
        ],
        synced_at=100,
    )

    preview = channels.preview_biblio_ftp_sync()
    assert preview["ready"] is False
    assert preview["incomplete"][0]["missing"] == ["description"]

    try:
        channels.sync_biblio_ftp()
        assert False, "expected sync to be blocked"
    except RuntimeError as exc:
        assert "missing required fields" in str(exc)
