#!/usr/bin/env python3
"""Build and validate a deterministic Chrome extension ZIP."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "app" / "extension"
FIXED_TIME = (2020, 1, 1, 0, 0, 0)
TEXT_SUFFIXES = {".js", ".json", ".html", ".css", ".txt"}
FORBIDDEN_SUFFIXES = {".map", ".pem", ".key", ".env"}
STORE_PERMISSIONS = {"storage", "alarms", "scripting"}


def validate_origin(origin: str, *, store: bool) -> str:
    value = str(origin or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("API origin must be an absolute http(s) origin")
    if parsed.username or parsed.password:
        raise ValueError("API origin cannot contain credentials")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("API origin must not contain a path, query or fragment")
    host = parsed.hostname.lower()
    if store:
        if parsed.scheme != "https":
            raise ValueError("Store builds require an HTTPS API origin")
        if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
            raise ValueError("Store builds cannot target a local development host")
    return value


def permission_for(origin: str) -> str:
    parsed = urlparse(origin)
    host = parsed.hostname
    if not host:
        raise ValueError("API origin is missing a hostname")
    return f"{parsed.scheme}://{host}/*"


def validate_version(version: str) -> None:
    if not re.fullmatch(r"\d+(?:\.\d+){0,3}", version):
        raise ValueError("Chrome manifest version must contain 1-4 numeric components")
    components = version.split(".")
    if any(len(part) > 1 and part.startswith("0") for part in components):
        raise ValueError("Chrome manifest version components cannot contain leading zeroes")
    if any(int(part) > 65535 for part in components):
        raise ValueError("Chrome manifest version components must be at most 65535")


def _is_vinted_pattern(pattern: str) -> bool:
    return bool(re.fullmatch(r"https://www\.vinted\.[A-Za-z.]+/\*", str(pattern)))


def validate_manifest(manifest: dict, *, mode: str, api_origin: str) -> None:
    if manifest.get("manifest_version") != 3:
        raise ValueError("Chrome Web Store build must use Manifest V3")

    permissions = set(manifest.get("permissions") or [])
    if mode == "store" and permissions != STORE_PERMISSIONS:
        extra = sorted(permissions - STORE_PERMISSIONS)
        missing = sorted(STORE_PERMISSIONS - permissions)
        raise ValueError(
            "Store permissions must stay minimal"
            + (f"; unexpected: {', '.join(extra)}" if extra else "")
            + (f"; missing: {', '.join(missing)}" if missing else "")
        )

    host_permissions = list(manifest.get("host_permissions") or [])
    if "<all_urls>" in host_permissions:
        raise ValueError("Broad <all_urls> permission is not allowed")
    if permission_for(api_origin) not in host_permissions:
        raise ValueError("Manifest is missing the paired dashboard host permission")
    if mode == "store" and any(not str(value).startswith("https://") for value in host_permissions):
        raise ValueError("Store host permissions must all use HTTPS")

    scripts = manifest.get("content_scripts") or []
    for script in scripts:
        matches = script.get("matches") or []
        if not matches or any(not _is_vinted_pattern(value) for value in matches):
            raise ValueError("Content scripts may only run on explicit Vinted web origins")

    if manifest.get("homepage_url") != api_origin:
        raise ValueError("Extension homepage_url must be the paired dashboard origin")


def validate_package_files(staging: Path) -> list[Path]:
    files = [path for path in sorted(staging.rglob("*")) if path.is_file()]
    for path in files:
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            raise ValueError(f"Forbidden artifact in extension package: {path.name}")
        if path.suffix.lower() in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8")
            if "__API_ORIGIN__" in text or "__API_HOST_PERMISSION__" in text:
                raise ValueError(f"Unresolved build placeholder in {path.name}")
            if path.suffix.lower() == ".html" and re.search(
                r"<script[^>]+src=[\"']https?://", text, flags=re.IGNORECASE
            ):
                raise ValueError(f"Remote script is not allowed in {path.name}")
            if path.suffix.lower() == ".js" and re.search(
                r"\b(?:eval\s*\(|new\s+Function\s*\()", text
            ):
                raise ValueError(f"Dynamic code execution is not allowed in {path.name}")
    return files


def build(mode: str, api_origin: str, output: Path, version: str | None) -> Path:
    api_origin = validate_origin(api_origin, store=mode == "store")
    if mode == "store" and not version:
        raise ValueError("Store builds require an explicit --version")
    if version:
        validate_version(version)

    with tempfile.TemporaryDirectory() as temporary:
        staging = Path(temporary) / "extension"
        shutil.copytree(SOURCE, staging)
        replacements = {
            "__API_ORIGIN__": api_origin,
            "__API_HOST_PERMISSION__": permission_for(api_origin),
        }
        for path in sorted(staging.rglob("*")):
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
                text = path.read_text(encoding="utf-8")
                for old, new in replacements.items():
                    text = text.replace(old, new)
                path.write_text(text, encoding="utf-8")

        manifest_path = staging / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if version:
            manifest["version"] = version
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        validate_manifest(manifest, mode=mode, api_origin=api_origin)
        files = validate_package_files(staging)

        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                info = zipfile.ZipInfo(path.relative_to(staging).as_posix(), FIXED_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, path.read_bytes())
    return output


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("dev", "store"), required=True)
    parser.add_argument("--api-origin", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dist" / "chrome-bridge.zip",
    )
    parser.add_argument("--version")
    args = parser.parse_args()
    output = build(args.mode, args.api_origin, args.output, args.version)
    print(output)
    print(f"sha256={sha256(output)}")


if __name__ == "__main__":
    main()
