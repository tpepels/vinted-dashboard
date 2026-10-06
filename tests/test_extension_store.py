from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


build_extension = _load_script("build_extension_test_module", "scripts/build_extension.py")
publish_store = _load_script("publish_store_test_module", "scripts/publish_chrome_webstore.py")


def _manifest(zip_path: Path) -> dict:
    with zipfile.ZipFile(zip_path) as archive:
        return json.loads(archive.read("manifest.json").decode("utf-8"))


def test_store_build_is_deterministic_and_minimally_permissioned(tmp_path):
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    build_extension.build("store", "https://dashboard.example", first, "2.1.0")
    build_extension.build("store", "https://dashboard.example", second, "2.1.0")

    assert first.read_bytes() == second.read_bytes()
    assert build_extension.sha256(first) == build_extension.sha256(second)

    manifest = _manifest(first)
    assert manifest["manifest_version"] == 3
    assert manifest["version"] == "2.1.0"
    assert set(manifest["permissions"]) == {"storage", "alarms", "scripting"}
    assert "tabs" not in manifest["permissions"]
    assert "<all_urls>" not in manifest["host_permissions"]
    assert "https://dashboard.example/*" in manifest["host_permissions"]
    assert manifest["homepage_url"] == "https://dashboard.example"

    matches = {
        value
        for script in manifest["content_scripts"]
        for value in script["matches"]
    }
    assert matches
    assert all(value.startswith("https://www.vinted.") for value in matches)


def test_store_build_requires_version_and_strict_https_origin(tmp_path):
    with pytest.raises(ValueError, match="explicit --version"):
        build_extension.build(
            "store",
            "https://dashboard.example",
            tmp_path / "missing-version.zip",
            None,
        )

    invalid = [
        "http://dashboard.example",
        "https://localhost",
        "https://dashboard.local",
        "https://dashboard.example/path",
        "https://dashboard.example?query=yes",
        "https://user:secret@dashboard.example",
    ]
    for origin in invalid:
        with pytest.raises(ValueError):
            build_extension.build(
                "store",
                origin,
                tmp_path / "invalid.zip",
                "2.1.0",
            )


def test_manifest_version_validation_matches_chrome_numeric_constraints():
    for value in ("1", "2.1", "2.1.0", "2.1.0.4", "65535.0.1"):
        build_extension.validate_version(value)

    for value in ("", "v2.1.0", "2.1.0.0.1", "02.1", "2.-1", "65536.1"):
        with pytest.raises(ValueError):
            build_extension.validate_version(value)


def test_store_package_contains_no_placeholders_remote_code_or_unused_notification_text(tmp_path):
    output = tmp_path / "store.zip"
    build_extension.build("store", "https://dashboard.example", output, "2.1.0")

    with zipfile.ZipFile(output) as archive:
        for name in archive.namelist():
            if Path(name).suffix.lower() not in {".js", ".json", ".html", ".css", ".txt"}:
                continue
            text = archive.read(name).decode("utf-8")
            assert "__API_ORIGIN__" not in text
            assert "__API_HOST_PERMISSION__" not in text
            assert "eval(" not in text
            assert "new Function(" not in text

        content = archive.read("content.js").decode("utf-8")
        assert '.filter(row=>row.category==="favorite")' in content
        assert "item_title:row.item_title" in content
        assert "actor:row.actor" in content
        assert "body:row.body" not in content


def test_dev_build_can_use_local_http_and_replaces_homepage(tmp_path):
    output = tmp_path / "dev.zip"
    build_extension.build("dev", "http://localhost:5050", output, None)
    manifest = _manifest(output)
    assert manifest["homepage_url"] == "http://localhost:5050"
    assert "http://localhost/*" in manifest["host_permissions"]


def test_publish_inspection_rejects_invalid_package_and_reads_version(tmp_path):
    output = tmp_path / "store.zip"
    build_extension.build("store", "https://dashboard.example", output, "3.1.0")
    manifest = publish_store.inspect_package(output)
    assert manifest["manifest_version"] == 3
    assert manifest["version"] == "3.1.0"

    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("popup.html", "<p>missing manifest</p>")
    with pytest.raises(SystemExit, match="no manifest"):
        publish_store.inspect_package(bad)


def test_upload_polling_waits_for_success_and_stops_on_failure(monkeypatch):
    monkeypatch.setattr(publish_store.time, "sleep", lambda _seconds: None)

    states = iter(
        [
            {"lastAsyncUploadState": "IN_PROGRESS"},
            {"lastAsyncUploadState": "SUCCEEDED"},
        ]
    )
    monkeypatch.setattr(
        publish_store,
        "fetch_status",
        lambda _item, _headers: next(states),
    )
    result = publish_store.wait_for_upload(
        {"uploadState": "IN_PROGRESS"},
        "publishers/p/items/e",
        {"Authorization": "Bearer test"},
        attempts=3,
        delay_seconds=0,
    )
    assert result["lastAsyncUploadState"] == "SUCCEEDED"

    monkeypatch.setattr(
        publish_store,
        "fetch_status",
        lambda _item, _headers: {"lastAsyncUploadState": "FAILED"},
    )
    with pytest.raises(SystemExit, match="FAILED"):
        publish_store.wait_for_upload(
            {"uploadState": "IN_PROGRESS"},
            "publishers/p/items/e",
            {"Authorization": "Bearer test"},
            attempts=1,
            delay_seconds=0,
        )


def test_host_permission_drops_port_but_keeps_scheme():
    assert build_extension.permission_for("https://dashboard.example:8443") == (
        "https://dashboard.example/*"
    )


def test_source_extension_version_is_bumped_for_local_download():
    manifest = json.loads((ROOT / "app" / "extension" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "3.1.0"



def test_content_script_uses_persistent_rendered_uploaded_age_sweep():
    content = (ROOT / "app" / "extension" / "content.js").read_text(encoding="utf-8")
    age = (ROOT / "app" / "extension" / "vinted_age.js").read_text(encoding="utf-8")
    background = (ROOT / "app" / "extension" / "background.js").read_text(encoding="utf-8")
    manifest = json.loads((ROOT / "app" / "extension" / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["content_scripts"][0]["js"][:2] == ["vinted_age.js", "content.js"]
    assert 'LISTED_AT_CACHE_KEY="vintedListedAtCacheV3"' in content
    assert 'LISTING_PAGE_AGE_CACHE_KEY="vintedListingPageAgeCacheV2"' in content
    assert "globalThis.VintedAge" in age
    assert "function fromUploadedText(value)" in age
    assert "function fromRenderedDocument(doc = document)" in age
    assert "VintedAge.fromRenderedDocument(document)" in content
    assert "VintedAge.advanceCached(cached)" in content
    assert "function relativeAgeFromPageHtml" not in content
    assert "age_scan_items:ageScanItems" in content
    assert 'message?.type==="read-vinted-uploaded-age"' in content
    assert "async function renderedUploadedAges(items,workerCount=4)" in background
    assert 'AGE_SWEEP_QUEUE_KEY="vintedAgeSweepQueueV1"' in background
    assert 'api("/api/extension/listing-ages"' in background
    assert "async function processAgeSweepWindow()" in background
    assert "await chrome.tabs.create({url:first.url,active:false})" in background
    assert "await chrome.tabs.update(tab.id,{url:target.href,active:false})" in background
    assert "await chrome.tabs.remove(tab.id)" in background
    assert 'files:["vinted_age.js","content.js"]' in background
    assert "const ageScanItems=await enrichListingDates(listings);" in content


def test_bridge_reloads_stale_content_script_before_sync():
    content = (ROOT / "app" / "extension" / "content.js").read_text(encoding="utf-8")
    background = (ROOT / "app" / "extension" / "background.js").read_text(encoding="utf-8")
    assert "const BRIDGE_CONTENT_PROTOCOL=4;" in content
    assert 'message?.type==="bridge-content-protocol"' in content
    assert "const CONTENT_PROTOCOL=4;" in background
    assert "async function ensureCurrentContentScript(tab)" in background
    assert "await chrome.tabs.reload(tab.id)" in background
    assert "tab=await ensureCurrentContentScript(tab);" in background


def test_content_script_has_reinjection_guard():
    content = (ROOT / "app" / "extension" / "content.js").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "(() => {" in content
    assert "globalThis.__RESELLER_DASHBOARD_VINTED_CONTENT_PROTOCOL__ === 4" in content
    assert "globalThis.__RESELLER_DASHBOARD_VINTED_CONTENT_PROTOCOL__ = 4" in content
    assert content.rstrip().endswith("})();")
    assert "node scripts/test_vinted_content_idempotent.js" in workflow


def test_ci_does_not_commit_a_static_fernet_key():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "APP_ENCRYPTION_KEY:" not in workflow
    assert "Fernet.generate_key()" in workflow



def test_content_script_caches_rich_vinted_listing_details_for_cross_listing():
    content = (ROOT / "app" / "extension" / "content.js").read_text(encoding="utf-8")
    assert 'LISTING_DETAIL_CACHE_KEY="vintedListingDetailCacheV3"' in content
    assert "function enrichListingDetails(listings)" in content
    assert "image_urls:images" in content
    assert "await enrichListingDetails(listings);" in content
    assert '["active","reserved","hidden","draft"]' in content



def test_content_script_does_not_trust_generic_api_dates_for_posting_age():
    content = (ROOT / "app" / "extension" / "content.js").read_text(encoding="utf-8")
    age = (ROOT / "app" / "extension" / "vinted_age.js").read_text(encoding="utf-8")
    assert "function secondsFromRelative(value)" in age
    assert "relativeAgeFromRaw" not in content
    assert 'first(raw,"created_at_ts","created_timestamp_ts","uploaded_ts","upload_date_dte")' in content
    assert '"created_at","uploaded_at","posted_at"' not in content
    assert 'listed_age_seconds:null,listed_age_source:null,listed_age_text:null' in content



def test_bridge_artifact_filename_always_includes_version():
    target = Path("/tmp/reseller-chrome-bridge.zip")
    assert build_extension.versioned_output_path(target, "3.1.0").name == (
        "reseller-chrome-bridge-v3.1.0.zip"
    )
    already = Path("/tmp/reseller-chrome-bridge-v3.1.0.zip")
    assert build_extension.versioned_output_path(already, "3.1.0") == already


def test_bridge_popup_always_shows_manifest_version():
    html = (ROOT / "app" / "extension" / "popup.html").read_text(encoding="utf-8")
    js = (ROOT / "app" / "extension" / "popup.js").read_text(encoding="utf-8")
    assert 'id="bridge-version"' in html
    assert 'chrome.runtime.getManifest().version' in js
    assert '"Chrome Bridge v"+version' in js


def test_dashboard_download_uses_versioned_bridge_filename(monkeypatch):
    from fastapi.testclient import TestClient
    from app import entry

    monkeypatch.delenv("PUBLIC_APP_URL", raising=False)
    client = TestClient(entry.app)
    response = client.get("/downloads/reseller-chrome-bridge.zip")
    assert response.status_code == 200
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="reseller-dashboard-chrome-bridge-v3.1.0.zip"'
    )
    assert response.headers["x-bridge-version"] == "3.1.0"

    versioned = client.get("/downloads/reseller-chrome-bridge-v3.1.0.zip")
    assert versioned.status_code == 200
    assert "v3.1.0.zip" in versioned.headers["content-disposition"]

    wrong = client.get("/downloads/reseller-chrome-bridge-v0.0.1.zip")
    assert wrong.status_code == 404



def test_product_api_refuses_legacy_generic_vinted_relative_age():
    api = (ROOT / "app" / "product_api.py").read_text(encoding="utf-8")
    assert 'age_source.startswith("vinted_page")' in api
    assert "trusted_relative_age" in api



def test_dashboard_refuses_generic_vinted_relative_age_client_side():
    app = (ROOT / "app" / "product_static" / "app.js").read_text(encoding="utf-8")
    assert 'row?.channel === "vinted"' in app
    assert '!String(row?.listed_age_source || "").startsWith("vinted_page")' in app
