"""Hosted-app authentication and workspace request context.

The web app uses opaque, revocable server-side sessions stored by SHA-256
hash only.  Mutating browser requests additionally require a double-submit
CSRF token.  Chrome bridge credentials are separate bearer tokens and can be
revoked independently.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Request, Response
from sqlalchemy import select

from app import billing, db, models
from app.product_models import AuthSession, ExtensionCredential


SESSION_COOKIE = "reseller_session"
CSRF_COOKIE = "reseller_csrf"
SESSION_DAYS = int(os.getenv("AUTH_SESSION_DAYS", "30"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}
PBKDF2_ITERATIONS = int(os.getenv("PASSWORD_HASH_ITERATIONS", "310000"))
# Activity timestamps are advisory; updating them on every GET needlessly
# serializes the web process and background worker on SQLite.
ACTIVITY_TOUCH_INTERVAL = timedelta(minutes=2)


@dataclass
class RequestContext:
    user: models.User
    workspace: models.Workspace
    membership: models.Membership
    session: Optional[AuthSession] = None
    extension: Optional[ExtensionCredential] = None


class _RateLimiter:
    def __init__(self) -> None:
        self._events: dict[str, list[float]] = {}

    def check(self, key: str, *, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        cutoff = now - window_seconds
        events = [value for value in self._events.get(key, []) if value >= cutoff]
        if len(events) >= limit:
            raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")
        events.append(now)
        self._events[key] = events


rate_limiter = _RateLimiter()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("Password must contain at least 10 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256$%d$%s$%s" % (
        PBKDF2_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_session(user: models.User, request: Request) -> tuple[str, str]:
    raw_token = secrets.token_urlsafe(36)
    csrf = secrets.token_urlsafe(24)
    with db.session_scope() as session:
        session.add(
            AuthSession(
                user_id=user.id,
                token_hash=token_hash(raw_token),
                csrf_hash=token_hash(csrf),
                expires_at=utcnow() + timedelta(days=SESSION_DAYS),
                user_agent=(request.headers.get("user-agent") or "")[:500] or None,
            )
        )
    return raw_token, csrf


def set_session_cookies(response: Response, raw_token: str, csrf: str) -> None:
    max_age = SESSION_DAYS * 86400
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        max_age=max_age,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=max_age,
        httponly=False,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def _workspace_for_user(session, user: models.User, requested: str | None):
    query = (
        select(models.Membership, models.Workspace)
        .join(models.Workspace, models.Workspace.id == models.Membership.workspace_id)
        .where(models.Membership.user_id == user.id)
        .order_by(models.Membership.created_at)
    )
    rows = session.execute(query).all()
    if not rows:
        raise HTTPException(status_code=403, detail="No workspace is assigned to this account")
    if requested:
        try:
            wanted = uuid.UUID(requested)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid workspace id") from exc
        for membership, workspace in rows:
            if workspace.id == wanted:
                return membership, workspace
        raise HTTPException(status_code=403, detail="Workspace access denied")
    return rows[0]


def require_context(request: Request) -> RequestContext:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        raise HTTPException(status_code=401, detail="Sign in required")
    now = utcnow()
    with db.session_scope() as session:
        auth_session = session.execute(
            select(AuthSession).where(AuthSession.token_hash == token_hash(raw))
        ).scalar_one_or_none()
        if auth_session is None or auth_session.expires_at <= now:
            raise HTTPException(status_code=401, detail="Session expired")
        user = session.get(models.User, auth_session.user_id)
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="Account is unavailable")
        membership, workspace = _workspace_for_user(
            session, user, request.headers.get("x-workspace-id")
        )
        if (
            auth_session.last_seen_at is None
            or now - auth_session.last_seen_at >= ACTIVITY_TOUCH_INTERVAL
        ):
            auth_session.last_seen_at = now
            session.flush()
        session.expunge(user)
        session.expunge(membership)
        session.expunge(workspace)
        session.expunge(auth_session)
        return RequestContext(user=user, workspace=workspace, membership=membership, session=auth_session)


def _billing_write_exempt(request: Request) -> bool:
    path = request.url.path
    if path in {"/api/app/billing/checkout", "/api/app/billing/portal"}:
        return True
    if request.method == "DELETE" and path == "/api/app/account":
        return True
    if request.method == "DELETE" and path.startswith("/api/app/extension/devices/"):
        return True
    if (
        request.method == "DELETE"
        and path.startswith("/api/app/connectors/")
        and path.endswith("/credentials")
    ):
        return True
    return False


def require_write_context(request: Request) -> RequestContext:
    context = require_context(request)
    csrf_cookie = request.cookies.get(CSRF_COOKIE) or ""
    csrf_header = request.headers.get("x-csrf-token") or ""
    if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    if context.session is None or not hmac.compare_digest(
        context.session.csrf_hash, token_hash(csrf_header)
    ):
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    if not _billing_write_exempt(request) and not billing.workspace_can_write(context.workspace):
        raise HTTPException(
            status_code=402,
            detail="Workspace is read-only until the subscription is active or trialing",
        )
    return context


def extension_context(request: Request) -> RequestContext:
    authorization = request.headers.get("authorization") or ""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Extension authorization required")
    raw = authorization.split(" ", 1)[1].strip()
    if not raw:
        raise HTTPException(status_code=401, detail="Extension authorization required")
    with db.session_scope() as session:
        credential = session.execute(
            select(ExtensionCredential).where(
                ExtensionCredential.token_hash == token_hash(raw),
                ExtensionCredential.revoked_at.is_(None),
            )
        ).scalar_one_or_none()
        if credential is None:
            raise HTTPException(status_code=401, detail="Extension credential is invalid or revoked")
        user = session.get(models.User, credential.user_id)
        workspace = session.get(models.Workspace, credential.workspace_id)
        if user is None or workspace is None or not user.is_active:
            raise HTTPException(status_code=401, detail="Extension account is unavailable")
        membership = session.execute(
            select(models.Membership).where(
                models.Membership.user_id == user.id,
                models.Membership.workspace_id == workspace.id,
            )
        ).scalar_one_or_none()
        if membership is None:
            raise HTTPException(status_code=403, detail="Extension workspace access denied")
        now = utcnow()
        if (
            credential.last_seen_at is None
            or now - credential.last_seen_at >= ACTIVITY_TOUCH_INTERVAL
        ):
            credential.last_seen_at = now
            session.flush()
        session.expunge(user)
        session.expunge(workspace)
        session.expunge(membership)
        session.expunge(credential)
        return RequestContext(
            user=user,
            workspace=workspace,
            membership=membership,
            extension=credential,
        )
