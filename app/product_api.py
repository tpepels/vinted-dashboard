"""Workspace-scoped product API.

This router is the commercial/hosted surface.  Legacy personal-dashboard
routes remain available separately for backwards compatibility.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app import billing, db, jobs, models
from app.channels import parse_biblio_inventory
from app.auth import (
    RequestContext,
    clear_session_cookies,
    create_session,
    extension_context,
    hash_password,
    rate_limiter,
    require_context,
    require_write_context,
    set_session_cookies,
    token_hash,
    utcnow,
    verify_password,
)
from app.constants import (
    BillingStatus,
    Channel,
    ChannelAccountStatus,
    ItemCategory,
    ItemStatus,
    ListingStatus,
    MembershipRole,
)
from app.connectors.base import connector_catalog
from app.connectors.hosted import (
    has_credentials as has_workspace_connector_credentials,
    import_biblio_workspace,
    test_biblio_workspace,
)
from app.crypto import decrypt_json, encrypt_json, using_derived_key
from app.import_export import (
    apply_inventory_import,
    inventory_export_rows,
    parse_table,
    preview_inventory_import,
    render_csv,
    render_xlsx,
    suggest_mapping,
)
from app.product_models import (
    AuthSession,
    ConnectorCredential,
    ExportJob,
    ExtensionCredential,
    ExtensionPairing,
    ImportJob,
    MappingPreset,
)
from app.strategy import strategy_settings
from app.workspace_bootstrap import (
    BOOTSTRAP_OWNER_EMAIL,
    get_or_create_channel_account,
)
from app.workspace_ingest import maybe_record_legacy_snapshot, record_workspace_snapshot


router = APIRouter()
APP_NAME = os.getenv("APP_NAME", "Reseller Dashboard").strip() or "Reseller Dashboard"


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str | None = None
    workspace_name: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class InventoryCreateRequest(BaseModel):
    sku: str
    title: str
    category: str = ItemCategory.GENERAL
    quantity: int = 1
    condition: str | None = None
    cost_cents: int | None = None
    currency: str = "EUR"
    location: str | None = None
    notes: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class InventoryPatchRequest(BaseModel):
    title: str | None = None
    category: str | None = None
    quantity: int | None = None
    condition: str | None = None
    cost_cents: int | None = None
    currency: str | None = None
    location: str | None = None
    notes: str | None = None
    status: str | None = None
    attributes: dict[str, Any] | None = None


class ListingLinkRequest(BaseModel):
    listing_id: str


class MappingPresetRequest(BaseModel):
    name: str
    direction: str = "import"
    file_type: str = "csv"
    mapping: dict[str, str]
    options: dict[str, Any] = Field(default_factory=dict)


class PairingCompleteRequest(BaseModel):
    code: str
    extension_version: str | None = None
    device_name: str = "Chrome"


class ConnectorCredentialsRequest(BaseModel):
    values: dict[str, str]


class SettingsRequest(BaseModel):
    name: str | None = None
    settings: dict[str, Any] | None = None


class WorkspaceBrowserSyncPayload(BaseModel):
    collected_at: float
    current_user: dict[str, Any]
    listings: list[dict[str, Any]]
    notifications: list[dict[str, Any]]
    orders: list[dict[str, Any]]
    market_results: list[dict[str, Any]] = Field(default_factory=list)
    extension_version: str | None = None


class DeleteWorkspaceRequest(BaseModel):
    confirm: str


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return value[:180] or "workspace"


def _unique_slug(session, wanted: str) -> str:
    base = _slug(wanted)
    candidate = base
    suffix = 2
    while session.execute(
        select(models.Workspace.id).where(models.Workspace.slug == candidate)
    ).scalar_one_or_none() is not None:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _serialize_workspace(workspace: models.Workspace) -> dict[str, Any]:
    return {
        "id": str(workspace.id),
        "name": workspace.name,
        "slug": workspace.slug,
        "billing_status": workspace.billing_status,
        "settings": dict(workspace.settings or {}),
    }


def _serialize_item(item: models.InventoryItem, listings: list[models.ChannelListing] | None = None) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "sku": item.sku,
        "title": item.title,
        "category": item.category,
        "quantity": item.quantity,
        "condition": item.condition,
        "cost_cents": item.cost_cents,
        "currency": item.currency,
        "location": item.location,
        "notes": item.notes,
        "status": item.status,
        "attributes": dict(item.attributes or {}),
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "listings": [
            {
                "id": str(row.id),
                "channel": row.channel,
                "external_id": row.external_id,
                "external_sku": row.external_sku,
                "title": row.title,
                "price_cents": row.price_cents,
                "currency": row.currency,
                "status": row.status,
                "url": row.url,
                "quantity": row.quantity,
                "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
            }
            for row in (listings or [])
        ],
    }


def _csrf_from_request(request: Request) -> str | None:
    return request.cookies.get("reseller_csrf")


@router.post("/api/auth/register")
def register(payload: RegisterRequest, request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"
    rate_limiter.check(f"register:{ip}", limit=8, window_seconds=3600)
    email = payload.email.strip().lower()
    if "@" not in email or len(email) > 320:
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    try:
        hashed = hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with db.session_scope() as session:
        user = session.execute(select(models.User).where(models.User.email == email)).scalar_one_or_none()
        if user is not None and user.hashed_password:
            raise HTTPException(status_code=409, detail="An account with this email already exists")

        if user is None:
            # Fresh self-hosted installs often already contain the unclaimed
            # bootstrap owner created by the legacy migration.  Adopt that
            # account on the first real registration so historical data stays
            # attached instead of creating an unrelated empty workspace.
            password_users = session.execute(
                select(func.count(models.User.id)).where(models.User.hashed_password.is_not(None))
            ).scalar_one()
            placeholder = None
            if int(password_users or 0) == 0:
                placeholder = session.execute(
                    select(models.User).where(
                        models.User.email == BOOTSTRAP_OWNER_EMAIL,
                        models.User.hashed_password.is_(None),
                    )
                ).scalar_one_or_none()
            if placeholder is not None:
                placeholder.email = email
                user = placeholder
            else:
                user = models.User(email=email, is_active=True)
                session.add(user)
                session.flush()

        user.hashed_password = hashed
        user.display_name = payload.display_name or user.display_name or email.split("@", 1)[0]

        membership = session.execute(
            select(models.Membership).where(models.Membership.user_id == user.id)
        ).scalars().first()
        if membership is None:
            name = (payload.workspace_name or f"{user.display_name}'s workspace").strip()
            workspace = models.Workspace(
                name=name,
                slug=_unique_slug(session, name),
                is_personal=True,
                billing_status=BillingStatus.TRIALING if billing.BILLING_ENABLED else BillingStatus.DEV,
                settings={},
            )
            session.add(workspace)
            session.flush()
            session.add(
                models.Membership(
                    user_id=user.id,
                    workspace_id=workspace.id,
                    role=MembershipRole.OWNER,
                )
            )
        session.flush()
        session.expunge(user)

    raw, csrf = create_session(user, request)
    set_session_cookies(response, raw, csrf)
    return {"ok": True, "email": user.email, "csrf_token": csrf}


@router.post("/api/auth/login")
def login(payload: LoginRequest, request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"
    rate_limiter.check(f"login:{ip}:{payload.email.lower()}", limit=10, window_seconds=900)
    email = payload.email.strip().lower()
    with db.session_scope() as session:
        user = session.execute(select(models.User).where(models.User.email == email)).scalar_one_or_none()
        if user is None or not verify_password(payload.password, user.hashed_password) or not user.is_active:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        session.expunge(user)
    raw, csrf = create_session(user, request)
    set_session_cookies(response, raw, csrf)
    return {"ok": True, "email": user.email, "csrf_token": csrf}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    raw = request.cookies.get("reseller_session")
    if raw:
        with db.session_scope() as session:
            auth_session = session.execute(
                select(AuthSession).where(AuthSession.token_hash == token_hash(raw))
            ).scalar_one_or_none()
            if auth_session is not None:
                session.delete(auth_session)
    clear_session_cookies(response)
    return {"ok": True}


@router.get("/api/auth/me")
def me(request: Request, context: RequestContext = Depends(require_context)):
    with db.session_scope() as session:
        rows = session.execute(
            select(models.Membership, models.Workspace)
            .join(models.Workspace, models.Workspace.id == models.Membership.workspace_id)
            .where(models.Membership.user_id == context.user.id)
            .order_by(models.Membership.created_at)
        ).all()
    return {
        "user": {
            "id": str(context.user.id),
            "email": context.user.email,
            "display_name": context.user.display_name,
        },
        "workspace": _serialize_workspace(context.workspace),
        "workspaces": [
            {**_serialize_workspace(workspace), "role": membership.role}
            for membership, workspace in rows
        ],
        "csrf_token": _csrf_from_request(request),
        "app_name": APP_NAME,
    }


@router.get("/api/app/inventory")
def inventory(
    q: str = "",
    status: str = "",
    category: str = "",
    context: RequestContext = Depends(require_context),
):
    with db.session_scope() as session:
        query = select(models.InventoryItem).where(
            models.InventoryItem.workspace_id == context.workspace.id
        )
        if status:
            query = query.where(models.InventoryItem.status == status)
        if category:
            query = query.where(models.InventoryItem.category == category)
        if q.strip():
            needle = f"%{q.strip()}%"
            query = query.where(
                models.InventoryItem.title.ilike(needle) | models.InventoryItem.sku.ilike(needle)
            )
        items = session.execute(query.order_by(models.InventoryItem.updated_at.desc()).limit(5000)).scalars().all()
        ids = [item.id for item in items]
        listings = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == context.workspace.id,
                models.ChannelListing.inventory_item_id.in_(ids) if ids else False,
            )
        ).scalars().all() if ids else []
        by_item: dict[uuid.UUID, list[models.ChannelListing]] = {}
        for listing in listings:
            if listing.inventory_item_id:
                by_item.setdefault(listing.inventory_item_id, []).append(listing)
        return {
            "items": [_serialize_item(item, by_item.get(item.id, [])) for item in items],
            "count": len(items),
        }


@router.post("/api/app/inventory")
def create_inventory_item(
    payload: InventoryCreateRequest,
    context: RequestContext = Depends(require_write_context),
):
    sku = payload.sku.strip()
    title = payload.title.strip()
    if not sku or not title:
        raise HTTPException(status_code=400, detail="SKU and title are required")
    if payload.quantity < 0:
        raise HTTPException(status_code=400, detail="Quantity cannot be negative")
    with db.session_scope() as session:
        exists = session.execute(
            select(models.InventoryItem.id).where(
                models.InventoryItem.workspace_id == context.workspace.id,
                models.InventoryItem.sku == sku,
            )
        ).scalar_one_or_none()
        if exists:
            raise HTTPException(status_code=409, detail="SKU already exists")
        item = models.InventoryItem(
            workspace_id=context.workspace.id,
            sku=sku,
            title=title,
            category=payload.category,
            quantity=payload.quantity,
            condition=payload.condition,
            cost_cents=payload.cost_cents,
            currency=payload.currency,
            location=payload.location,
            notes=payload.notes,
            status=ItemStatus.ACTIVE if payload.quantity > 0 else ItemStatus.ARCHIVED,
            attributes=payload.attributes,
        )
        session.add(item)
        session.flush()
        result = _serialize_item(item, [])
    return {"ok": True, "item": result}


@router.patch("/api/app/inventory/{item_id}")
def update_inventory_item(
    item_id: uuid.UUID,
    payload: InventoryPatchRequest,
    context: RequestContext = Depends(require_write_context),
):
    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        if item is None or item.workspace_id != context.workspace.id:
            raise HTTPException(status_code=404, detail="Inventory item not found")
        values = payload.model_dump(exclude_unset=True)
        if "quantity" in values and values["quantity"] is not None and values["quantity"] < 0:
            raise HTTPException(status_code=400, detail="Quantity cannot be negative")
        for key, value in values.items():
            setattr(item, key, value)
        session.flush()
        listings = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.inventory_item_id == item.id,
                models.ChannelListing.workspace_id == context.workspace.id,
            )
        ).scalars().all()
        result = _serialize_item(item, listings)
    return {"ok": True, "item": result}


@router.post("/api/app/inventory/{item_id}/link")
def link_listing(
    item_id: uuid.UUID,
    payload: ListingLinkRequest,
    context: RequestContext = Depends(require_write_context),
):
    try:
        listing_id = uuid.UUID(payload.listing_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid listing id") from exc
    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        listing = session.get(models.ChannelListing, listing_id)
        if (
            item is None or listing is None
            or item.workspace_id != context.workspace.id
            or listing.workspace_id != context.workspace.id
        ):
            raise HTTPException(status_code=404, detail="Item or listing not found")
        listing.inventory_item_id = item.id
    return {"ok": True}


@router.get("/api/app/today")
def today(context: RequestContext = Depends(require_context)):
    settings = strategy_settings(context.workspace.settings)
    now = datetime.now(timezone.utc)
    with db.session_scope() as session:
        listings = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == context.workspace.id,
                models.ChannelListing.status == ListingStatus.ACTIVE,
            )
        ).scalars().all()
        actions: list[dict[str, Any]] = []
        for listing in listings:
            age = max(0, (now - listing.first_seen_at).days)
            snaps = session.execute(
                select(models.ListingSnapshot)
                .where(models.ListingSnapshot.channel_listing_id == listing.id)
                .order_by(models.ListingSnapshot.captured_at.desc())
                .limit(8)
            ).scalars().all()
            latest_fav = snaps[0].favourites if snaps else None
            week_old = snaps[-1].favourites if len(snaps) > 1 else None
            fav_gain = (
                latest_fav - week_old
                if latest_fav is not None and week_old is not None and latest_fav >= week_old
                else None
            )
            suggestion = None
            priority = 0
            if age >= settings["very_stale_days"] and int(latest_fav or 0) <= settings["low_favourites"]:
                suggestion = "Refresh listing"
                priority = 100
            elif age >= settings["stale_days"] and int(latest_fav or 0) >= settings["high_favourites"]:
                suggestion = "Review price"
                priority = 90
            elif fav_gain is not None and fav_gain >= settings["momentum_favourites_7d"]:
                suggestion = "Leave alone"
                priority = 40
            if suggestion:
                actions.append({
                    "listing_id": str(listing.id),
                    "item_id": str(listing.inventory_item_id) if listing.inventory_item_id else None,
                    "title": listing.title,
                    "channel": listing.channel,
                    "action": suggestion,
                    "priority": priority,
                    "age_days": age,
                    "favourites": latest_fav,
                    "favourites_gain": fav_gain,
                    "url": listing.url,
                })
        actions.sort(key=lambda row: (-row["priority"], -row["age_days"]))
    return {"actions": actions[:50], "count": len(actions), "strategy": settings}


@router.get("/api/app/sales")
def sales(context: RequestContext = Depends(require_context)):
    with db.session_scope() as session:
        rows = session.execute(
            select(models.Sale)
            .where(models.Sale.workspace_id == context.workspace.id)
            .order_by(models.Sale.occurred_at.desc(), models.Sale.last_seen_at.desc())
            .limit(1000)
        ).scalars().all()
    return {
        "sales": [
            {
                "id": str(row.id),
                "channel": row.channel,
                "external_order_id": row.external_order_id,
                "direction": row.direction,
                "title": row.title,
                "counterparty": row.counterparty,
                "total_cents": row.total_cents,
                "currency": row.currency,
                "status": row.status,
                "is_closed": row.is_closed,
                "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
            }
            for row in rows
        ]
    }


@router.get("/api/app/analytics")
def analytics(context: RequestContext = Depends(require_context)):
    year_start = datetime(datetime.now(timezone.utc).year, 1, 1, tzinfo=timezone.utc)
    with db.session_scope() as session:
        inventory_count = session.execute(
            select(func.count(models.InventoryItem.id)).where(
                models.InventoryItem.workspace_id == context.workspace.id,
                models.InventoryItem.status == ItemStatus.ACTIVE,
            )
        ).scalar_one()
        listings_count = session.execute(
            select(func.count(models.ChannelListing.id)).where(
                models.ChannelListing.workspace_id == context.workspace.id,
                models.ChannelListing.status == ListingStatus.ACTIVE,
            )
        ).scalar_one()
        sold_rows = session.execute(
            select(models.Sale).where(
                models.Sale.workspace_id == context.workspace.id,
                models.Sale.direction == "sell",
                models.Sale.occurred_at >= year_start,
            )
        ).scalars().all()
        followers = session.execute(
            select(models.ProfileObservation)
            .join(models.ChannelAccount, models.ChannelAccount.id == models.ProfileObservation.channel_account_id)
            .where(models.ChannelAccount.workspace_id == context.workspace.id)
            .order_by(models.ProfileObservation.captured_at.desc())
            .limit(1)
        ).scalar_one_or_none()
    return {
        "active_inventory": int(inventory_count or 0),
        "active_listings": int(listings_count or 0),
        "sales_ytd_count": len(sold_rows),
        "sales_ytd_cents": sum(int(row.total_cents or 0) for row in sold_rows),
        "currency": next((row.currency for row in sold_rows if row.currency), "EUR"),
        "followers": followers.followers if followers else None,
        "following": followers.following if followers else None,
    }


@router.get("/api/app/analytics/history")
def analytics_history(
    days: int = 90,
    context: RequestContext = Depends(require_context),
):
    days = max(7, min(int(days or 90), 365))
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    with db.session_scope() as session:
        listings = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == context.workspace.id,
                models.ChannelListing.channel == Channel.VINTED,
            )
        ).scalars().all()
        listing_ids = [row.id for row in listings]
        snapshots = (
            session.execute(
                select(models.ListingSnapshot)
                .where(models.ListingSnapshot.channel_listing_id.in_(listing_ids))
                .order_by(
                    models.ListingSnapshot.channel_listing_id,
                    models.ListingSnapshot.captured_at,
                )
            ).scalars().all()
            if listing_ids else []
        )
        accounts = session.execute(
            select(models.ChannelAccount.id).where(
                models.ChannelAccount.workspace_id == context.workspace.id,
                models.ChannelAccount.channel == Channel.VINTED,
            )
        ).scalars().all()
        profiles = (
            session.execute(
                select(models.ProfileObservation)
                .where(
                    models.ProfileObservation.channel_account_id.in_(accounts),
                    models.ProfileObservation.captured_at >= since,
                )
                .order_by(models.ProfileObservation.captured_at)
            ).scalars().all()
            if accounts else []
        )
        sales_rows = session.execute(
            select(models.Sale).where(
                models.Sale.workspace_id == context.workspace.id,
                models.Sale.direction == "sell",
                models.Sale.occurred_at >= since,
            )
        ).scalars().all()

    by_listing: dict[Any, list[models.ListingSnapshot]] = {}
    daily: dict[str, dict[str, int]] = {}
    for snap in snapshots:
        by_listing.setdefault(snap.channel_listing_id, []).append(snap)

    for series in by_listing.values():
        previous = None
        for snap in series:
            if previous is not None and snap.captured_at >= since:
                key = snap.captured_at.date().isoformat()
                row = daily.setdefault(
                    key,
                    {"views_gained": 0, "favourites_gained": 0, "sales": 0, "revenue_cents": 0},
                )
                if (
                    snap.views is not None
                    and previous.views is not None
                    and snap.views >= previous.views
                ):
                    row["views_gained"] += snap.views - previous.views
                if (
                    snap.favourites is not None
                    and previous.favourites is not None
                    and snap.favourites >= previous.favourites
                ):
                    row["favourites_gained"] += snap.favourites - previous.favourites
            previous = snap

    for sale in sales_rows:
        if not sale.occurred_at:
            continue
        key = sale.occurred_at.date().isoformat()
        row = daily.setdefault(
            key,
            {"views_gained": 0, "favourites_gained": 0, "sales": 0, "revenue_cents": 0},
        )
        row["sales"] += 1
        row["revenue_cents"] += int(sale.total_cents or 0)

    profile_by_day: dict[str, dict[str, Any]] = {}
    for row in profiles:
        profile_by_day[row.captured_at.date().isoformat()] = {
            "date": row.captured_at.date().isoformat(),
            "followers": row.followers,
            "following": row.following,
        }

    listing_by_id = {row.id: row for row in listings}
    top = []
    week_cutoff = now - timedelta(days=7)
    for listing_id, series in by_listing.items():
        if not series:
            continue
        latest = series[-1]
        baseline = next((row for row in reversed(series) if row.captured_at <= week_cutoff), series[0])
        views_gain = (
            latest.views - baseline.views
            if latest.views is not None and baseline.views is not None and latest.views >= baseline.views
            else 0
        )
        fav_gain = (
            latest.favourites - baseline.favourites
            if latest.favourites is not None
            and baseline.favourites is not None
            and latest.favourites >= baseline.favourites
            else 0
        )
        listing = listing_by_id.get(listing_id)
        if listing is None:
            continue
        top.append(
            {
                "listing_id": str(listing.id),
                "item_id": str(listing.inventory_item_id) if listing.inventory_item_id else None,
                "title": listing.title,
                "url": listing.url,
                "status": listing.status,
                "views": latest.views,
                "favourites": latest.favourites,
                "views_gain_7d": views_gain,
                "favourites_gain_7d": fav_gain,
            }
        )
    top.sort(
        key=lambda row: (
            int(row["views_gain_7d"] or 0),
            int(row["favourites_gain_7d"] or 0),
            int(row["views"] or 0),
        ),
        reverse=True,
    )

    return {
        "days": days,
        "daily": [{"date": key, **daily[key]} for key in sorted(daily)],
        "followers": [profile_by_day[key] for key in sorted(profile_by_day)],
        "top_listings": top[:25],
    }


@router.get("/api/app/listings/{listing_id}/history")
def listing_history(
    listing_id: uuid.UUID,
    days: int = 180,
    context: RequestContext = Depends(require_context),
):
    days = max(7, min(int(days or 180), 730))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with db.session_scope() as session:
        listing = session.get(models.ChannelListing, listing_id)
        if listing is None or listing.workspace_id != context.workspace.id:
            raise HTTPException(status_code=404, detail="Listing not found")
        rows = session.execute(
            select(models.ListingSnapshot)
            .where(
                models.ListingSnapshot.channel_listing_id == listing.id,
                models.ListingSnapshot.captured_at >= since,
            )
            .order_by(models.ListingSnapshot.captured_at)
        ).scalars().all()
    return {
        "listing": {
            "id": str(listing.id),
            "title": listing.title,
            "channel": listing.channel,
            "url": listing.url,
            "status": listing.status,
        },
        "history": [
            {
                "captured_at": row.captured_at.isoformat(),
                "views": row.views,
                "favourites": row.favourites,
                "price_cents": row.price_cents,
                "status": row.status,
            }
            for row in rows
        ],
    }


@router.post("/api/app/import/preview")
async def import_preview(
    file: UploadFile = File(...),
    mapping_json: str = Form("{}"),
    full_snapshot: bool = Form(False),
    context: RequestContext = Depends(require_write_context),
):
    content = await file.read()
    try:
        table = parse_table(file.filename or "inventory.csv", content)
        supplied = json.loads(mapping_json or "{}")
        if not isinstance(supplied, dict):
            raise ValueError("Mapping must be an object")
        mapping = supplied if supplied else suggest_mapping(table.headers)
        mapped_fields = set(mapping.values())
        mapping_ready = "sku" in mapped_fields and "title" in mapped_fields
        with db.session_scope() as session:
            preview = (
                preview_inventory_import(
                    session,
                    context.workspace.id,
                    table.rows,
                    {str(k): str(v) for k, v in mapping.items()},
                    full_snapshot=full_snapshot,
                )
                if table.headers and mapping_ready
                else {
                    "rows": len(table.rows),
                    "counts": {"new": 0, "update": 0, "unchanged": 0, "conflict": 0},
                    "preview": [],
                    "missing_existing": [],
                    "missing_existing_count": 0,
                    "can_apply": False,
                    "full_snapshot": full_snapshot,
                    "mapping_required": True,
                }
            )
            job = ImportJob(
                workspace_id=context.workspace.id,
                user_id=context.user.id,
                filename=file.filename or "inventory",
                file_type=table.file_type,
                status="previewed",
                mapping=mapping,
                options={"full_snapshot": full_snapshot},
                summary=preview,
            )
            session.add(job)
            session.flush()
            job_id = str(job.id)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "job_id": job_id,
        "file_type": table.file_type,
        "headers": table.headers,
        "suggested_mapping": mapping,
        **preview,
    }


@router.post("/api/app/import/apply")
async def import_apply(
    file: UploadFile = File(...),
    mapping_json: str = Form(...),
    full_snapshot: bool = Form(False),
    preset_name: str = Form(""),
    context: RequestContext = Depends(require_write_context),
):
    content = await file.read()
    try:
        mapping = json.loads(mapping_json)
        if not isinstance(mapping, dict):
            raise ValueError("Mapping must be an object")
        table = parse_table(file.filename or "inventory.csv", content)
        with db.session_scope() as session:
            result = apply_inventory_import(
                session,
                context.workspace.id,
                table.rows,
                {str(k): str(v) for k, v in mapping.items()},
                full_snapshot=full_snapshot,
            )
            job = ImportJob(
                workspace_id=context.workspace.id,
                user_id=context.user.id,
                filename=file.filename or "inventory",
                file_type=table.file_type,
                status="applied",
                mapping=mapping,
                options={"full_snapshot": full_snapshot},
                summary=result,
                applied_at=utcnow(),
            )
            session.add(job)
            if preset_name.strip():
                existing = session.execute(
                    select(MappingPreset).where(
                        MappingPreset.workspace_id == context.workspace.id,
                        MappingPreset.direction == "import",
                        MappingPreset.name == preset_name.strip(),
                    )
                ).scalar_one_or_none()
                if existing is None:
                    existing = MappingPreset(
                        workspace_id=context.workspace.id,
                        name=preset_name.strip(),
                        direction="import",
                        file_type=table.file_type,
                    )
                    session.add(existing)
                existing.mapping = mapping
                existing.options = {"full_snapshot": full_snapshot}
                existing.file_type = table.file_type
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


@router.get("/api/app/mappings")
def mappings(direction: str = "", context: RequestContext = Depends(require_context)):
    with db.session_scope() as session:
        query = select(MappingPreset).where(MappingPreset.workspace_id == context.workspace.id)
        if direction:
            query = query.where(MappingPreset.direction == direction)
        rows = session.execute(query.order_by(MappingPreset.name)).scalars().all()
    return {
        "presets": [
            {
                "id": str(row.id),
                "name": row.name,
                "direction": row.direction,
                "file_type": row.file_type,
                "mapping": row.mapping,
                "options": row.options,
            }
            for row in rows
        ]
    }


@router.post("/api/app/mappings")
def save_mapping(
    payload: MappingPresetRequest,
    context: RequestContext = Depends(require_write_context),
):
    if payload.direction not in {"import", "export"}:
        raise HTTPException(status_code=400, detail="Mapping direction must be import or export")
    with db.session_scope() as session:
        row = session.execute(
            select(MappingPreset).where(
                MappingPreset.workspace_id == context.workspace.id,
                MappingPreset.direction == payload.direction,
                MappingPreset.name == payload.name.strip(),
            )
        ).scalar_one_or_none()
        if row is None:
            row = MappingPreset(
                workspace_id=context.workspace.id,
                name=payload.name.strip(),
                direction=payload.direction,
                file_type=payload.file_type,
            )
            session.add(row)
        row.mapping = payload.mapping
        row.options = payload.options
        row.file_type = payload.file_type
        session.flush()
        ident = str(row.id)
    return {"ok": True, "id": ident}


@router.get("/api/app/export")
def export_inventory(
    format: str = "csv",
    status: str = "",
    context: RequestContext = Depends(require_context),
):
    kind = format.lower()
    if kind not in {"csv", "xlsx"}:
        raise HTTPException(status_code=400, detail="Export format must be csv or xlsx")
    with db.session_scope() as session:
        query = select(models.InventoryItem).where(
            models.InventoryItem.workspace_id == context.workspace.id
        )
        if status:
            query = query.where(models.InventoryItem.status == status)
        items = session.execute(query.order_by(models.InventoryItem.sku)).scalars().all()
        rows = inventory_export_rows(items)
        job = ExportJob(
            workspace_id=context.workspace.id,
            user_id=context.user.id,
            file_type=kind,
            status="completed",
            mapping={},
            options={"status": status or "all"},
            row_count=len(rows),
            completed_at=utcnow(),
        )
        session.add(job)
    body = render_xlsx(rows) if kind == "xlsx" else render_csv(rows)
    media = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if kind == "xlsx" else "text/csv; charset=utf-8"
    )
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="inventory.{kind}"'},
    )


@router.get("/api/app/connectors")
def connectors(context: RequestContext = Depends(require_context)):
    bootstrap_slug = os.getenv("BOOTSTRAP_WORKSPACE_SLUG", "personal")
    is_bootstrap = context.workspace.slug == bootstrap_slug
    with db.session_scope() as session:
        accounts = session.execute(
            select(models.ChannelAccount).where(
                models.ChannelAccount.workspace_id == context.workspace.id
            )
        ).scalars().all()
        stored_credentials = {
            row.channel
            for row in session.execute(
                select(ConnectorCredential).where(
                    ConnectorCredential.workspace_id == context.workspace.id
                )
            ).scalars()
        }
        extension_count = session.execute(
            select(func.count(ExtensionCredential.id)).where(
                ExtensionCredential.workspace_id == context.workspace.id,
                ExtensionCredential.revoked_at.is_(None),
            )
        ).scalar_one()

    account_by_channel = {row.channel: row for row in accounts}
    result = []
    for info in connector_catalog():
        channel = info["channel"]
        account = account_by_channel.get(channel)
        configured = False
        operational = False

        if channel == Channel.VINTED:
            configured = operational = bool(extension_count)
        elif channel in {Channel.CSV, Channel.EXCEL}:
            configured = operational = True
        elif channel == Channel.BIBLIO:
            env_ready = bool(
                os.getenv("BIBLIO_FTP_USERNAME", "").strip()
                and os.getenv("BIBLIO_FTP_PASSWORD", "").strip()
            )
            configured = channel in stored_credentials or (is_bootstrap and env_ready)
            operational = configured
        elif channel == Channel.EBAY:
            env_ready = bool(
                os.getenv("EBAY_OAUTH_TOKEN", "").strip()
                or (
                    os.getenv("EBAY_CLIENT_ID", "").strip()
                    and os.getenv("EBAY_CLIENT_SECRET", "").strip()
                    and os.getenv("EBAY_REFRESH_TOKEN", "").strip()
                )
            )
            configured = channel in stored_credentials or (is_bootstrap and env_ready)
            operational = configured

        result.append(
            {
                **info,
                "configured": configured,
                "operational": operational,
                "sync_available": operational and channel in {Channel.BIBLIO, Channel.EBAY},
                "status": (
                    account.status
                    if account and operational
                    else ("connected" if operational else "disconnected")
                ),
                "last_synced_at": (
                    account.last_synced_at.isoformat()
                    if account and account.last_synced_at
                    else None
                ),
                "note": None,
            }
        )
    return {"connectors": result}


@router.put("/api/app/connectors/{channel}/credentials")
def save_connector_credentials(
    channel: str,
    payload: ConnectorCredentialsRequest,
    context: RequestContext = Depends(require_write_context),
):
    known = {row["channel"] for row in connector_catalog()}
    if channel not in known or channel in {Channel.VINTED, Channel.CSV, Channel.EXCEL}:
        raise HTTPException(status_code=400, detail="This connector does not accept stored credentials")
    cleaned = {str(k): str(v) for k, v in payload.values.items() if str(v).strip()}
    if not cleaned:
        raise HTTPException(status_code=400, detail="No credentials supplied")
    with db.session_scope() as session:
        row = session.execute(
            select(ConnectorCredential).where(
                ConnectorCredential.workspace_id == context.workspace.id,
                ConnectorCredential.channel == channel,
            )
        ).scalar_one_or_none()
        if row is None:
            row = ConnectorCredential(
                workspace_id=context.workspace.id,
                channel=channel,
                encrypted_payload=encrypt_json(cleaned),
            )
            session.add(row)
        else:
            row.encrypted_payload = encrypt_json(cleaned)
        account, _ = get_or_create_channel_account(session, context.workspace, channel, {})
        account.status = ChannelAccountStatus.DISCONNECTED
        account.config = {
            **dict(account.config or {}),
            "credentials_stored": True,
            "integration_state": "configured",
        }
    return {
        "ok": True,
        "stored_keys": sorted(cleaned),
        "encrypted": True,
        "operational": True,
    }


@router.delete("/api/app/connectors/{channel}/credentials")
def delete_connector_credentials(
    channel: str,
    context: RequestContext = Depends(require_write_context),
):
    with db.session_scope() as session:
        row = session.execute(
            select(ConnectorCredential).where(
                ConnectorCredential.workspace_id == context.workspace.id,
                ConnectorCredential.channel == channel,
            )
        ).scalar_one_or_none()
        if row is not None:
            session.delete(row)
        account = session.execute(
            select(models.ChannelAccount).where(
                models.ChannelAccount.workspace_id == context.workspace.id,
                models.ChannelAccount.channel == channel,
            )
        ).scalar_one_or_none()
        if account is not None:
            account.status = ChannelAccountStatus.DISCONNECTED
    return {"ok": True}


@router.post("/api/app/connectors/{channel}/sync")
def enqueue_connector_sync(
    channel: str,
    context: RequestContext = Depends(require_write_context),
):
    if channel not in {Channel.BIBLIO, Channel.EBAY}:
        raise HTTPException(status_code=400, detail="This connector has no server-side sync job")
    bootstrap = context.workspace.slug == os.getenv("BOOTSTRAP_WORKSPACE_SLUG", "personal")
    stored = has_workspace_connector_credentials(context.workspace.id, channel)
    if channel == Channel.BIBLIO:
        env_ready = bool(
            os.getenv("BIBLIO_FTP_USERNAME", "").strip()
            and os.getenv("BIBLIO_FTP_PASSWORD", "").strip()
        )
        if not stored and not (bootstrap and env_ready):
            raise HTTPException(status_code=400, detail="BIBLIO FTP is not configured")
    if channel == Channel.EBAY:
        env_ready = bool(
            os.getenv("EBAY_OAUTH_TOKEN", "").strip()
            or (
                os.getenv("EBAY_CLIENT_ID", "").strip()
                and os.getenv("EBAY_CLIENT_SECRET", "").strip()
                and os.getenv("EBAY_REFRESH_TOKEN", "").strip()
            )
        )
        if not stored and not (bootstrap and env_ready):
            raise HTTPException(status_code=400, detail="eBay OAuth is not configured")
    job_id = jobs.enqueue(f"{channel}_sync", {}, context.workspace.id)
    return {"ok": True, "job_id": str(job_id), "queued": True}


@router.post("/api/app/connectors/biblio/import")
async def biblio_workspace_import(
    file: UploadFile = File(...),
    context: RequestContext = Depends(require_write_context),
):
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("latin-1")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="Could not decode BIBLIO inventory file") from exc
    try:
        rows = parse_biblio_inventory(text, currency="EUR")
        result = import_biblio_workspace(
            context.workspace.id,
            rows,
            filename=file.filename or "BIBLIO inventory",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


@router.post("/api/app/connectors/biblio/test")
def biblio_workspace_test(
    context: RequestContext = Depends(require_write_context),
):
    if has_workspace_connector_credentials(context.workspace.id, Channel.BIBLIO):
        try:
            return test_biblio_workspace(context.workspace.id)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if context.workspace.slug == os.getenv("BOOTSTRAP_WORKSPACE_SLUG", "personal"):
        from app.channels import test_biblio_ftp
        try:
            return test_biblio_ftp()
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail="BIBLIO FTP is not configured")


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
    return {
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
        ]
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
        workspace = session.get(models.Workspace, pairing.workspace_id)
        session.flush()
        workspace_name = workspace.name if workspace else "Workspace"
    return {
        "ok": True,
        "token": raw_token,
        "workspace": workspace_name,
        "app_name": APP_NAME,
    }


@router.get("/api/extension/status")
def extension_status(context: RequestContext = Depends(extension_context)):
    latest = os.getenv("EXTENSION_LATEST_VERSION", "2.0.0")
    return {
        "ok": True,
        "workspace": _serialize_workspace(context.workspace),
        "app_name": APP_NAME,
        "latest_version": latest,
        "installed_version": context.extension.extension_version if context.extension else None,
    }


@router.post("/api/extension/browser-sync")
def extension_browser_sync(
    payload: WorkspaceBrowserSyncPayload,
    context: RequestContext = Depends(extension_context),
):
    data = payload.model_dump()
    version = payload.extension_version or (
        context.extension.extension_version if context.extension else None
    )
    result = record_workspace_snapshot(context.workspace.id, data, extension_version=version)
    maybe_record_legacy_snapshot(context.workspace.slug, data)
    if context.extension is not None and version:
        with db.session_scope() as session:
            row = session.get(ExtensionCredential, context.extension.id)
            if row is not None:
                row.extension_version = version
                row.last_seen_at = utcnow()
    return {"ok": True, **result}


@router.get("/api/extension/market-research/queue")
def extension_market_queue(context: RequestContext = Depends(extension_context)):
    # Store builds keep unattended scraping disabled.  The legacy development
    # extension still has its old explicit endpoint for the personal setup.
    enabled = os.getenv("EXTENSION_MARKET_RESEARCH_ENABLED", "false").lower() in {
        "1", "true", "yes", "on"
    }
    return {"jobs": [], "enabled": enabled}


@router.get("/api/app/settings")
def get_settings(context: RequestContext = Depends(require_context)):
    info = billing.summary(context.workspace)
    return {
        "workspace": _serialize_workspace(context.workspace),
        "app_name": APP_NAME,
        "billing": {
            "enabled": info.enabled,
            "provider": info.provider,
            "status": info.status,
            "customer_configured": bool(info.customer_id),
        },
        "security": {
            "derived_encryption_key": using_derived_key(),
            "cookie_secure": os.getenv("COOKIE_SECURE", "false").lower() in {"1", "true", "yes", "on"},
        },
    }


@router.patch("/api/app/settings")
def update_settings(
    payload: SettingsRequest,
    context: RequestContext = Depends(require_write_context),
):
    with db.session_scope() as session:
        workspace = session.get(models.Workspace, context.workspace.id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="Workspace not found")
        if payload.name is not None and payload.name.strip():
            workspace.name = payload.name.strip()[:200]
        if payload.settings is not None:
            workspace.settings = payload.settings
        session.flush()
        result = _serialize_workspace(workspace)
    return {"ok": True, "workspace": result}


@router.post("/api/app/billing/checkout")
def billing_checkout(context: RequestContext = Depends(require_write_context)):
    try:
        url = billing.create_checkout(context.workspace, context.user.email)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"url": url}


@router.post("/api/app/billing/portal")
def billing_portal(context: RequestContext = Depends(require_write_context)):
    try:
        url = billing.create_portal(context.workspace)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"url": url}


@router.post("/api/billing/webhook")
async def billing_webhook(request: Request):
    payload = await request.body()
    try:
        event = billing.verify_stripe_webhook(
            payload, request.headers.get("stripe-signature") or ""
        )
        billing.apply_stripe_event(event)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"received": True}


@router.get("/api/app/account/export")
def account_export(context: RequestContext = Depends(require_context)):
    with db.session_scope() as session:
        items = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == context.workspace.id
            )
        ).scalars().all()
        listings = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == context.workspace.id
            )
        ).scalars().all()
        sales_rows = session.execute(
            select(models.Sale).where(models.Sale.workspace_id == context.workspace.id)
        ).scalars().all()
    body = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "workspace": _serialize_workspace(context.workspace),
        "inventory": [_serialize_item(row, []) for row in items],
        "listings": [
            {
                "id": str(row.id),
                "inventory_item_id": str(row.inventory_item_id) if row.inventory_item_id else None,
                "channel": row.channel,
                "external_id": row.external_id,
                "title": row.title,
                "status": row.status,
                "price_cents": row.price_cents,
                "currency": row.currency,
                "url": row.url,
            }
            for row in listings
        ],
        "sales": [
            {
                "id": str(row.id),
                "channel": row.channel,
                "direction": row.direction,
                "external_order_id": row.external_order_id,
                "title": row.title,
                "total_cents": row.total_cents,
                "currency": row.currency,
                "status": row.status,
            }
            for row in sales_rows
        ],
    }
    return Response(
        content=json.dumps(body, ensure_ascii=False, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="account-data.json"'},
    )


@router.delete("/api/app/account")
def delete_workspace(
    payload: DeleteWorkspaceRequest,
    response: Response,
    context: RequestContext = Depends(require_write_context),
):
    if payload.confirm != context.workspace.slug:
        raise HTTPException(
            status_code=400,
            detail=f"Type the workspace slug '{context.workspace.slug}' to confirm deletion",
        )
    with db.session_scope() as session:
        workspace = session.get(models.Workspace, context.workspace.id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="Workspace not found")
        membership_count = session.execute(
            select(func.count(models.Membership.id)).where(
                models.Membership.user_id == context.user.id
            )
        ).scalar_one()
        session.delete(workspace)
        if int(membership_count or 0) <= 1:
            user = session.get(models.User, context.user.id)
            if user is not None:
                user.is_active = False
    clear_session_cookies(response)
    return {"ok": True}
