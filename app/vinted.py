from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests


class VintedError(RuntimeError):
    pass


class VintedAuthRequired(VintedError):
    pass


class VintedRateLimited(VintedError):
    pass


class VintedBlocked(VintedError):
    pass


@dataclass
class EndpointResult:
    path: str
    status: int | None
    ok: bool
    detail: str


def _first(mapping: dict[str, Any] | None, *keys: str) -> Any:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _id(value: Any) -> str | None:
    if isinstance(value, dict):
        value = _first(value, "id", "user_id")
    if value in (None, ""):
        return None
    return str(value)


def _username(value: Any) -> str | None:
    if isinstance(value, dict):
        value = _first(value, "login", "username", "name", "display_name")
    return str(value) if value not in (None, "") else None


def _money(value: Any) -> tuple[int | None, str]:
    currency = "EUR"
    if isinstance(value, dict):
        currency = str(_first(value, "currency_code", "currency", "code") or "EUR")
        value = _first(value, "amount", "value", "price")
    if value in (None, ""):
        return None, currency
    try:
        text = str(value).strip().replace("€", "").replace(" ", "")
        if "," in text and "." not in text:
            text = text.replace(",", ".")
        return round(float(text) * 100), currency
    except (TypeError, ValueError):
        return None, currency


def _timestamp(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        # Some Vinted payloads use seconds, others milliseconds.
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000
        try:
            from datetime import datetime, timezone

            return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError):
            return str(value)
    return str(value)


def _photo_url(item: dict[str, Any]) -> str | None:
    photo = _first(item, "photo", "primary_photo")
    if isinstance(photo, dict):
        direct = _first(photo, "full_size_url", "url")
        if direct:
            return str(direct)
        thumbs = photo.get("thumbnails")
        if isinstance(thumbs, list) and thumbs:
            for thumb in reversed(thumbs):
                if isinstance(thumb, dict) and thumb.get("url"):
                    return str(thumb["url"])
    photos = item.get("photos")
    if isinstance(photos, list) and photos:
        first = photos[0]
        if isinstance(first, dict):
            return str(_first(first, "full_size_url", "url") or "") or None
    return None


def _item_url(base_url: str, item: dict[str, Any]) -> str | None:
    direct = _first(item, "url", "web_url")
    if direct:
        text = str(direct)
        return text if text.startswith("http") else urljoin(base_url, text)
    item_id = _first(item, "id", "item_id")
    if item_id:
        return f"{base_url}/items/{item_id}"
    return None


def _listing_status(item: dict[str, Any]) -> str:
    raw = str(_first(item, "status", "state", "item_status") or "").lower().replace(" ", "_")
    if item.get("is_reserved"):
        return "reserved"
    if item.get("is_hidden"):
        return "hidden"
    if item.get("is_draft"):
        return "draft"
    if item.get("is_closed") or item.get("is_sold"):
        return "sold"
    if raw in {"sold", "closed", "reserved", "hidden", "draft", "active"}:
        return raw
    return raw or "active"


CLOSED_STATUS_WORDS = {
    "cancelled",
    "canceled",
    "completed",
    "complete",
    "closed",
    "finished",
    "refunded",
    "failed",
}


def is_closed_status(value: str | None) -> bool:
    status = (value or "").lower().replace("-", "_").replace(" ", "_")
    return any(word in status for word in CLOSED_STATUS_WORDS)


class VintedClient:
    def __init__(self) -> None:
        self.base_url = os.getenv("VINTED_BASE_URL", "https://www.vinted.pt").rstrip("/")
        self.user_id = str(os.getenv("VINTED_USER_ID", "58344842"))
        self.profile_url = os.getenv(
            "VINTED_PROFILE_URL", f"{self.base_url}/member/{self.user_id}"
        )
        self.username = os.getenv("VINTED_USERNAME", "tom_waits")
        self.timeout = int(os.getenv("VINTED_TIMEOUT_SECONDS", "20"))
        self.max_pages = int(os.getenv("VINTED_MAX_PAGES", "10"))
        self.session_file = Path("/app/data/vinted-session.cookie")
        self._lock = threading.Lock()
        self._session: requests.Session | None = None
        self._diagnostics: list[EndpointResult] = []
        self._orders_source: str | None = None

    @property
    def host(self) -> str:
        return urlparse(self.base_url).netloc

    def cookie_string(self) -> str:
        if self.session_file.exists():
            return self.session_file.read_text(encoding="utf-8").strip()
        env_cookie = os.getenv("VINTED_COOKIE", "").strip()
        if env_cookie:
            return env_cookie

        access = os.getenv("VINTED_ACCESS_TOKEN_WEB", "").strip()
        refresh = os.getenv("VINTED_REFRESH_TOKEN_WEB", "").strip()
        parts = []
        if access:
            parts.append(f"access_token_web={access}")
        if refresh:
            parts.append(f"refresh_token_web={refresh}")
        return "; ".join(parts)

    def has_auth(self) -> bool:
        cookie = self.cookie_string()
        return "access_token_web=" in cookie or "refresh_token_web=" in cookie

    def save_cookie(self, value: str) -> None:
        value = value.strip()
        if not value:
            raise ValueError("Cookie header is empty")
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.session_file.write_text(value, encoding="utf-8")
        self.session_file.chmod(0o600)
        self.reset_session()

    def clear_cookie(self) -> None:
        if self.session_file.exists():
            self.session_file.unlink()
        self.reset_session()

    def reset_session(self) -> None:
        with self._lock:
            if self._session is not None:
                self._session.close()
            self._session = None
            self._orders_source = None

    def _headers(self) -> dict[str, str]:
        user_agent = os.getenv("VINTED_USER_AGENT", "").strip() or (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
        )
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-GB,en;q=0.9,pt-PT;q=0.8,pt;q=0.7",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": user_agent,
            "Referer": self.base_url + "/",
            "X-Requested-With": "XMLHttpRequest",
        }
        cookie = self.cookie_string()
        if cookie:
            headers["Cookie"] = cookie
        anon = os.getenv("VINTED_ANON_ID", "").strip()
        if not anon and cookie:
            match = re.search(r"(?:^|;\\s*)anon_id=([^;]+)", cookie)
            if match:
                anon = match.group(1)
        if anon:
            headers["X-Anon-Id"] = anon
        return headers

    def _get_session(self) -> requests.Session:
        with self._lock:
            if self._session is None:
                session = requests.Session(impersonate="chrome")
                session.headers.update(self._headers())
                self._session = session
            return self._session

    def _request(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        auth: bool = False,
        allow_404: bool = False,
    ) -> Any:
        if auth and not self.has_auth():
            raise VintedAuthRequired(
                "Vinted session required for private account data"
            )

        url = path if path.startswith("http") else urljoin(self.base_url + "/", path.lstrip("/"))
        session = self._get_session()
        try:
            response = session.get(url, params=params, timeout=self.timeout)
        except Exception as exc:
            self._record(path, None, False, type(exc).__name__)
            raise VintedError(f"Vinted request failed: {exc}") from exc

        if response.status_code == 404 and allow_404:
            self._record(path, 404, False, "not found")
            return None
        if response.status_code in {401, 403}:
            detail = "authentication rejected" if auth else "request blocked"
            self._record(path, response.status_code, False, detail)
            if response.status_code == 401:
                raise VintedAuthRequired(
                    "Vinted rejected the saved session. Replace the Vinted cookie."
                )
            raise VintedBlocked(
                "Vinted returned 403. The session may be stale or Cloudflare may be blocking this request."
            )
        if response.status_code == 429:
            self._record(path, 429, False, "rate limited")
            raise VintedRateLimited("Vinted rate-limited the dashboard (HTTP 429).")
        if not response.ok:
            self._record(path, response.status_code, False, response.reason)
            raise VintedError(
                f"Vinted returned HTTP {response.status_code} for {path}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            self._record(path, response.status_code, False, "non-JSON response")
            raise VintedError(f"Vinted returned non-JSON data for {path}") from exc

        self._record(path, response.status_code, True, "ok")
        return payload

    def _record(self, path: str, status: int | None, ok: bool, detail: str) -> None:
        result = EndpointResult(path=path, status=status, ok=ok, detail=detail)
        self._diagnostics = [x for x in self._diagnostics if x.path != path]
        self._diagnostics.append(result)
        self._diagnostics = self._diagnostics[-20:]

    @staticmethod
    def _list_from(payload: Any, *keys: str) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if not isinstance(payload, dict):
            return []
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        # Some Vinted web endpoints wrap data once.
        data = payload.get("data")
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            for key in keys:
                value = data.get(key)
                if isinstance(value, list):
                    return [x for x in value if isinstance(x, dict)]
        return []

    def get_profile(self) -> dict[str, Any]:
        payload = self._request(f"/api/v2/users/{self.user_id}", params={"localize": "false"})
        raw = payload.get("user", payload) if isinstance(payload, dict) else {}
        return {
            "id": _id(raw) or self.user_id,
            "username": _username(raw) or self.username,
            "profile_url": self.profile_url,
            "photo_url": _photo_url(raw) if isinstance(raw, dict) else None,
            "feedback_reputation": _first(raw, "feedback_reputation", "rating"),
            "item_count": _first(raw, "item_count", "items_count"),
        }

    def get_current_user(self) -> dict[str, Any]:
        payload = self._request("/api/v2/users/current", auth=True)
        raw = payload.get("user", payload) if isinstance(payload, dict) else {}
        return {
            "id": _id(raw),
            "username": _username(raw),
        }

    def get_listings(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()

        for page in range(1, self.max_pages + 1):
            payload = self._request(
                f"/api/v2/users/{self.user_id}/items",
                params={
                    "order": "newest_first",
                    "page": page,
                    "per_page": 96,
                    "include_sold": "true",
                },
            )
            items = self._list_from(payload, "items", "user_items")
            if not items:
                break

            for raw in items:
                item_id = str(_first(raw, "id", "item_id") or "")
                if item_id and item_id in seen:
                    continue
                if item_id:
                    seen.add(item_id)
                price, currency = _money(
                    _first(raw, "price", "total_item_price", "item_price")
                )
                rows.append(
                    {
                        "id": item_id or None,
                        "title": str(_first(raw, "title", "name") or "Untitled"),
                        "price_cents": price,
                        "currency": currency,
                        "status": _listing_status(raw),
                        "isbn": _first(raw, "isbn"),
                        "vinted_url": _item_url(self.base_url, raw),
                        "image_url": _photo_url(raw),
                        "listed_at": _timestamp(
                            _first(raw, "created_at_ts", "created_at", "upload_date")
                        ),
                        "favourites": _first(
                            raw, "favourite_count", "favorites_count", "favourites_count"
                        ),
                        "views": _first(raw, "view_count", "views_count"),
                    }
                )

            pagination = payload.get("pagination") if isinstance(payload, dict) else None
            if isinstance(pagination, dict):
                total_pages = pagination.get("total_pages")
                if isinstance(total_pages, int) and page >= total_pages:
                    break
                if pagination.get("next_page") is None and len(items) < 96:
                    break
            elif len(items) < 96:
                break

        return rows

    def get_notifications(self) -> list[dict[str, Any]]:
        payload = self._request(
            "/web/api/notifications/notifications",
            params={"page": 1, "per_page": 100},
            auth=True,
        )
        raw_rows = self._list_from(payload, "notifications", "items", "entries")
        rows = []
        for raw in raw_rows:
            title = _first(raw, "title", "subject", "heading")
            body = _first(raw, "body", "text", "message", "description")
            if isinstance(body, dict):
                body = _first(body, "text", "value")
            link = _first(raw, "url", "link", "deep_link", "target_url")
            if link and not str(link).startswith("http"):
                link = urljoin(self.base_url, str(link))
            is_read = _first(raw, "is_read", "read", "seen")
            read_at = _first(raw, "read_at", "seen_at")
            rows.append(
                {
                    "id": str(_first(raw, "id", "notification_id") or ""),
                    "kind": str(_first(raw, "type", "notification_type", "event_type") or "notification"),
                    "title": str(title or body or "Vinted notification"),
                    "body": str(body or ""),
                    "occurred_at": _timestamp(
                        _first(raw, "created_at", "created_at_ts", "timestamp", "updated_at")
                    ),
                    "read": bool(is_read or read_at),
                    "url": str(link) if link else None,
                }
            )
        return rows

    def _get_threads(self) -> tuple[list[dict[str, Any]], str]:
        candidates = []
        if self._orders_source:
            candidates.append(self._orders_source)
        candidates.extend(
            [
                "/api/v2/conversations",
                f"/api/v2/users/{self.user_id}/msg_threads",
            ]
        )

        tried: set[str] = set()
        for path in candidates:
            if path in tried:
                continue
            tried.add(path)
            payload = self._request(
                path,
                params={"page": 1, "per_page": 100},
                auth=True,
                allow_404=True,
            )
            if payload is None:
                continue
            rows = self._list_from(
                payload,
                "conversations",
                "threads",
                "msg_threads",
                "items",
                "entries",
            )
            if rows or isinstance(payload, dict):
                self._orders_source = path
                return rows, path
        return [], "none"

    @staticmethod
    def _transaction_from_thread(thread: dict[str, Any]) -> dict[str, Any] | None:
        for key in (
            "transaction",
            "order",
            "transaction_summary",
            "transaction_details",
        ):
            value = thread.get(key)
            if isinstance(value, dict):
                return value

        # Some thread payloads flatten transaction fields.
        transaction_markers = {
            "transaction_id",
            "transaction_status",
            "buyer",
            "seller",
            "buyer_id",
            "seller_id",
        }
        if len(transaction_markers.intersection(thread.keys())) >= 2:
            return thread
        return None

    def _normalize_order(
        self,
        thread: dict[str, Any],
        transaction: dict[str, Any],
        current_user_id: str | None,
    ) -> dict[str, Any]:
        buyer = _first(transaction, "buyer") or _first(thread, "buyer")
        seller = _first(transaction, "seller") or _first(thread, "seller")
        buyer_id = _id(buyer) or _id(_first(transaction, "buyer_id"))
        seller_id = _id(seller) or _id(_first(transaction, "seller_id"))

        if current_user_id and seller_id == current_user_id:
            direction = "sell"
            counterparty = _username(buyer)
        elif current_user_id and buyer_id == current_user_id:
            direction = "buy"
            counterparty = _username(seller)
        elif transaction.get("is_seller") is True or thread.get("is_seller") is True:
            direction = "sell"
            counterparty = _username(buyer)
        elif transaction.get("is_buyer") is True or thread.get("is_buyer") is True:
            direction = "buy"
            counterparty = _username(seller)
        else:
            direction_hint = str(
                _first(transaction, "direction", "type", "role")
                or _first(thread, "direction", "type", "role")
                or ""
            ).lower()
            direction = "sell" if "sell" in direction_hint else "buy" if "buy" in direction_hint else "unknown"
            counterparty = _username(_first(thread, "opposite_user", "other_user"))

        item = (
            _first(transaction, "item")
            or _first(transaction, "listing")
            or _first(thread, "item")
            or _first(thread, "listing")
        )
        if not isinstance(item, dict):
            item = {}

        title = (
            _first(item, "title", "name")
            or _first(transaction, "title", "item_title", "order_title")
            or _first(thread, "title", "subject")
            or "Vinted order"
        )
        status = str(
            _first(
                transaction,
                "status",
                "status_title",
                "transaction_status",
                "state",
            )
            or _first(thread, "transaction_status", "status")
            or "open"
        ).lower().strip().replace(" ", "_").replace("-", "_")

        price, currency = _money(
            _first(
                transaction,
                "total_price",
                "total",
                "price",
                "item_price",
                "amount",
            )
            or _first(item, "price", "total_item_price")
        )

        order_id = _first(transaction, "id", "transaction_id", "order_id")
        if order_id is None:
            order_id = _first(thread, "transaction_id", "order_id", "id", "thread_id")

        thread_id = _first(thread, "id", "thread_id", "conversation_id")
        url = _first(thread, "url", "web_url")
        if not url and thread_id:
            url = f"{self.base_url}/inbox/{thread_id}"
        elif url and not str(url).startswith("http"):
            url = urljoin(self.base_url, str(url))

        return {
            "id": str(order_id or ""),
            "thread_id": str(thread_id or ""),
            "direction": direction,
            "title": str(title),
            "counterparty": counterparty,
            "total_cents": price,
            "currency": currency,
            "status": status,
            "is_closed": is_closed_status(status),
            "tracking_code": _first(
                transaction, "tracking_code", "tracking_number", "shipment_tracking_code"
            ),
            "updated_at": _timestamp(
                _first(transaction, "updated_at", "updated_at_ts", "created_at")
                or _first(thread, "updated_at", "last_message_at", "created_at")
            ),
            "vinted_url": str(url) if url else None,
        }

    def get_orders(self) -> tuple[list[dict[str, Any]], str]:
        current = self.get_current_user()
        current_user_id = current.get("id")
        threads, source = self._get_threads()
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()

        for thread in threads:
            transaction = self._transaction_from_thread(thread)
            if not transaction:
                continue
            row = self._normalize_order(thread, transaction, current_user_id)
            key = row["id"] or row["thread_id"]
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            rows.append(row)

        return rows, source

    def diagnostics(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "user_id": self.user_id,
            "auth_configured": self.has_auth(),
            "orders_source": self._orders_source,
            "endpoints": [
                {
                    "path": x.path,
                    "status": x.status,
                    "ok": x.ok,
                    "detail": x.detail,
                }
                for x in self._diagnostics
            ],
        }
