from __future__ import annotations

import io
import json
import os
import time
import zipfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app import main as main_module
from app.product_api import router as product_router
from app.runtime_config import (
    is_production,
    public_app_origin,
    safe_runtime_summary,
)
from app.intelligence import (
    intelligence_payload,
    queued_market_jobs,
    record_snapshot,
    request_market_research,
)
from app.channels import (
    biblio_ftp_status,
    channel_inventory_payload,
    import_biblio_inventory,
    preview_biblio_ftp_sync,
    record_vinted_items,
    sync_biblio_ftp,
    sync_ebay_inventory,
    test_biblio_ftp,
)


BROWSER_SYNC_FILE = Path("/app/data/vinted-browser-sync.json")
EXTENSION_DIR = Path(__file__).resolve().parent / "extension"
LEGACY_EXTENSION_DIR = Path(__file__).resolve().parent / "legacy_extension"
MAX_AGE_SECONDS = int(os.getenv("VINTED_BROWSER_SYNC_MAX_AGE_SECONDS", "1200"))


class BrowserSyncPayload(BaseModel):
    collected_at: float
    current_user: dict[str, Any]
    listings: list[dict[str, Any]]
    notifications: list[dict[str, Any]]
    orders: list[dict[str, Any]]
    market_results: list[dict[str, Any]] = Field(default_factory=list)


class MarketResearchRequest(BaseModel):
    listing_id: str
    title: str


class BiblioImportRequest(BaseModel):
    filename: str | None = None
    content: str


def _save_snapshot(payload: BrowserSyncPayload) -> None:
    data = payload.model_dump()
    BROWSER_SYNC_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = BROWSER_SYNC_FILE.with_suffix(".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False),
        encoding="utf-8",
    )
    temp.chmod(0o600)
    temp.replace(BROWSER_SYNC_FILE)
    record_snapshot(data)
    record_vinted_items(list(data.get("listings") or []), float(data.get("collected_at") or time.time()))


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
app.include_router(product_router)


@app.middleware("http")
async def production_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=()",
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; connect-src 'self'; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'",
    )
    if is_production():
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
    return response

LEGACY_API_ENABLED = os.getenv("LEGACY_API_ENABLED", "false").strip().lower() in {
    "1", "true", "yes", "on"
}
LEGACY_PREFIXES = (
    "/api/dashboard",
    "/api/refresh",
    "/api/diagnostics",
    "/api/session",
    "/api/browser-sync",
    "/api/intelligence",
    "/api/market-research",
    "/api/channels",
    "/downloads/vinted-session-sync.zip",
)


@app.middleware("http")
async def legacy_api_gate(request: Request, call_next):
    if not LEGACY_API_ENABLED and any(
        request.url.path == prefix or request.url.path.startswith(prefix + "/")
        for prefix in LEGACY_PREFIXES
    ):
        return Response(status_code=404)
    return await call_next(request)


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


@app.get("/api/intelligence")
def intelligence():
    snapshot = _load_snapshot(allow_stale=True) or {}
    listings = list(snapshot.get("listings") or [])
    if not listings:
        try:
            listings = main_module.client.get_listings()
        except Exception:
            listings = []
    return intelligence_payload(listings)


@app.post("/api/market-research/request")
def market_research_request(payload: MarketResearchRequest):
    try:
        result = request_market_research(payload.listing_id, payload.title)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "job": result}


@app.get("/api/market-research/queue")
def market_research_queue(limit: int = 3):
    return {"jobs": queued_market_jobs(limit)}


@app.get("/api/channels")
def channels():
    return channel_inventory_payload()


@app.post("/api/channels/biblio/import")
def biblio_import(payload: BiblioImportRequest):
    try:
        result = import_biblio_inventory(payload.content, payload.filename)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


@app.get("/api/channels/biblio/ftp/preview")
def biblio_ftp_preview():
    return {
        "status": biblio_ftp_status(),
        "preview": preview_biblio_ftp_sync(),
    }


@app.post("/api/channels/biblio/ftp/test")
def biblio_ftp_test():
    try:
        return test_biblio_ftp()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/channels/biblio/ftp/sync")
def biblio_ftp_sync():
    try:
        return sync_biblio_ftp()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/channels/ebay/sync")
def ebay_sync():
    try:
        result = sync_ebay_inventory()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


@app.get("/api/browser-sync/status")
def browser_sync_status():
    current = _load_snapshot()
    stale = _load_snapshot(allow_stale=True)
    return {
        "active": bool(current),
        "collected_at": (stale or {}).get("collected_at"),
        "max_age_seconds": MAX_AGE_SECONDS,
    }


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
            if path.suffix.lower() in {".js", ".json", ".html", ".txt", ".css"}:
                text = path.read_text(encoding="utf-8")
                for old, new in replacements.items():
                    text = text.replace(old, new)
                archive.writestr(str(relative), text)
            else:
                archive.write(path, relative)
    return buffer.getvalue()


def _legacy_extension_zip(dashboard_url: str) -> bytes:
    from urllib.parse import urlparse

    origin = dashboard_url.rstrip("/")
    parsed = urlparse(origin)
    host = parsed.hostname or "media-server"
    dashboard_pattern = f"{parsed.scheme or 'http'}://{host}/*"
    return _zip_directory(
        LEGACY_EXTENSION_DIR,
        {
            "http://media-server:5050": origin,
            "http://media-server/*": dashboard_pattern,
        },
    )


# Backwards-compatible helper used by the existing personal-dashboard tests
# and any local tooling that imported it directly.
_extension_zip = _legacy_extension_zip


def _paired_extension_zip(dashboard_url: str) -> bytes:
    from urllib.parse import urlparse

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


@app.get("/downloads/vinted-session-sync.zip")
def download_legacy_extension(request: Request):
    dashboard_url = str(request.base_url).rstrip("/")
    return Response(
        content=_legacy_extension_zip(dashboard_url),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="vinted-dashboard-session-sync.zip"'
        },
    )


@app.get("/downloads/reseller-chrome-bridge.zip")
def download_paired_extension(request: Request):
    dashboard_url = public_app_origin() or str(request.base_url).rstrip("/")
    return Response(
        content=_paired_extension_zip(dashboard_url),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="reseller-dashboard-chrome-bridge.zip"'
        },
    )


@app.get("/api/runtime")
def runtime_summary():
    """Public, secret-free deployment posture for smoke checks."""
    return safe_runtime_summary()
