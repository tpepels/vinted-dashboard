from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from curl_cffi import requests


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


def _parse_cookie_header(value: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in (value or "").split(";"):
        key, sep, item = part.strip().partition("=")
        if sep and key:
            cookies[key.strip()] = item.strip()
    return cookies


def _extract_csrf_token(html: str) -> str | None:
    match = re.search(
        r'<meta(?=[^>]*\bname=["\']csrf[-_]?token["\'])'
        r'(?=[^>]*\bcontent=["\']([^"\']+))[^>]*>',
        html or "",
        flags=re.IGNORECASE | re.DOTALL,
    )
    return unescape(match.group(1)) if match else None


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

    numeric: float | None = None
    if isinstance(value, (int, float)):
        numeric = float(value)
    elif isinstance(value, str):
        text = value.strip()
        try:
            numeric = float(text)
        except ValueError:
            numeric = None

    if numeric is not None:
        # Some Vinted payloads use seconds, others milliseconds, and several
        # endpoints serialize the Unix timestamp as a string.
        seconds = numeric
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
    # Vinted's generic "status" field is item condition (for example
    # "Very good"), not listing lifecycle. Lifecycle is represented by
    # boolean flags and, on some payloads, explicit state/item_status fields.
    if item.get("is_reserved"):
        return "reserved"
    if item.get("is_hidden"):
        return "hidden"
    if item.get("is_draft"):
        return "draft"
    if item.get("is_closed") or item.get("is_sold"):
        return "sold"

    raw = str(
        _first(item, "state", "item_status", "listing_status") or ""
    ).lower().strip().replace("-", "_").replace(" ", "_")
    aliases = {
        "available": "active",
        "published": "active",
        "visible": "active",
        "online": "active",
        "for_sale": "active",
        "for_sell": "active",
        "closed": "sold",
    }
    raw = aliases.get(raw, raw)
    if raw in {"sold", "reserved", "hidden", "draft", "active"}:
        return raw

    # Items returned by the public wardrobe endpoint are active unless Vinted
    # explicitly marks them otherwise with one of the lifecycle flags above.
    return "active"


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


def _notification_details(body: str, link: str | None) -> dict[str, str | None]:
    text = (body or "").strip()
    link_text = str(link or "")
    if "/want_it/" not in link_text:
        return {
            "category": "other",
            "item_id": None,
            "item_title": None,
            "actor": None,
        }

    item_match = re.search(r"/items/(\d+)", link_text)
    item_id = item_match.group(1) if item_match else None
    actor = None
    item_title = None

    patterns = (
        r"^(?P<actor>.+?) adicionou o teu (?P<title>.+?) aos seus favoritos\.?$",
        r"^(?P<actor>.+?) added your (?P<title>.+?) to (?:their|his|her) favou?rites\.?$",
    )
    for pattern in patterns:
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if match:
            actor = match.group("actor").strip()
            item_title = match.group("title").strip()
            break

    return {
        "category": "favorite",
        "item_id": item_id,
        "item_title": item_title,
        "actor": actor,
    }

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
        self._public_session: requests.Session | None = None
        self._diagnostics: list[EndpointResult] = []
        self._orders_source: str | None = None
        self._notifications_source: str | None = None
        self._listings_source: str | None = None

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
        return bool(self.cookie_string())

    def has_refresh_token(self) -> bool:
        cookies = _parse_cookie_header(self.cookie_string())
        refresh = cookies.get("refresh_token_web", "") or os.getenv(
            "VINTED_REFRESH_TOKEN_WEB", ""
        ).strip()
        return bool(refresh)

    def _write_cookie_map(self, cookies: dict[str, str]) -> None:
        value = "; ".join(f"{key}={item}" for key, item in cookies.items() if key)
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.session_file.write_text(value, encoding="utf-8")
        self.session_file.chmod(0o600)

    def _persist_refreshed_tokens(
        self,
        access_token: str,
        refresh_token: str | None = None,
    ) -> None:
        cookies = _parse_cookie_header(self.cookie_string())
        cookies["access_token_web"] = access_token
        if refresh_token:
            cookies["refresh_token_web"] = refresh_token
        self._write_cookie_map(cookies)
        self.reset_session()

    def save_cookie(self, value: str) -> None:
        value = value.strip()
        if value.lower().startswith("cookie:"):
            value = value.split(":", 1)[1].strip()
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
            if self._public_session is not None:
                self._public_session.close()
            self._session = None
            self._public_session = None
            self._orders_source = None
            self._notifications_source = None
            self._listings_source = None

    def _headers(self) -> dict[str, str]:
        user_agent = os.getenv("VINTED_USER_AGENT", "").strip()
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-GB,en;q=0.9,pt-PT;q=0.8,pt;q=0.7",
            "Origin": self.base_url,
            "Referer": self.base_url + "/",
            "X-Platform": "web",
            "X-Money-Object": "true",
            "X-Requested-With": "XMLHttpRequest",
        }
        if user_agent:
            headers["User-Agent"] = user_agent

        cookie = self.cookie_string()
        cookies = _parse_cookie_header(cookie)
        if cookie:
            headers["Cookie"] = cookie

        access = cookies.get("access_token_web", "") or os.getenv(
            "VINTED_ACCESS_TOKEN_WEB", ""
        ).strip()
        if access:
            headers["Authorization"] = f"Bearer {access}"

        csrf = (
            os.getenv("VINTED_CSRF_TOKEN", "").strip()
            or cookies.get("x-csrf-token", "")
            or cookies.get("csrf_token", "")
            or cookies.get("csrf", "")
        )
        if csrf:
            headers["X-Csrf-Token"] = csrf

        anon = cookies.get("anon_id", "") or os.getenv("VINTED_ANON_ID", "").strip()
        if anon:
            headers["X-Anon-Id"] = anon
        return headers

    def _public_headers(self) -> dict[str, str]:
        headers = self._headers()
        for key in ("Cookie", "Authorization", "X-Csrf-Token", "X-Anon-Id"):
            headers.pop(key, None)
        return headers

    def _get_public_session(self) -> requests.Session:
        with self._lock:
            if self._public_session is None:
                session = requests.Session(impersonate="chrome")
                session.headers.update(self._public_headers())
                try:
                    response = session.get(self.base_url + "/", timeout=self.timeout)
                    csrf = response.headers.get("X-Csrf-Token") or _extract_csrf_token(
                        response.text
                    )
                    anon = response.headers.get("X-Anon-Id") or response.cookies.get(
                        "anon_id"
                    )
                    access = response.cookies.get("access_token_web")
                    if csrf:
                        session.headers["X-Csrf-Token"] = csrf
                    if anon:
                        session.headers["X-Anon-Id"] = anon
                    if access:
                        session.headers["Authorization"] = f"Bearer {access}"
                except Exception:
                    pass
                self._public_session = session
            return self._public_session

    def _refresh_auth_session(self) -> bool:
        cookies = _parse_cookie_header(self.cookie_string())
        refresh_token = (
            cookies.get("refresh_token_web", "")
            or os.getenv("VINTED_REFRESH_TOKEN_WEB", "").strip()
        )
        if not refresh_token:
            return False

        refresh_session = requests.Session(impersonate="chrome")
        headers = self._headers().copy()
        headers.pop("Authorization", None)
        headers["Referer"] = self.base_url + "/session-refresh?ref_url=%2F"
        headers["Origin"] = self.base_url
        refresh_session.headers.update(headers)

        access_token = ""
        rotated_refresh = ""

        try:
            response = refresh_session.post(
                self.base_url + "/web/api/auth/refresh",
                data="",
                timeout=self.timeout,
            )
            if response.ok:
                access_token = (
                    response.cookies.get("access_token_web")
                    or refresh_session.cookies.get("access_token_web")
                    or ""
                )
                rotated_refresh = (
                    response.cookies.get("refresh_token_web")
                    or refresh_session.cookies.get("refresh_token_web")
                    or ""
                )
                if not access_token:
                    try:
                        body = response.json()
                    except ValueError:
                        body = {}
                    if isinstance(body, dict):
                        access_token = str(
                            body.get("access_token")
                            or body.get("access_token_web")
                            or ""
                        )
                        rotated_refresh = str(
                            body.get("refresh_token")
                            or body.get("refresh_token_web")
                            or rotated_refresh
                            or ""
                        )
        except Exception:
            response = None

        # Compatibility fallback used by some current Vinted web clients.
        if not access_token:
            try:
                response = refresh_session.post(
                    self.base_url + "/oauth/token",
                    data={
                        "grant_type": "refresh_token",
                        "client_id": "web",
                        "refresh_token": refresh_token,
                    },
                    timeout=self.timeout,
                )
                if response.ok:
                    try:
                        body = response.json()
                    except ValueError:
                        body = {}
                    if isinstance(body, dict):
                        access_token = str(
                            body.get("access_token")
                            or body.get("access_token_web")
                            or ""
                        )
                        rotated_refresh = str(
                            body.get("refresh_token")
                            or body.get("refresh_token_web")
                            or ""
                        )
                    if not access_token:
                        access_token = (
                            response.cookies.get("access_token_web")
                            or refresh_session.cookies.get("access_token_web")
                            or ""
                        )
                    if not rotated_refresh:
                        rotated_refresh = (
                            response.cookies.get("refresh_token_web")
                            or refresh_session.cookies.get("refresh_token_web")
                            or ""
                        )
            except Exception:
                return False

        if not access_token:
            return False

        self._persist_refreshed_tokens(
            access_token,
            rotated_refresh or refresh_token,
        )
        self._record("/web/api/auth/refresh", 200, True, "session refreshed")
        return True

    def _get_session(self) -> requests.Session:
        with self._lock:
            if self._session is None:
                session = requests.Session(impersonate="chrome")
                session.headers.update(self._headers())

                # A normal Vinted page load can return the request identity headers
                # used by its own frontend. Capture them when available.
                try:
                    response = session.get(self.base_url + "/", timeout=self.timeout)
                    csrf = response.headers.get("X-Csrf-Token") or _extract_csrf_token(
                        response.text
                    )
                    anon = response.headers.get("X-Anon-Id") or response.cookies.get(
                        "anon_id"
                    )
                    access = response.cookies.get("access_token_web")
                    if csrf and "X-Csrf-Token" not in session.headers:
                        session.headers["X-Csrf-Token"] = csrf
                    if anon and "X-Anon-Id" not in session.headers:
                        session.headers["X-Anon-Id"] = anon
                    if access and "Authorization" not in session.headers:
                        session.headers["Authorization"] = f"Bearer {access}"
                except Exception:
                    # The API calls below still have a chance to succeed with the
                    # browser cookie supplied by the user.
                    pass

                self._session = session
            return self._session

    def _request(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        auth: bool = False,
        allow_404: bool = False,
        anonymous: bool = False,
        retry_auth: bool = True,
    ) -> Any:
        if auth and not self.has_auth():
            raise VintedAuthRequired(
                "Vinted session required for private account data"
            )

        url = path if path.startswith("http") else urljoin(self.base_url + "/", path.lstrip("/"))
        session = self._get_public_session() if anonymous else self._get_session()
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

            if auth and retry_auth and self._refresh_auth_session():
                return self._request(
                    path,
                    params=params,
                    auth=auth,
                    allow_404=allow_404,
                    anonymous=anonymous,
                    retry_auth=False,
                )

            if auth:
                if self.has_refresh_token():
                    raise VintedAuthRequired(
                        "Vinted rejected the session and automatic token refresh failed."
                    )
                raise VintedAuthRequired(
                    "Vinted rejected the saved session. Add refresh_token_web so the dashboard can renew it automatically."
                )
            raise VintedBlocked(
                "Vinted returned 403. Cloudflare or DataDome may be blocking this request."
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
        payload = self._request(
            f"/api/v2/users/{self.user_id}",
            params={"localize": "false"},
            anonymous=True,
        )
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

    def _normalize_listing(
        self,
        raw: dict[str, Any],
        *,
        forced_status: str | None = None,
    ) -> dict[str, Any] | None:
        owner = _first(raw, "user", "seller", "owner")
        owner_id = _id(owner)
        if owner_id and owner_id != self.user_id:
            return None

        item_id = str(_first(raw, "id", "item_id") or "")
        price, currency = _money(
            _first(raw, "price", "total_item_price", "item_price")
        )
        status = _listing_status(raw)
        if forced_status and status == "active":
            status = "sold" if forced_status == "closed" else forced_status

        return {
            "id": item_id or None,
            "title": str(_first(raw, "title", "name") or "Untitled"),
            "price_cents": price,
            "currency": currency,
            "status": status,
            "isbn": _first(raw, "isbn"),
            "vinted_url": _item_url(self.base_url, raw),
            "image_url": _photo_url(raw),
            "listed_at": _timestamp(
                _first(
                    raw,
                    "created_at_ts",
                    "created_timestamp_ts",
                    "created_at",
                    "uploaded_at",
                    "posted_at",
                    "upload_date_dte",
                )
            ),
            "favourites": _first(
                raw, "favourite_count", "favorites_count", "favourites_count"
            ),
            "views": _first(raw, "view_count", "views_count"),
        }

    def _append_listing_rows(
        self,
        rows: list[dict[str, Any]],
        seen: set[str],
        items: list[dict[str, Any]],
        *,
        forced_status: str | None = None,
    ) -> None:
        for raw in items:
            row = self._normalize_listing(raw, forced_status=forced_status)
            if row is None:
                continue
            item_id = str(row.get("id") or "")
            if item_id and item_id in seen:
                continue
            if item_id:
                seen.add(item_id)
            rows.append(row)

    def _get_owner_listings(self) -> list[dict[str, Any]] | None:
        if not self.has_auth():
            return None

        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        owner_path = f"/api/v2/users/{self.user_id}/items"
        endpoint_supported = False

        # Current authenticated clients use this endpoint with a status filter.
        # Query each lifecycle view because Vinted does not include sold/draft
        # inventory in the public wardrobe feed.
        for status in ("active", "sold", "reserved", "draft", "closed"):
            for page in range(1, self.max_pages + 1):
                try:
                    payload = self._request(
                        owner_path,
                        params={
                            "status": status,
                            "order": "newest_first",
                            "page": page,
                            "per_page": 96,
                        },
                        auth=True,
                        allow_404=True,
                    )
                except (VintedAuthRequired, VintedBlocked, VintedRateLimited):
                    raise
                except VintedError:
                    # Some markets reject individual lifecycle filters.
                    # Keep the other supported views rather than failing all
                    # inventory loading.
                    break

                if payload is None:
                    if status == "active" and page == 1:
                        break
                    break

                endpoint_supported = True
                items = self._list_from(payload, "items", "user_items")
                if not items:
                    break

                self._append_listing_rows(
                    rows,
                    seen,
                    items,
                    forced_status=status,
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

        # The own-wardrobe response can contain lifecycle flags that are not
        # represented by the status-filter API, especially hidden items.
        wardrobe_path = f"/api/v2/wardrobe/{self.user_id}/items"
        for page in range(1, self.max_pages + 1):
            try:
                payload = self._request(
                    wardrobe_path,
                    params={
                        "order": "newest_first",
                        "page": page,
                        "per_page": 96,
                    },
                    auth=True,
                    allow_404=True,
                )
            except VintedError:
                break

            if payload is None:
                break
            endpoint_supported = True
            items = self._list_from(payload, "items", "user_items")
            if not items:
                break
            self._append_listing_rows(rows, seen, items)

            pagination = payload.get("pagination") if isinstance(payload, dict) else None
            if isinstance(pagination, dict):
                total_pages = pagination.get("total_pages")
                if isinstance(total_pages, int) and page >= total_pages:
                    break
                if pagination.get("next_page") is None and len(items) < 96:
                    break
            elif len(items) < 96:
                break

        if endpoint_supported:
            self._listings_source = "authenticated owner inventory"
            return rows
        return None

    def _get_public_listings(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        wardrobe_path = f"/api/v2/wardrobe/{self.user_id}/items"

        for page in range(1, self.max_pages + 1):
            payload = self._request(
                wardrobe_path,
                params={
                    "order": "newest_first",
                    "page": page,
                    "per_page": 96,
                },
                allow_404=True,
                anonymous=True,
            )
            if payload is None:
                raise VintedError(
                    "Vinted does not expose a working wardrobe endpoint for this market"
                )

            items = self._list_from(payload, "items", "user_items")
            if not items:
                break
            self._append_listing_rows(rows, seen, items, forced_status="active")

            pagination = payload.get("pagination") if isinstance(payload, dict) else None
            if isinstance(pagination, dict):
                total_pages = pagination.get("total_pages")
                if isinstance(total_pages, int) and page >= total_pages:
                    break
                if pagination.get("next_page") is None and len(items) < 96:
                    break
            elif len(items) < 96:
                break

        self._listings_source = wardrobe_path
        return rows

    def get_listings(self) -> list[dict[str, Any]]:
        owner_rows = self._get_owner_listings()
        if owner_rows is not None:
            return owner_rows
        return self._get_public_listings()

    def get_notifications(self) -> list[dict[str, Any]]:
        candidates = []
        if self._notifications_source:
            candidates.append(self._notifications_source)
        candidates.extend(
            [
                "/api/v2/notifications",
                "/web/api/notifications/notifications",
            ]
        )

        payload = None
        raw_rows: list[dict[str, Any]] = []
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
            raw_rows = self._list_from(payload, "notifications", "items", "entries")
            self._notifications_source = path
            break

        rows = []
        for raw in raw_rows:
            title = _first(raw, "title", "subject", "heading")
            body = _first(raw, "body", "text", "message", "description")
            if isinstance(body, dict):
                body = _first(body, "text", "value")
            link = _first(raw, "link", "url", "deep_link", "target_url")
            if link and not str(link).startswith("http"):
                link = urljoin(self.base_url, str(link))
            body_text = str(body or "")
            details = _notification_details(body_text, str(link) if link else None)
            is_read = _first(raw, "is_read", "read", "seen")
            read_at = _first(raw, "read_at", "seen_at")
            rows.append(
                {
                    "id": str(_first(raw, "id", "notification_id") or ""),
                    "kind": str(
                        _first(raw, "entry_type", "type", "notification_type", "event_type")
                        or "notification"
                    ),
                    "category": details["category"],
                    "item_id": details["item_id"],
                    "item_title": details["item_title"],
                    "actor": details["actor"],
                    "title": str(title or body or "Vinted notification"),
                    "body": body_text,
                    "occurred_at": _timestamp(
                        _first(raw, "updated_at", "created_at", "created_at_ts", "timestamp")
                    ),
                    "read": bool(is_read or read_at),
                    "url": str(link) if link else None,
                }
            )
        return rows

    def _normalize_my_order(
        self, raw: dict[str, Any], direction: str
    ) -> dict[str, Any]:
        price, currency = _money(
            _first(raw, "price", "total_price", "total", "amount")
        )
        lifecycle_status = str(
            _first(raw, "transaction_user_status", "state") or ""
        ).lower().strip().replace(" ", "_").replace("-", "_")
        status = str(
            _first(raw, "status") or lifecycle_status or "open"
        ).lower().strip().replace(" ", "_").replace("-", "_")

        conversation_id = _first(raw, "conversation_id", "thread_id")
        order_id = _first(raw, "id", "transaction_id", "order_id") or conversation_id
        url = _first(raw, "url", "web_url", "link")
        if not url and conversation_id:
            url = f"{self.base_url}/inbox/{conversation_id}"
        elif url and not str(url).startswith("http"):
            url = urljoin(self.base_url, str(url))

        other_user = _first(raw, "opposite_user", "other_user", "user")
        counterparty = _username(other_user)

        return {
            "id": str(order_id or ""),
            "thread_id": str(conversation_id or ""),
            "direction": direction,
            "title": str(_first(raw, "title", "item_title", "name") or "Vinted order"),
            "counterparty": counterparty,
            "total_cents": price,
            "currency": currency,
            "status": status,
            "lifecycle_status": lifecycle_status or status,
            "is_closed": is_closed_status(lifecycle_status or status),
            "tracking_code": _first(
                raw, "tracking_code", "tracking_number", "shipment_tracking_code"
            ),
            "updated_at": _timestamp(
                _first(raw, "updated_at", "date", "created_at", "created_at_ts")
            ),
            "vinted_url": str(url) if url else None,
        }

    def _get_my_orders(
        self, order_type: str, direction: str
    ) -> tuple[list[dict[str, Any]], bool]:
        rows: list[dict[str, Any]] = []
        for page in range(1, self.max_pages + 1):
            payload = self._request(
                "/api/v2/my_orders",
                params={
                    "type": order_type,
                    "status": "all",
                    "page": page,
                    "per_page": 100,
                },
                auth=True,
                allow_404=True,
            )
            if payload is None:
                return [], False

            raw_rows = self._list_from(payload, "my_orders", "orders", "items")
            rows.extend(self._normalize_my_order(raw, direction) for raw in raw_rows)

            pagination = payload.get("pagination") if isinstance(payload, dict) else None
            if isinstance(pagination, dict):
                total_pages = pagination.get("total_pages")
                if isinstance(total_pages, int) and page >= total_pages:
                    break
                if pagination.get("next_page") is None and len(raw_rows) < 100:
                    break
            elif len(raw_rows) < 100:
                break
        return rows, True

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
            "lifecycle_status": status,
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
        direct_rows: list[dict[str, Any]] = []
        direct_supported = True
        for order_type, direction in (("sold", "sell"), ("purchased", "buy")):
            rows, supported = self._get_my_orders(order_type, direction)
            if not supported:
                direct_supported = False
                break
            direct_rows.extend(rows)

        if direct_supported:
            self._orders_source = "/api/v2/my_orders"
            seen: set[tuple[str, str]] = set()
            deduped = []
            for row in direct_rows:
                key = (row["direction"], row["id"] or row["thread_id"])
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(row)
            return deduped, self._orders_source

        # Fallback for Vinted variants that do not expose my_orders.
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
            "refresh_token_available": self.has_refresh_token(),
            "orders_source": self._orders_source,
            "notifications_source": self._notifications_source,
            "listings_source": self._listings_source,
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
