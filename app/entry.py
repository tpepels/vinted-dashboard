"""Production web application composition root.

Only the workspace-scoped product API is mounted here. Legacy data migration is
an offline/startup concern handled by :mod:`app.legacy_migration`, not a second
runtime API surface.
"""

from __future__ import annotations

import os
from html import escape
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from app.bridge_api import router as bridge_router
from app.bridge_package import (
    bridge_filename,
    extension_source_version,
    paired_extension_zip,
)
from app.product_api import router as product_router
from app.runtime_config import (
    database_readiness,
    is_production,
    privacy_contact_email,
    public_app_origin,
    safe_runtime_summary,
)


BASE_DIR = Path(__file__).resolve().parent
APP_NAME = os.getenv("APP_NAME", "Reseller Dashboard").strip() or "Reseller Dashboard"

app = FastAPI(title=APP_NAME, version="1.0.0")
app.mount(
    "/app-static",
    StaticFiles(directory=BASE_DIR / "product_static"),
    name="app-static",
)
app.include_router(product_router)
app.include_router(bridge_router)


@app.middleware("http")
async def production_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(self), microphone=(), geolocation=()",
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


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "product_static" / "index.html")


@app.get("/privacy")
def privacy():
    template = (BASE_DIR / "product_static" / "privacy.html").read_text(
        encoding="utf-8"
    )
    contact = privacy_contact_email()
    if contact:
        safe = escape(contact)
        replacement = f'<a href="mailto:{safe}">{safe}</a>'
    else:
        replacement = "the administrator of this deployment"
    return HTMLResponse(template.replace("__PRIVACY_CONTACT__", replacement))


@app.get("/api/health")
def health():
    return {"ok": True, "app": APP_NAME}


@app.get("/api/ready")
def ready():
    try:
        result = database_readiness()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Service is not ready") from exc
    return {"ok": True, **result}


def _bridge_download_response(request: Request) -> Response:
    dashboard_url = public_app_origin() or str(request.base_url).rstrip("/")
    version = extension_source_version()
    try:
        content = paired_extension_zip(dashboard_url)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Extension files are not installed") from exc
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{bridge_filename(version)}"',
            "X-Bridge-Version": version,
        },
    )


@app.get("/downloads/reseller-chrome-bridge.zip")
def download_paired_extension(request: Request):
    """Stable compatibility URL; the downloaded filename remains versioned."""
    return _bridge_download_response(request)


@app.get("/downloads/reseller-chrome-bridge-v{version}.zip")
def download_versioned_paired_extension(version: str, request: Request):
    current = extension_source_version()
    if version != current:
        raise HTTPException(
            status_code=404,
            detail=f"Bridge v{version} is not the installed bridge; current is v{current}.",
        )
    return _bridge_download_response(request)


@app.get("/api/runtime")
def runtime_summary():
    """Public, secret-free deployment posture for smoke checks."""
    result = safe_runtime_summary()
    try:
        from app.service_status import status as service_status

        result["worker"] = service_status(
            "worker",
            max_age_seconds=max(
                30,
                int(os.getenv("WORKER_HEARTBEAT_MAX_AGE_SECONDS", "90")),
            ),
        )
    except Exception:
        result["worker"] = {
            "service": "worker",
            "healthy": False,
            "last_seen_at": None,
            "age_seconds": None,
        }
    return result
