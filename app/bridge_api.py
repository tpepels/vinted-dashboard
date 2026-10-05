"""Paired Chrome bridge HTTP API.

This router owns pairing, bridge device management, bridge status and browser
snapshot ingestion. Product inventory/business endpoints remain in
:mod:`app.product_api`.
"""

from __future__ import annotations

import os
import secrets
import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import billing, db, models
from app.auth import (
    RequestContext,
    extension_context,
    rate_limiter,
    require_context,
    require_write_context,
    token_hash,
    utcnow,
)
from app.bridge_package import extension_source_version
from app.product_models import ExtensionCredential, ExtensionPairing
from app.workspace_ingest import record_workspace_snapshot

router = APIRouter()
APP_NAME = os.getenv("APP_NAME", "Reseller Dashboard").strip() or "Reseller Dashboard"


class PairingCompleteRequest(BaseModel):
    code: str
    extension_version: str | None = None
    device_name: str = "Chrome"


class WorkspaceBrowserSyncPayload(BaseModel):
    collected_at: float
    current_user: dict[str, Any]
    listings: list[dict[str, Any]]
    notifications: list[dict[str, Any]]
    orders: list[dict[str, Any]]
    market_results: list[dict[str, Any]] = Field(default_factory=list)
    extension_version: str | None = None


def _serialize_workspace(workspace: models.Workspace) -> dict[str, Any]:
    return {
        "id": str(workspace.id),
        "name": workspace.name,
        "slug": workspace.slug,
        "billing_status": workspace.billing_status,
        "trial_ends_at": (
            workspace.trial_ends_at.isoformat() if workspace.trial_ends_at else None
        ),
        "settings": dict(workspace.settings or {}),
    }


@router.post("/api/app/extension/pairings")
def start_pairing(context: RequestContext = Depends(require_write_context)):
    raw = "-".join((secrets.token_hex(2).upper(), secrets.token_hex(2).upper()))
    with db.session_scope() as session:
        session.add(
            ExtensionPairing(
                workspace_id=context.workspace.id,
                user_id=context.user.id,
                code_hash=token_hash(raw),
                expires_at=utcnow() + timedelta(minutes=10),
            )
        )
    return {"code": raw, "expires_in_seconds": 600}


@router.get("/api/app/extension/devices")
def extension_devices(context: RequestContext = Depends(require_context)):
    with db.session_scope() as session:
        rows = session.execute(
            select(ExtensionCredential)
            .where(ExtensionCredential.workspace_id == context.workspace.id)
            .order_by(ExtensionCredential.created_at.desc())
        ).scalars().all()
    latest = extension_source_version()
    return {
        "latest_version": latest,
        "download_url": f"/downloads/reseller-chrome-bridge-v{latest}.zip",
        "devices": [
            {
                "id": str(row.id),
                "name": row.name,
                "extension_version": row.extension_version,
                "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
                "revoked": row.revoked_at is not None,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
    }


@router.delete("/api/app/extension/devices/{credential_id}")
def revoke_extension(
    credential_id: uuid.UUID,
    context: RequestContext = Depends(require_write_context),
):
    with db.session_scope() as session:
        row = session.get(ExtensionCredential, credential_id)
        if row is None or row.workspace_id != context.workspace.id:
            raise HTTPException(status_code=404, detail="Extension device not found")
        row.revoked_at = utcnow()
    return {"ok": True}


@router.post("/api/extension/pair")
def complete_pairing(payload: PairingCompleteRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    rate_limiter.check(f"extension-pair:{ip}", limit=20, window_seconds=900)
    code = payload.code.strip().upper()
    now = utcnow()
    with db.session_scope() as session:
        pairing = session.execute(
            select(ExtensionPairing).where(
                ExtensionPairing.code_hash == token_hash(code),
                ExtensionPairing.claimed_at.is_(None),
                ExtensionPairing.expires_at > now,
            )
        ).scalar_one_or_none()
        if pairing is None:
            raise HTTPException(status_code=400, detail="Pairing code is invalid or expired")

        workspace = session.get(models.Workspace, pairing.workspace_id)
        if workspace is None or not billing.workspace_can_write(workspace):
            raise HTTPException(
                status_code=402,
                detail="Workspace is read-only until the subscription is active or trialing",
            )

        raw_token = secrets.token_urlsafe(42)
        credential = ExtensionCredential(
            workspace_id=pairing.workspace_id,
            user_id=pairing.user_id,
            name=(payload.device_name or "Chrome")[:200],
            token_hash=token_hash(raw_token),
            extension_version=payload.extension_version,
            last_seen_at=now,
        )
        session.add(credential)
        pairing.claimed_at = now
        session.flush()
        workspace_name = workspace.name

    return {
        "ok": True,
        "token": raw_token,
        "workspace": workspace_name,
        "app_name": APP_NAME,
    }


@router.get("/api/extension/status")
def extension_status(context: RequestContext = Depends(extension_context)):
    latest = extension_source_version()
    return {
        "ok": True,
        "workspace": _serialize_workspace(context.workspace),
        "app_name": APP_NAME,
        "latest_version": latest,
        "installed_version": (
            context.extension.extension_version if context.extension else None
        ),
    }


@router.post("/api/extension/browser-sync")
def extension_browser_sync(
    payload: WorkspaceBrowserSyncPayload,
    context: RequestContext = Depends(extension_context),
):
    if not billing.workspace_can_write(context.workspace):
        raise HTTPException(
            status_code=402,
            detail="Workspace is read-only until the subscription is active or trialing",
        )

    data = payload.model_dump()
    version = payload.extension_version or (
        context.extension.extension_version if context.extension else None
    )
    result = record_workspace_snapshot(
        context.workspace.id,
        data,
        extension_version=version,
    )

    if context.extension is not None and version:
        with db.session_scope() as session:
            row = session.get(ExtensionCredential, context.extension.id)
            if row is not None:
                row.extension_version = version
                row.last_seen_at = utcnow()

    return {"ok": True, **result}
