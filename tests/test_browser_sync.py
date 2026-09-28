import json
import zipfile
from io import BytesIO

from app import entry, intelligence


def test_browser_sync_snapshot_roundtrip(monkeypatch, tmp_path):
    snapshot_file = tmp_path / "browser-sync.json"
    monkeypatch.setattr(entry, "BROWSER_SYNC_FILE", snapshot_file)
    monkeypatch.setattr(intelligence, "DB_PATH", tmp_path / "history.sqlite3")

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


def test_extension_zip_contains_manifest_and_uses_download_host():
    archive = zipfile.ZipFile(
        BytesIO(entry._extension_zip("http://192.168.1.200:5050"))
    )
    names = set(archive.namelist())

    assert "manifest.json" in names
    assert "background.js" in names
    assert "content.js" in names
    assert "popup.html" in names

    background = archive.read("background.js").decode("utf-8")
    manifest = archive.read("manifest.json").decode("utf-8")
    assert 'const DASHBOARD_URL = "http://192.168.1.200:5050";' in background
    assert "http://192.168.1.200/*" in manifest
    assert "http://media-server:5050" not in background
