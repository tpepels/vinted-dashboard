from __future__ import annotations

import io
import uuid

from PIL import Image

from app.connectors import hosted


def _png_bytes(width: int = 640, height: int = 800) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _stub_progress(monkeypatch):
    run_id = uuid.uuid4()
    updates = []
    states = []
    inventory_marks = []
    monkeypatch.setattr(hosted, "_start_biblio_run", lambda *args, **kwargs: run_id)
    monkeypatch.setattr(
        hosted,
        "_update_biblio_run",
        lambda *args, **kwargs: updates.append((args, kwargs)),
    )
    monkeypatch.setattr(
        hosted,
        "_set_biblio_listing_states",
        lambda *args, **kwargs: states.append((args, kwargs)),
    )
    monkeypatch.setattr(
        hosted,
        "_mark_biblio_inventory_sync",
        lambda workspace_id, rows: inventory_marks.extend(rows),
    )
    return run_id, updates, states, inventory_marks


def test_biblio_photo_filename_matches_book_id():
    assert hosted._biblio_photo_filename("BK-100", 0) == "BK-100.jpg"
    assert hosted._biblio_photo_filename("BK-100", 1) == "BK-100_1.jpg"
    assert hosted._biblio_photo_filename("BK-100", 4) == "BK-100_4.jpg"


def test_biblio_photo_filename_rejects_unsafe_book_id():
    for value in ("A/B", "A\\B", "O'Brien"):
        try:
            hosted._biblio_photo_filename(value, 0)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe Book ID was accepted: {value}")


def test_biblio_photo_download_converts_vinted_image_to_jpeg(monkeypatch):
    class Response:
        status_code = 200
        content = _png_bytes()

    monkeypatch.setattr(hosted.requests, "get", lambda *args, **kwargs: Response())
    result = hosted._download_biblio_jpeg(
        "https://images1.vinted.net/t/01_source.webp"
    )
    assert result.startswith(b"\xff\xd8")
    with Image.open(io.BytesIO(result)) as image:
        assert image.format == "JPEG"
        assert image.size == (640, 800)


def test_biblio_photo_download_rejects_non_vinted_url():
    try:
        hosted._download_biblio_jpeg("https://example.com/photo.jpg")
    except ValueError as exc:
        assert "trusted Vinted" in str(exc)
    else:
        raise AssertionError("untrusted URL was accepted")


def test_biblio_photo_signature_prevents_repeat_upload():
    urls = [
        "https://images1.vinted.net/t/a.jpg",
        "https://images1.vinted.net/t/b.jpg",
    ]
    signature = hosted._biblio_photo_signature(urls)
    active = [{
        "sku": "BK-1",
        "image_urls": urls,
        "photo_sync_signature": signature,
    }]
    assert hosted._pending_biblio_photo_rows(active) == []


def test_biblio_sync_uploads_inventory_and_vinted_photos(monkeypatch):
    listing_id = str(uuid.uuid4())
    active = [{
        "source_id": "BK-1",
        "sku": "BK-1",
        "title": "Book",
        "author": "Author",
        "description": "Description",
        "isbn": "9780000000002",
        "price_cents": 500,
        "currency": "EUR",
        "quantity": 1,
        "status": "active",
        "listing_id": listing_id,
        "image_urls": [
            "https://images1.vinted.net/t/one.jpg",
            "https://images1.vinted.net/t/two.webp",
        ],
        "photo_sync_signature": None,
        "inventory_sync_signature": None,
        "inventory_signature": "inventory-sig",
        "inventory_dirty": True,
    }]

    monkeypatch.setattr(
        hosted,
        "_workspace_or_env_biblio_values",
        lambda workspace_id: {
            "host": "ftp.biblio.com",
            "username": "seller",
            "password": "secret",
            "directory": "",
            "filename_prefix": "test",
        },
    )
    monkeypatch.setattr(
        hosted,
        "_biblio_rows",
        lambda workspace_id, listing_id=None: (active, []),
    )
    monkeypatch.setattr(hosted, "_download_biblio_jpeg", lambda url: b"jpeg-data")

    stored: list[tuple[str, bytes]] = []

    class FakeFTP:
        def connect(self, host, timeout=20):
            return None

        def login(self, username, password):
            return None

        def set_pasv(self, value):
            return None

        def cwd(self, directory):
            return None

        def storbinary(self, command, handle):
            stored.append((command, handle.read()))

        def quit(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(hosted.ftplib, "FTP", FakeFTP)

    marked = []
    monkeypatch.setattr(
        hosted,
        "_mark_biblio_photo_sync",
        lambda workspace_id, synced: marked.extend(synced),
    )
    _run_id, updates, _states, inventory_marks = _stub_progress(monkeypatch)

    result = hosted.sync_biblio_workspace(uuid.uuid4())

    commands = [command for command, _body in stored]
    assert any(command.startswith("STOR test-") and command.endswith(".txt") for command in commands)
    assert "STOR BK-1.jpg" in commands
    assert "STOR BK-1_1.jpg" in commands
    assert result["photos_uploaded"] == 2
    assert result["photo_errors"] == []
    assert len(marked) == 1
    assert marked[0][0] == listing_id
    assert marked[0][2] == 2
    assert inventory_marks == active
    assert any(kwargs.get("status") == "success" for _args, kwargs in updates)


def test_partial_biblio_photo_failure_is_retried_later(monkeypatch):
    listing_id = str(uuid.uuid4())
    active = [{
        "source_id": "BK-2",
        "sku": "BK-2",
        "title": "Book",
        "author": "Author",
        "description": "Description",
        "isbn": None,
        "price_cents": 600,
        "currency": "EUR",
        "quantity": 1,
        "status": "active",
        "listing_id": listing_id,
        "image_urls": [
            "https://images1.vinted.net/t/one.jpg",
            "https://images1.vinted.net/t/two.jpg",
        ],
        "photo_sync_signature": None,
        "inventory_sync_signature": None,
        "inventory_signature": "inventory-sig-2",
        "inventory_dirty": True,
    }]

    monkeypatch.setattr(
        hosted,
        "_workspace_or_env_biblio_values",
        lambda workspace_id: {
            "host": "ftp.biblio.com",
            "username": "seller",
            "password": "secret",
            "directory": "",
            "filename_prefix": "test",
        },
    )
    monkeypatch.setattr(
        hosted,
        "_biblio_rows",
        lambda workspace_id, listing_id=None: (active, []),
    )

    calls = 0

    def download(url):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("temporary image failure")
        return b"jpeg-data"

    monkeypatch.setattr(hosted, "_download_biblio_jpeg", download)

    class FakeFTP:
        def connect(self, host, timeout=20): pass
        def login(self, username, password): pass
        def set_pasv(self, value): pass
        def cwd(self, directory): pass
        def storbinary(self, command, handle): handle.read()
        def quit(self): pass
        def close(self): pass

    monkeypatch.setattr(hosted.ftplib, "FTP", FakeFTP)
    marked = []
    monkeypatch.setattr(
        hosted,
        "_mark_biblio_photo_sync",
        lambda workspace_id, synced: marked.extend(synced),
    )
    _stub_progress(monkeypatch)

    result = hosted.sync_biblio_workspace(uuid.uuid4())
    assert result["photos_uploaded"] == 1
    assert result["photo_errors"]
    assert marked == []


def test_incremental_biblio_sync_skips_unchanged_inventory(monkeypatch):
    active = [{
        "source_id": "BK-UNCHANGED",
        "sku": "BK-UNCHANGED",
        "title": "Book",
        "author": "Author",
        "description": "Description",
        "isbn": None,
        "price_cents": 600,
        "currency": "EUR",
        "quantity": 1,
        "status": "active",
        "listing_id": str(uuid.uuid4()),
        "image_urls": [],
        "photo_sync_signature": None,
        "inventory_signature": "same",
        "inventory_sync_signature": "same",
        "inventory_dirty": False,
    }]
    monkeypatch.setattr(
        hosted,
        "_workspace_or_env_biblio_values",
        lambda workspace_id: {
            "host": "ftp.biblio.com",
            "username": "seller",
            "password": "secret",
            "directory": "",
            "filename_prefix": "test",
        },
    )
    monkeypatch.setattr(
        hosted,
        "_biblio_rows",
        lambda workspace_id, listing_id=None: (active, []),
    )
    monkeypatch.setattr(
        hosted.ftplib,
        "FTP",
        lambda: (_ for _ in ()).throw(AssertionError("FTP should not be opened")),
    )
    _run_id, updates, states, inventory_marks = _stub_progress(monkeypatch)

    result = hosted.sync_biblio_workspace(uuid.uuid4())

    assert result["active"] == 0
    assert result["deletes"] == 0
    assert result["photos_uploaded"] == 0
    assert "Nothing changed" in result["detail"]
    assert inventory_marks == []
    assert any(kwargs.get("status") == "success" for _args, kwargs in updates)
    assert any(kwargs.get("publish_state") == "ftp_uploaded" for _args, kwargs in states)


def test_photo_only_retry_resends_photos_without_inventory(monkeypatch):
    listing_id = str(uuid.uuid4())
    urls = [
        "https://images1.vinted.net/t/one.jpg",
        "https://images1.vinted.net/t/two.jpg",
    ]
    active = [{
        "source_id": "BK-PHOTOS",
        "sku": "BK-PHOTOS",
        "title": "Book",
        "author": "Author",
        "description": "Description",
        "isbn": None,
        "price_cents": 600,
        "currency": "EUR",
        "quantity": 1,
        "status": "active",
        "listing_id": listing_id,
        "image_urls": urls,
        "photo_sync_signature": hosted._biblio_photo_signature(urls),
        "inventory_signature": "same",
        "inventory_sync_signature": "same",
        "inventory_dirty": False,
    }]
    monkeypatch.setattr(
        hosted,
        "_workspace_or_env_biblio_values",
        lambda workspace_id: {
            "host": "ftp.biblio.com",
            "username": "seller",
            "password": "secret",
            "directory": "",
            "filename_prefix": "test",
        },
    )
    monkeypatch.setattr(
        hosted,
        "_biblio_rows",
        lambda workspace_id, listing_id=None: (active, []),
    )
    monkeypatch.setattr(hosted, "_download_biblio_jpeg", lambda url: b"jpeg-data")

    stored = []

    class FakeFTP:
        def connect(self, host, timeout=20): pass
        def login(self, username, password): pass
        def set_pasv(self, value): pass
        def cwd(self, directory): pass
        def storbinary(self, command, handle):
            stored.append((command, handle.read()))
        def quit(self): pass
        def close(self): pass

    monkeypatch.setattr(hosted.ftplib, "FTP", FakeFTP)
    marked = []
    monkeypatch.setattr(
        hosted,
        "_mark_biblio_photo_sync",
        lambda workspace_id, synced: marked.extend(synced),
    )
    _run_id, updates, _states, inventory_marks = _stub_progress(monkeypatch)

    result = hosted.sync_biblio_workspace(
        uuid.uuid4(),
        photos_only=True,
        force_photos=True,
    )

    commands = [command for command, _body in stored]
    assert commands == ["STOR BK-PHOTOS.jpg", "STOR BK-PHOTOS_1.jpg"]
    assert result["active"] == 0
    assert result["photos_uploaded"] == 2
    assert inventory_marks == []
    assert marked and marked[0][0] == listing_id
    assert any(kwargs.get("status") == "success" for _args, kwargs in updates)
