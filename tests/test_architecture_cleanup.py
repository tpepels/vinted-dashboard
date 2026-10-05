from pathlib import Path

from fastapi.testclient import TestClient

from app import entry


ROOT = Path(__file__).resolve().parents[1]


def test_production_runtime_does_not_wrap_legacy_app():
    source = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "from app import main" not in source
    assert "BrowserSyncClient" not in source
    assert "LEGACY_API_ENABLED" not in source
    assert "LEGACY_UI_ENABLED" not in source
    assert "legacy_api_gate" not in source


def test_obsolete_runtime_modules_and_assets_are_removed():
    for relative in (
        "app/main.py",
        "app/vinted.py",
        "app/intelligence.py",
        "app/channels.py",
        "app/static/index.html",
        "app/static/app.js",
        "app/static/styles.css",
        "app/legacy_extension/manifest.json",
        "app/legacy_extension/background.js",
        "app/legacy_extension/content.js",
        "app/legacy_extension/popup.html",
        "app/legacy_extension/popup.js",
        "app/legacy_extension/popup.css",
    ):
        assert not (ROOT / relative).exists(), relative


def test_legacy_http_surface_is_not_mounted():
    client = TestClient(entry.app)
    assert client.get("/classic").status_code == 404
    assert client.get("/api/dashboard").status_code == 404
    assert client.get("/api/browser-sync/status").status_code == 404
    assert client.get("/api/intelligence").status_code == 404
    assert client.get("/api/market-research/queue").status_code == 404
    assert client.get("/downloads/vinted-session-sync.zip").status_code == 404


def test_bridge_api_has_single_owner():
    product = (ROOT / "app" / "product_api.py").read_text(encoding="utf-8")
    bridge = (ROOT / "app" / "bridge_api.py").read_text(encoding="utf-8")

    for route in (
        "/api/app/extension/pairings",
        "/api/app/extension/devices",
        "/api/extension/pair",
        "/api/extension/status",
        "/api/extension/browser-sync",
    ):
        assert route not in product
        assert route in bridge


def test_scanner_camera_is_allowed_only_for_same_origin():
    response = TestClient(entry.app).get("/")
    policy = response.headers["permissions-policy"]
    assert "camera=(self)" in policy
    assert "microphone=()" in policy
    assert "geolocation=()" in policy
