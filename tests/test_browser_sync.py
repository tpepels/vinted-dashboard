import json
import zipfile
from io import BytesIO

from app import entry


def test_browser_sync_snapshot_roundtrip(monkeypatch, tmp_path):
    snapshot_file = tmp_path / "browser-sync.json"
    monkeypatch.setattr(entry, "BROWSER_SYNC_FILE", snapshot_file)

    payload = entry.BrowserSyncPayload(
        collected_at=1_900_000_000.0,
        current_user={"id": "58344842", "username": "tom_waits"},
        listings=[
            {
                "id": "101",
                "title": "Stoner",
                "price_cents": 800,
                "currency": "EUR",
                "status": "active",
            }
        ],
        notifications=[],
        orders=[],
    )

    entry._save_snapshot(payload)

    saved = json.loads(snapshot_file.read_text(encoding="utf-8"))
    assert saved["current_user"]["id"] == "58344842"
    assert saved["listings"][0]["title"] == "Stoner"


def test_extension_zip_contains_manifest():
    response = entry.download_extension()
    archive = zipfile.ZipFile(BytesIO(response.body))
    names = set(archive.namelist())

    assert "manifest.json" in names
    assert "background.js" in names
    assert "content.js" in names
    assert "popup.html" in names
