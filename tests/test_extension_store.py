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
    build_extension.build("store", "https://dashboard.example", output, "3.2.0")
    manifest = publish_store.inspect_package(output)
    assert manifest["manifest_version"] == 3
    assert manifest["version"] == "3.2.0"

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
    assert manifest["version"] == "3.2.0"



def test_content_script_uses_resumable_finite_rendered_uploaded_age_job():
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
    assert 'AGE_JOB_KEY="vintedAgeBurstJobV2"' in background
    assert "const AGE_WORKERS=4;" in background
    assert "const AGE_WAVES_PER_EVENT=8;" in background
    assert 'AGE_FAILURES_KEY="vintedAgeScanFailuresV1"' in background
    assert 'api("/api/extension/listing-ages"' in background
    assert "async function startAgeJob(items,reason)" in background
    assert "async function processAgeJobWave()" in background
    assert "async function renderedUploadedAgeWave(job,batch)" in background
    assert "const AGE_NAVIGATION_MIN_INTERVAL_MS=1800;" in background
    assert "const AGE_RATE_LIMIT_BASE_COOLDOWN_MS=30*60*1000;" in background
    assert "async function waitForAgeNavigationSlot()" in background
    assert "let ageNavigationChain=Promise.resolve();" in background
    assert 'url:Array.from({length:batch.length},()=>"about:blank")' in background
    assert "chrome.windows.create({" in background
    assert 'state:"minimized"' in background
    assert "await chrome.tabs.update(tab.id,{url:target.href,active:false})" in background
    assert "await closeAgeWorkerWindow(job)" in background
    assert "AGE_SWEEP_ALARM" not in background
    assert 'files:["vinted_age.js","content.js"]' in background
    assert "const ageScanItems=await enrichListingDates(listings);" in content


def test_bridge_reloads_stale_content_script_before_sync():
    content = (ROOT / "app" / "extension" / "content.js").read_text(encoding="utf-8")
    background = (ROOT / "app" / "extension" / "background.js").read_text(encoding="utf-8")
    assert "const BRIDGE_CONTENT_PROTOCOL=7;" in content
    assert 'message?.type==="bridge-content-protocol"' in content
    assert "const CONTENT_PROTOCOL=7;" in background
    assert "async function ensureCurrentContentScript(tab)" in background
    assert "await chrome.tabs.reload(tab.id)" in background
    assert "tab=await ensureCurrentContentScript(tab);" in background


def test_content_script_has_reinjection_guard():
    content = (ROOT / "app" / "extension" / "content.js").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "(() => {" in content
    assert "globalThis.__RESELLER_DASHBOARD_VINTED_CONTENT_PROTOCOL__ === 7" in content
    assert "globalThis.__RESELLER_DASHBOARD_VINTED_CONTENT_PROTOCOL__ = 7" in content
    assert content.rstrip().endswith("})();")
    assert "node scripts/test_vinted_content_idempotent.js" in workflow


def test_ci_does_not_commit_a_static_fernet_key():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "APP_ENCRYPTION_KEY:" not in workflow
    assert "Fernet.generate_key()" in workflow



def test_content_script_caches_rich_vinted_listing_details_for_cross_listing():
    content = (ROOT / "app" / "extension" / "content.js").read_text(encoding="utf-8")
    assert 'LISTING_DETAIL_CACHE_KEY="vintedListingDetailCacheV4"' in content
    assert 'function enrichListingDetails(listings,reason="periodic")' in content
    assert "image_urls:images" in content
    assert ": await enrichListingDetails(listings,reason);" in content
    assert 'const budget=reason==="manual"?12:4;' in content
    assert '{minDelayMs:1500,maxRetries:1}' in content
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
    assert build_extension.versioned_output_path(target, "3.2.0").name == (
        "reseller-chrome-bridge-v3.2.0.zip"
    )
    already = Path("/tmp/reseller-chrome-bridge-v3.2.0.zip")
    assert build_extension.versioned_output_path(already, "3.2.0") == already


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
        == 'attachment; filename="reseller-dashboard-chrome-bridge-v3.2.0.zip"'
    )
    assert response.headers["x-bridge-version"] == "3.2.0"

    versioned = client.get("/downloads/reseller-chrome-bridge-v3.2.0.zip")
    assert versioned.status_code == 200
    assert "v3.2.0.zip" in versioned.headers["content-disposition"]

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



def test_vinted_detail_parser_prefers_direct_category_and_description_fields():
    content = (ROOT / "app" / "extension" / "content.js").read_text(encoding="utf-8")
    assert 'description:metaText(first(raw,"description","item_description","itemDescription"))' in content
    assert 'category:metaText(first(raw,"catalog_title","category_title","category_name","catalog","category","catalogs"))' in content


def test_bridge_age_scan_is_finite_resumable_and_failures_have_cooldown():
    background = (ROOT / "app" / "extension" / "background.js").read_text(encoding="utf-8")
    assert "const AGE_WORKERS=4;" in background
    assert "const AGE_WAVES_PER_EVENT=8;" in background
    assert "const AGE_FAILURE_COOLDOWN_MS=24*60*60*1000;" in background
    assert 'if(reason==="manual")return clean;' in background
    assert "job.remaining=job.remaining.slice(batch.length);" in background
    assert "await chrome.storage.local.set({[AGE_JOB_KEY]:job});" in background
    assert "chrome.alarms.create(AGE_JOB_ALARM" in background
    assert "sync_skipped_for_age_job:true" in background
    assert "AGE_SWEEP_ALARM" not in background


def test_vinted_fetches_are_paced_and_rate_limit_safe():
    content = (ROOT / "app" / "extension" / "content.js").read_text(encoding="utf-8")
    assert "let lastVintedFetchAt=0;" in content
    assert "function rateLimitDelay(response,attempt)" in content
    assert "Vinted rate limited the sync." in content
    assert "error.vintedRateLimited=true" in content
    assert 'if(status==="active")throw error;' in content
    assert "secondaryRateLimited=true" in content
    assert 'const budget=reason==="manual"?12:4;' in content
    assert "if(!detailSync.rate_limited)" in content



def test_vinted_age_worker_detects_site_rate_limit_and_pauses():
    content = (ROOT / "app" / "extension" / "content.js").read_text(encoding="utf-8")
    background = (ROOT / "app" / "extension" / "background.js").read_text(encoding="utf-8")
    assert "function renderedPageAccessState()" in content
    assert '"you are rate limited"' in content
    assert '"too many requests"' in content
    assert "rate_limited:Boolean(result?.rate_limited)" in background
    assert "AGE_RATE_LIMIT_BASE_COOLDOWN_MS" in background
    assert "job.cooldown_until=Date.now()+cooldown" in background
    assert "await closeAgeWorkerWindow(job)" in background
