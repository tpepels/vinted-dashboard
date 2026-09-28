from __future__ import annotations

import io
import json
import os
import time
import zipfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app import main as main_module


BROWSER_SYNC_FILE = Path("/app/data/vinted-browser-sync.json")
EXTENSION_DIR = Path(__file__).resolve().parent / "extension"
MAX_AGE_SECONDS = int(os.getenv("VINTED_BROWSER_SYNC_MAX_AGE_SECONDS", "1200"))


class BrowserSyncPayload(BaseModel):
    collected_at: float
    current_user: dict[str, Any]
    listings: list[dict[str, Any]]
    notifications: list[dict[str, Any]]
    orders: list[dict[str, Any]]


def _save_snapshot(payload: BrowserSyncPayload) -> None:
    BROWSER_SYNC_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = BROWSER_SYNC_FILE.with_suffix(".tmp")
    temp.write_text(
        json.dumps(payload.model_dump(), ensure_ascii=False),
        encoding="utf-8",
    )
    temp.chmod(0o600)
    temp.replace(BROWSER_SYNC_FILE)


def _load_snapshot(*, allow_stale: bool = False) -> dict[str, Any] | None:
    if not BROWSER_SYNC_FILE.exists():
        return None
    try:
        data = json.loads(BROWSER_SYNC_FILE.read_text(encoding="utf-8"))
        collected_at = float(data.get("collected_at") or 0)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not allow_stale and time.time() - collected_at > MAX_AGE_SECONDS:
        return None
    return data


class BrowserSyncClient:
    def __init__(self, fallback: Any) -> None:
        self._fallback = fallback

    def __getattr__(self, name: str) -> Any:
        return getattr(self._fallback, name)

    def has_auth(self) -> bool:
        return bool(_load_snapshot()) or self._fallback.has_auth()

    def has_refresh_token(self) -> bool:
        return bool(_load_snapshot()) or self._fallback.has_refresh_token()

    def get_current_user(self) -> dict[str, Any]:
        snapshot = _load_snapshot()
        if snapshot:
            return dict(snapshot.get("current_user") or {})
        return self._fallback.get_current_user()

    def get_listings(self) -> list[dict[str, Any]]:
        snapshot = _load_snapshot()
        if snapshot:
            return list(snapshot.get("listings") or [])
        return self._fallback.get_listings()

    def get_notifications(self) -> list[dict[str, Any]]:
        snapshot = _load_snapshot()
        if snapshot:
            return list(snapshot.get("notifications") or [])
        return self._fallback.get_notifications()

    def get_orders(self) -> tuple[list[dict[str, Any]], str]:
        snapshot = _load_snapshot()
        if snapshot:
            return list(snapshot.get("orders") or []), "Chrome session sync"
        return self._fallback.get_orders()


_original_client = main_module.client
main_module.client = BrowserSyncClient(_original_client)

_original_dashboard_data = main_module.dashboard_data


def _dashboard_data(force: bool = False) -> dict[str, Any]:
    data = _original_dashboard_data(force=force)
    snapshot = _load_snapshot()
    data["browser_sync"] = {
        "active": bool(snapshot),
        "collected_at": (snapshot or {}).get("collected_at"),
        "max_age_seconds": MAX_AGE_SECONDS,
    }
    return data


main_module.dashboard_data = _dashboard_data
app = main_module.app


@app.post("/api/browser-sync")
def browser_sync(payload: BrowserSyncPayload):
    _save_snapshot(payload)
    main_module.clear_cache()
    return {
        "ok": True,
        "collected_at": payload.collected_at,
        "listings": len(payload.listings),
        "notifications": len(payload.notifications),
        "orders": len(payload.orders),
    }


@app.get("/api/browser-sync/status")
def browser_sync_status():
    current = _load_snapshot()
    stale = _load_snapshot(allow_stale=True)
    return {
        "active": bool(current),
        "collected_at": (stale or {}).get("collected_at"),
        "max_age_seconds": MAX_AGE_SECONDS,
    }


@app.get("/downloads/vinted-session-sync.zip")
def download_extension():
    if not EXTENSION_DIR.exists():
        raise HTTPException(status_code=404, detail="Extension files are not installed")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(EXTENSION_DIR.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(EXTENSION_DIR))

    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="vinted-dashboard-session-sync.zip"'
        },
    )
