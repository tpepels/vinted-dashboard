from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.vinted import (
    VintedAuthRequired,
    VintedBlocked,
    VintedClient,
    VintedError,
    VintedRateLimited,
    is_closed_status,
)


BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Vinted Dashboard", version="0.2.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

client = VintedClient()
CACHE_SECONDS = int(os.getenv("VINTED_CACHE_SECONDS", "60"))
_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "value": None}


class SessionCookie(BaseModel):
    cookie: str


def _error_text(exc: Exception) -> str:
    if isinstance(exc, VintedAuthRequired):
        return str(exc)
    if isinstance(exc, VintedRateLimited):
        return str(exc)
    if isinstance(exc, VintedBlocked):
        return str(exc)
    if isinstance(exc, VintedError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def _dashboard_uncached() -> dict[str, Any]:
    errors: list[str] = []

    try:
        profile = client.get_profile()
    except Exception as exc:
        profile = {
            "id": client.user_id,
            "username": client.username,
            "profile_url": client.profile_url,
        }
        errors.append(f"Profile: {_error_text(exc)}")

    try:
        listings = client.get_listings()
    except Exception as exc:
        listings = []
        errors.append(f"Listings: {_error_text(exc)}")

    authenticated = False
    current_user = None
    notifications: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    orders_source = None

    if client.has_auth():
        try:
            current_user = client.get_current_user()
            authenticated = bool(current_user.get("id"))
        except Exception as exc:
            errors.append(f"Session: {_error_text(exc)}")

        if authenticated:
            try:
                notifications = client.get_notifications()
            except Exception as exc:
                errors.append(f"Notifications: {_error_text(exc)}")
            try:
                orders, orders_source = client.get_orders()
            except Exception as exc:
                errors.append(f"Orders: {_error_text(exc)}")

    sales = [x for x in orders if x.get("direction") == "sell"]
    purchases = [x for x in orders if x.get("direction") == "buy"]
    unknown_orders = [x for x in orders if x.get("direction") == "unknown"]

    open_sales = [x for x in sales if not x.get("is_closed")]
    open_purchases = [x for x in purchases if not x.get("is_closed")]
    unread = [x for x in notifications if not x.get("read")]

    attention_statuses = {
        "awaiting_shipment",
        "label_ready",
        "ready_for_pickup",
        "confirmation_needed",
        "pending",
        "payment_pending",
        "needs_action",
    }
    attention = [
        x
        for x in orders
        if not x.get("is_closed")
        and (
            x.get("status") in attention_statuses
            or "ship" in str(x.get("status", ""))
            or "pickup" in str(x.get("status", ""))
            or "confirm" in str(x.get("status", ""))
        )
    ][:10]

    expected_user = str(client.user_id)
    connected_user = str((current_user or {}).get("id") or "")
    if authenticated and connected_user and connected_user != expected_user:
        errors.append(
            f"Authenticated Vinted user is {connected_user}, but dashboard profile is {expected_user}."
        )

    return {
        "fetched_at": time.time(),
        "profile": profile,
        "auth": {
            "configured": client.has_auth(),
            "authenticated": authenticated,
            "current_user": current_user,
        },
        "summary": {
            "active_listings": sum(
                1 for x in listings if x.get("status") not in {"sold", "closed"}
            ),
            "open_sales": len(open_sales),
            "open_purchases": len(open_purchases),
            "unread_notifications": len(unread),
            "total_listings": len(listings),
        },
        "listings": listings,
        "sales": sales,
        "purchases": purchases,
        "unknown_orders": unknown_orders,
        "notifications": notifications,
        "attention": attention,
        "orders_source": orders_source,
        "errors": errors,
    }


def dashboard_data(force: bool = False) -> dict[str, Any]:
    now = time.monotonic()
    with _cache_lock:
        if (
            not force
            and _cache["value"] is not None
            and now - float(_cache["at"]) < CACHE_SECONDS
        ):
            return _cache["value"]

    value = _dashboard_uncached()
    with _cache_lock:
        _cache["at"] = time.monotonic()
        _cache["value"] = value
    return value


def clear_cache() -> None:
    with _cache_lock:
        _cache["at"] = 0.0
        _cache["value"] = None


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "source": "vinted"}


@app.get("/api/dashboard")
def dashboard(refresh: bool = False):
    return dashboard_data(force=refresh)


@app.post("/api/refresh")
def refresh():
    clear_cache()
    return dashboard_data(force=True)


@app.get("/api/diagnostics")
def diagnostics():
    return client.diagnostics()


@app.get("/api/session")
def session_status():
    result = {
        "configured": client.has_auth(),
        "authenticated": False,
        "current_user": None,
    }
    if not client.has_auth():
        return result
    try:
        current = client.get_current_user()
        result["authenticated"] = bool(current.get("id"))
        result["current_user"] = current
    except Exception as exc:
        result["error"] = _error_text(exc)
    return result


@app.post("/api/session")
def set_session(payload: SessionCookie):
    try:
        client.save_cookie(payload.cookie)
        clear_cache()
        current = client.get_current_user()
        return {
            "configured": True,
            "authenticated": bool(current.get("id")),
            "current_user": current,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except VintedAuthRequired as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except VintedBlocked as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except VintedRateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except VintedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.delete("/api/session")
def delete_session():
    client.clear_cookie()
    clear_cache()
    return {"configured": False, "authenticated": False}
