#!/usr/bin/env python3
"""Safely update the *existing* unpacked Chrome bridge without changing its ID.

First use:
  python scripts/update_chrome_bridge.py ~/Downloads/reseller-dashboard-chrome-bridge-v3.4.16.zip --extension-dir /path/to/current/extension
Later:
  python scripts/update_chrome_bridge.py ~/Downloads/reseller-dashboard-chrome-bridge-vNEXT.zip

Chrome stores the pairing token in chrome.storage.local, keyed by extension ID.
Replacing files in the SAME installed directory keeps that ID and its storage.
Never remove the existing Chrome extension or use Load unpacked again.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import tempfile
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

EXPECTED_NAME = "Reseller Dashboard Chrome Bridge"
REQUIRED_FILES = {"manifest.json", "background.js", "content.js", "popup.html", "popup.js"}
MAX_ZIP_BYTES = 25 * 1024 * 1024
MAX_FILES = 100


def remembered_dir() -> Path:
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config / "vinted-dashboard" / "chrome-bridge-folder.json"


def _read_manifest(data: bytes) -> dict:
    manifest = json.loads(data)
    if not isinstance(manifest, dict) or manifest.get("name") != EXPECTED_NAME:
        raise ValueError("The folder or ZIP is not the Reseller Dashboard Chrome Bridge.")
    if manifest.get("manifest_version") != 3:
        raise ValueError("Expected a Chrome Manifest V3 extension.")
    return manifest


def _version(v: object) -> tuple[int, ...]:
    parts = str(v or "").split(".")
    if not 1 <= len(parts) <= 4 or any(not p.isdecimal() for p in parts):
        raise ValueError("Invalid Chrome bridge version.")
    return tuple(int(x) for x in parts) + (0,) * (4 - len(parts))


def update_extension(archive_path: Path, extension_dir: Path) -> tuple[str, str]:
    """Only replace files inside the original Chrome-loaded directory.

    Do not touch the Chrome profile or pairing data. Reject foreign/malicious
    packages before writing anything, and never follow destination symlinks.
    """
    extension_dir = extension_dir.expanduser().absolute()
    archive_path = archive_path.expanduser().absolute()
    if not extension_dir.is_dir() or extension_dir.is_symlink():
        raise ValueError("Installed extension directory does not exist or is a symlink.")
    manifest_path = extension_dir / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("Choose the EXISTING unpacked extension folder containing manifest.json.")
    installed = _read_manifest(manifest_path.read_bytes())

    with ZipFile(archive_path) as archive:
        names: set[str] = set()
        contents: dict[str, bytes] = {}
        if len(archive.infolist()) > MAX_FILES:
            raise ValueError("Unexpected number of files in extension ZIP.")
        if sum(item.file_size for item in archive.infolist()) > MAX_ZIP_BYTES:
            raise ValueError("Extension ZIP is unexpectedly large.")
        for item in archive.infolist():
            raw = item.filename
            relative = PurePosixPath(raw)
            if (raw.startswith("/") or "\\" in raw or
                    not relative.parts or ".." in relative.parts):
                raise ValueError("Unsafe file path in extension ZIP.")
            if stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError("Extension ZIP must not contain symlinks.")
            if item.is_dir():
                continue
            name = relative.as_posix()
            if name in names:
                raise ValueError("Duplicate file in extension ZIP.")
            names.add(name)
            contents[name] = archive.read(item)
    if not REQUIRED_FILES.issubset(contents):
        raise ValueError("Extension ZIP is missing required files.")
    new = _read_manifest(contents["manifest.json"])
    if new.get("homepage_url") != installed.get("homepage_url"):
        raise ValueError("Extension ZIP belongs to a different dashboard. Pairing cannot be reused.")
    if _version(new.get("version")) < _version(installed.get("version")):
        raise ValueError("Refusing to install an older bridge version.")

    for name in contents:
        dest = extension_dir / name
        if any(part.is_symlink() for part in (dest, *dest.parents) if part != extension_dir and extension_dir in part.parents):
            raise ValueError("Installed extension contains a symlink at " + name)
    # Files are fully validated before the first write. Atomic file replaces
    # avoid exposing half-written JavaScript while Chrome remains open.
    for name, blob in contents.items():
        dest = extension_dir / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=dest.parent, prefix=".bridge-update-", delete=False) as tmp:
            tmp.write(blob)
            temporary = Path(tmp.name)
        try:
            temporary.chmod(0o644)
            temporary.replace(dest)
        finally:
            temporary.unlink(missing_ok=True)
    return str(installed.get("version")), str(new.get("version"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Update Chrome Bridge in place, preserving its existing pairing.")
    parser.add_argument("zip", type=Path, help="New ZIP downloaded from Connections → Vinted")
    parser.add_argument("--extension-dir", type=Path, help="Original folder already loaded in chrome://extensions (needed once)")
    args = parser.parse_args()

    config = remembered_dir()
    if args.extension_dir is not None:
        folder = args.extension_dir.expanduser().absolute()
    elif config.exists():
        folder = Path(json.loads(config.read_text(encoding="utf-8"))["extension_dir"])
    else:
        parser.error("Specify --extension-dir /path/to/the/CURRENT/extension on the first update.")
    old, new = update_extension(args.zip, folder)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"extension_dir": str(folder)}) + "\n", encoding="utf-8")
    config.chmod(0o600)
    print(f"Chrome Bridge updated from v{old} to v{new} IN PLACE: {folder}")
    print("Open chrome://extensions and click Reload for the existing bridge.")
    print("Do NOT remove the extension or use Load unpacked. Your saved pairing is unchanged.")


if __name__ == "__main__":
    main()
