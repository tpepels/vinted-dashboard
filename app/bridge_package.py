"""Chrome bridge packaging and version helpers.

This module is the single source of truth for the paired bridge artifact served
by the dashboard. It intentionally knows nothing about HTTP routing or product
business logic.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException

EXTENSION_DIR = Path(__file__).resolve().parent / "extension"
_TEXT_SUFFIXES = {".js", ".json", ".html", ".txt", ".css"}


def extension_source_version() -> str:
    manifest_path = EXTENSION_DIR / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        version = str(manifest.get("version") or "").strip()
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        version = ""
    return version or "0.0.0"


def bridge_filename(version: str | None = None) -> str:
    resolved = str(version or extension_source_version()).strip() or "0.0.0"
    return f"reseller-dashboard-chrome-bridge-v{resolved}.zip"


def _zip_directory(
    directory: Path,
    replacements: dict[str, str] | None = None,
) -> bytes:
    if not directory.exists():
        raise HTTPException(status_code=404, detail="Extension files are not installed")
    replacements = replacements or {}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(directory)
            if path.suffix.lower() in _TEXT_SUFFIXES:
                text = path.read_text(encoding="utf-8")
                for old, new in replacements.items():
                    text = text.replace(old, new)
                archive.writestr(str(relative), text)
            else:
                archive.write(path, relative)
    return buffer.getvalue()


def paired_extension_zip(dashboard_url: str) -> bytes:
    origin = dashboard_url.rstrip("/")
    parsed = urlparse(origin)
    scheme = parsed.scheme or "http"
    host = parsed.hostname or "localhost"
    host_permission = f"{scheme}://{host}/*"
    return _zip_directory(
        EXTENSION_DIR,
        {
            "__API_ORIGIN__": origin,
            "__API_HOST_PERMISSION__": host_permission,
        },
    )
