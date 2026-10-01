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
    build_extension.build("store", "https://dashboard.example", output, "3.0.0")
    manifest = publish_store.inspect_package(output)
    assert manifest["manifest_version"] == 3
    assert manifest["version"] == "3.0.0"

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
    assert manifest["version"] == "2.1.0"
