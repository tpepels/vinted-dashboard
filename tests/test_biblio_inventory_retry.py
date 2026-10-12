"""Targeted BIBLIO inventory recovery does not resend unrelated photos or books."""

from __future__ import annotations

import io
import uuid

import pytest

from app.connectors import hosted


def test_retry_clean_inventory_sends_only_one_record_and_no_photos(monkeypatch):
    listing_id = uuid.uuid4()
    row = {
        "source_id": "BK-123", "sku": "BK-123", "title": "A Book",
        "author": "An Author", "description": "Good copy",
        "price_cents": 500, "isbn": "9780000000002",
        "currency": "EUR", "quantity": 1, "status": "active",
        "listing_id": str(listing_id),
        "inventory_signature": "unchanged", "inventory_sync_signature": "unchanged",
        "inventory_synced_at": "2026-10-10T09:00:00+00:00",
        "inventory_dirty": False,
        "image_urls": ["https://images1.vinted.net/t/one.jpg"],
        "photo_sync_signature": None,  # Photo transfer is independently pending.
    }
    files = []
    class FTP:
        _biblio_transport = "ftps"
        def storbinary(self, name, source):
            files.append((name, source.read()))
        def quit(self):
            pass
        def close(self):
            pass

    monkeypatch.setattr(hosted, "_workspace_or_env_biblio_values", lambda *_: {
        "username": "seller", "password": "secret", "filename_prefix": "retry"
    })
    monkeypatch.setattr(hosted, "_refresh_biblio_linked_sources", lambda *a, **k: None)
    monkeypatch.setattr(hosted, "_biblio_rows", lambda *a, **k: ([row], []))
    monkeypatch.setattr(hosted, "_connect_biblio_ftp", lambda *_: FTP())
    monkeypatch.setattr(hosted, "_start_biblio_run", lambda *a, **k: uuid.uuid4())
    monkeypatch.setattr(hosted, "_update_biblio_run", lambda *a, **k: None)
    monkeypatch.setattr(hosted, "_set_biblio_listing_states", lambda *a, **k: None)
    monkeypatch.setattr(hosted, "_mark_biblio_inventory_sync", lambda *a, **k: None)
    monkeypatch.setattr(hosted, "_mark_biblio_photo_sync", lambda *a, **k: None)
    monkeypatch.setattr(hosted, "_download_biblio_jpeg", lambda *_: pytest.fail("Photo should not download"))

    result = hosted.sync_biblio_workspace(uuid.uuid4(), listing_id=listing_id, force_inventory=True)
    assert result["active"] == 1
    assert result["deletes"] == 0
    assert result["photos_uploaded"] == 0
    assert len(files) == 1
    assert files[0][0].startswith("STOR retry-")
    assert b"BK-123" in files[0][1]


def test_inventory_retry_fails_closed_for_sold_book(monkeypatch):
    monkeypatch.setattr(hosted, "_workspace_or_env_biblio_values", lambda *_: {})
    monkeypatch.setattr(hosted, "_refresh_biblio_linked_sources", lambda *a, **k: None)
    monkeypatch.setattr(hosted, "_biblio_rows", lambda *a, **k: ([], [{
        "source_id": "SOLD", "inventory_dirty": True,
    }]))
    with pytest.raises(ValueError, match="no longer active"):
        hosted.sync_biblio_workspace(
            uuid.uuid4(), listing_id=uuid.uuid4(), force_inventory=True,
        )


def test_inventory_retry_never_targets_entire_catalogue():
    with pytest.raises(ValueError, match="exactly one"):
        hosted.sync_biblio_workspace(uuid.uuid4(), force_inventory=True)
