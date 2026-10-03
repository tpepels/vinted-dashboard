"""Workspace-scoped product API.

This router is the commercial/hosted surface.  Legacy personal-dashboard
routes remain available separately for backwards compatibility.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select

from app import billing, db, jobs, listing_assistant, models, publishing, stock_intake
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
    KNOWN_ITEM_CATEGORIES,
)
from app.connectors.base import connector_catalog
from app.connectors.hosted import (
    exchange_etsy_authorization_code,
    has_credentials as has_workspace_connector_credentials,
    import_biblio_workspace,
    test_biblio_workspace,
    test_bigcommerce_workspace,
    test_depop_workspace,
    test_etsy_workspace,
    test_shopify_workspace,
    test_squarespace_workspace,
    test_wix_workspace,
    test_woocommerce_workspace,
)
from app.connectors.workspace_sync import recompute_inventory_item
from app.cross_channel import (
    acknowledge_manual_action,
    reconcile_sale_state,
    retry_action,
    serialize_actions,
    unlinked_sale_reconciliation,
)
from app.crypto import decrypt_json, encrypt_json, using_derived_key
from app.import_export import (
    apply_inventory_import,
    inventory_export_rows,
    parse_table,
    preview_inventory_import,
    EXPORT_HEADERS,
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
    BackgroundJob,
)
from app.reconciliation import apply_reconciliation_merges, reconciliation_suggestions
from app.purchase_costs import apply_purchase_cost, purchase_cost_suggestions
from app.runtime_config import public_app_origin
from app.stock_policy import sale_counts_as_sold
from app.strategy import strategy_settings
from app.vinted_analytics import build_vinted_analytics, daily_snapshot_series
from pathlib import Path
import json
from app.workspace_bootstrap import (
    BOOTSTRAP_OWNER_EMAIL,
    get_or_create_channel_account,
)
from app.workspace_ingest import maybe_record_legacy_snapshot, record_workspace_snapshot


router = APIRouter()
APP_NAME = os.getenv("APP_NAME", "Reseller Dashboard").strip() or "Reseller Dashboard"


ETSY_OAUTH_SCOPES = ("listings_r", "transactions_r")
ETSY_OAUTH_CALLBACK_PATH = "/api/app/connectors/etsy/oauth/callback"
ETSY_OAUTH_TTL = timedelta(minutes=10)


def _etsy_oauth_redirect_uri() -> str | None:
    origin = public_app_origin()
    return f"{origin}{ETSY_OAUTH_CALLBACK_PATH}" if origin else None


def _biblio_configured_for_workspace(workspace: models.Workspace) -> bool:
    if has_workspace_connector_credentials(workspace.id, Channel.BIBLIO):
        return True
    bootstrap_slug = os.getenv("BOOTSTRAP_WORKSPACE_SLUG", "personal")
    if workspace.slug != bootstrap_slug:
        return False
    return bool(
        os.getenv("BIBLIO_FTP_USERNAME", "").strip()
        and os.getenv("BIBLIO_FTP_PASSWORD", "").strip()
    )


def _etsy_oauth_authorized(values: dict[str, Any]) -> bool:
    return bool(
        str(values.get("oauth_token") or "").strip()
        or str(values.get("refresh_token") or "").strip()
    )


def _etsy_pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


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


class InventoryBulkRequest(BaseModel):
    item_ids: list[uuid.UUID]
    category: str | None = None
    condition: str | None = None
    cost_cents: int | None = None
    default_price_cents: int | None = None
    currency: str | None = None
    location: str | None = None
    status: str | None = None


class QuickListingCreateRequest(BaseModel):
    sku: str | None = None
    title: str
    description: str = ""
    category: str = ItemCategory.GENERAL
    item_type: str | None = None
    brand: str | None = None
    size: str | None = None
    colour: str | None = None
    material: str | None = None
    condition: str | None = None
    author: str | None = None
    isbn: str | None = None
    publisher: str | None = None
    edition: str | None = None
    measurements: str | None = None
    waist_cm: str | None = None
    inside_leg_cm: str | None = None
    pit_to_pit_cm: str | None = None
    length_cm: str | None = None
    price_cents: int
    cost_cents: int | None = None
    currency: str = "EUR"
    location: str | None = None
    notes: str | None = None
    photo_count: int = 0
    analysis_used: bool = False


class BiblioPublishRequest(BaseModel):
    source_listing_id: uuid.UUID | None = None
    title: str | None = None
    author: str | None = None
    description: str | None = None
    isbn: str | None = None
    price_cents: int | None = Field(default=None, ge=0)


class BarcodeLookupRequest(BaseModel):
    code: str


class StockIntakeItemRequest(BaseModel):
    sku: str | None = None
    barcode: str | None = None
    barcode_format: str | None = None
    title: str
    category: str = ItemCategory.GENERAL
    condition: str | None = None
    cost_cents: int | None = Field(default=None, ge=0)
    price_cents: int | None = Field(default=None, ge=0)
    currency: str = "EUR"
    location: str | None = None
    notes: str | None = None
    author: str | None = None
    isbn: str | None = None
    publisher: str | None = None
    edition: str | None = None
    publication_year: int | None = None
    cover_url: str | None = None
    source_url: str | None = None


class StockIntakeBatchRequest(BaseModel):
    items: list[StockIntakeItemRequest] = Field(min_length=1, max_length=100)


class OnboardingRequest(BaseModel):
    primary_category: str | None = None
    completed: bool | None = None


class ListingLinkRequest(BaseModel):
    listing_id: str


class ReconciliationMergeInstruction(BaseModel):
    target_item_id: uuid.UUID
    source_item_id: uuid.UUID


class ReconciliationApplyRequest(BaseModel):
    merges: list[ReconciliationMergeInstruction]


class SaleLinkRequest(BaseModel):
    inventory_item_id: uuid.UUID


class PurchaseCostApplyRequest(BaseModel):
    inventory_item_id: uuid.UUID
    cost_cents: int = Field(ge=0)


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
        "trial_ends_at": workspace.trial_ends_at.isoformat() if workspace.trial_ends_at else None,
        "settings": dict(workspace.settings or {}),
    }


def _effective_item_ask(
    item: models.InventoryItem,
    listings: list[models.ChannelListing] | None = None,
) -> tuple[int | None, str | None]:
    attributes = dict(item.attributes or {})
    default_price = attributes.get("default_price_cents")
    if default_price is not None:
        return int(default_price), "default"

    active_prices = [
        int(row.price_cents)
        for row in (listings or [])
        if row.status == ListingStatus.ACTIVE and row.price_cents is not None
    ]
    if not active_prices:
        return None, None
    # One physical item can be listed on several channels.  Use the lowest
    # live asking price as the conservative single-stock valuation rather than
    # double-counting the same physical item.
    return min(active_prices), "marketplace"


def _serialize_item(item: models.InventoryItem, listings: list[models.ChannelListing] | None = None) -> dict[str, Any]:
    attributes = dict(item.attributes or {})
    default_price = attributes.get("default_price_cents")
    effective_ask, effective_ask_source = _effective_item_ask(item, listings)
    potential_margin = (
        int(effective_ask) - int(item.cost_cents)
        if effective_ask is not None and item.cost_cents is not None
        else None
    )
    return {
        "id": str(item.id),
        "sku": item.sku,
        "title": item.title,
        "category": item.category,
        "quantity": item.quantity,
        "condition": item.condition,
        "cost_cents": item.cost_cents,
        "default_price_cents": default_price,
        "effective_ask_cents": effective_ask,
        "effective_ask_source": effective_ask_source,
        "potential_margin_cents": potential_margin,
        "currency": item.currency,
        "location": item.location,
        "notes": item.notes,
        "status": item.status,
        "attributes": attributes,
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
            billing_status, trial_ends_at = billing.new_workspace_billing()
            workspace = models.Workspace(
                name=name,
                slug=_unique_slug(session, name),
                is_personal=True,
                billing_status=billing_status,
                trial_ends_at=trial_ends_at,
                settings={
                    "onboarding": {
                        "completed": False,
                        "primary_category": None,
                    }
                },
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
        "billing": {
            "enabled": billing.BILLING_ENABLED,
            **billing.write_access(context.workspace),
        },
    }


@router.get("/api/app/onboarding")
def onboarding(context: RequestContext = Depends(require_context)):
    with db.session_scope() as session:
        inventory_count = session.execute(
            select(func.count(models.InventoryItem.id)).where(
                models.InventoryItem.workspace_id == context.workspace.id
            )
        ).scalar_one()
        active_devices = session.execute(
            select(func.count(ExtensionCredential.id)).where(
                ExtensionCredential.workspace_id == context.workspace.id,
                ExtensionCredential.revoked_at.is_(None),
            )
        ).scalar_one()
        accounts = session.execute(
            select(models.ChannelAccount).where(
                models.ChannelAccount.workspace_id == context.workspace.id
            )
        ).scalars().all()
        connected_channels = sorted({
            account.channel
            for account in accounts
            if account.status == ChannelAccountStatus.CONNECTED
        })
        suggestions = reconciliation_suggestions(session, context.workspace.id)

    workspace_settings = dict(context.workspace.settings or {})
    saved = dict(workspace_settings.get("onboarding") or {})
    explicit = bool(saved)
    completed = bool(saved.get("completed")) if explicit else bool(inventory_count)
    saved_category = saved.get("primary_category")
    primary_category = str(saved_category or ItemCategory.GENERAL)
    if primary_category not in {*KNOWN_ITEM_CATEGORIES, "mixed"}:
        primary_category = ItemCategory.GENERAL

    return {
        "completed": completed,
        "primary_category": primary_category,
        "existing_workspace": not explicit and bool(inventory_count),
        "inventory_count": int(inventory_count or 0),
        "connected_channels": connected_channels,
        "vinted_bridge_paired": bool(active_devices),
        "reconciliation_count": len(suggestions),
        "steps": {
            "choose_category": saved_category in {*KNOWN_ITEM_CATEGORIES, "mixed"},
            "stock_loaded": bool(inventory_count),
            "marketplace_connected": bool(active_devices or connected_channels),
            "matches_reviewed": not bool(suggestions),
        },
    }


@router.put("/api/app/onboarding")
def update_onboarding(
    payload: OnboardingRequest,
    context: RequestContext = Depends(require_write_context),
):
    allowed = {*KNOWN_ITEM_CATEGORIES, "mixed"}
    if payload.primary_category is not None and payload.primary_category not in allowed:
        raise HTTPException(status_code=400, detail="Unknown primary inventory category")
    with db.session_scope() as session:
        workspace = session.get(models.Workspace, context.workspace.id)
        settings = dict(workspace.settings or {})
        onboarding = dict(settings.get("onboarding") or {})
        if payload.primary_category is not None:
            onboarding["primary_category"] = payload.primary_category
        if payload.completed is not None:
            onboarding["completed"] = bool(payload.completed)
        settings["onboarding"] = onboarding
        workspace.settings = settings
        session.flush()
        result = dict(onboarding)
    return {"ok": True, "onboarding": result}


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


@router.get("/api/app/listings")
def listings(
    channel: str = "",
    context: RequestContext = Depends(require_context),
):
    """Workspace listing feed with the latest analytics snapshot.

    Filtering/sorting is intentionally done in the product UI so the old
    Vinted-style controls remain instant while the same view can include
    multiple marketplaces.
    """
    with db.session_scope() as session:
        latest_snapshot = (
            select(
                models.ListingSnapshot.channel_listing_id.label("listing_id"),
                func.max(models.ListingSnapshot.captured_at).label("captured_at"),
            )
            .group_by(models.ListingSnapshot.channel_listing_id)
            .subquery()
        )
        query = (
            select(models.ChannelListing, models.ListingSnapshot)
            .outerjoin(
                latest_snapshot,
                latest_snapshot.c.listing_id == models.ChannelListing.id,
            )
            .outerjoin(
                models.ListingSnapshot,
                and_(
                    models.ListingSnapshot.channel_listing_id == models.ChannelListing.id,
                    models.ListingSnapshot.captured_at == latest_snapshot.c.captured_at,
                ),
            )
            .where(models.ChannelListing.workspace_id == context.workspace.id)
        )
        if channel.strip():
            query = query.where(models.ChannelListing.channel == channel.strip().lower())
        rows = session.execute(
            query.order_by(models.ChannelListing.first_seen_at.desc()).limit(5000)
        ).all()
        linked_item_ids = {
            listing.inventory_item_id
            for listing, _snapshot in rows
            if listing.inventory_item_id is not None
        }
        linked_items = (
            session.execute(
                select(models.InventoryItem).where(
                    models.InventoryItem.workspace_id == context.workspace.id,
                    models.InventoryItem.id.in_(linked_item_ids),
                )
            ).scalars().all()
            if linked_item_ids
            else []
        )
        item_category_by_id = {item.id: item.category for item in linked_items}

    result = []
    for listing, snapshot in rows:
        listed_at = (listing.extra or {}).get("listed_at")
        listed_at_source = (
            str((listing.extra or {}).get("listed_at_source") or "vinted")
            if listed_at
            else None
        )
        result.append(
            {
                "id": str(listing.id),
                "inventory_item_id": (
                    str(listing.inventory_item_id) if listing.inventory_item_id else None
                ),
                "inventory_category": (
                    item_category_by_id.get(listing.inventory_item_id)
                    if listing.inventory_item_id
                    else None
                ),
                "channel": listing.channel,
                "external_id": listing.external_id,
                "external_sku": listing.external_sku,
                "title": listing.title,
                "status": listing.status,
                "price_cents": listing.price_cents,
                "currency": listing.currency,
                "quantity": listing.quantity,
                "url": listing.url,
                "listed_at": listed_at,
                "listed_at_source": listed_at_source,
                "first_seen_at": (
                    listing.first_seen_at.isoformat() if listing.first_seen_at else None
                ),
                "last_seen_at": (
                    listing.last_seen_at.isoformat() if listing.last_seen_at else None
                ),
                "views": snapshot.views if snapshot else None,
                "favourites": snapshot.favourites if snapshot else None,
                "snapshot_at": (
                    snapshot.captured_at.isoformat() if snapshot else None
                ),
            }
        )
    return {"listings": result, "count": len(result)}


@router.get("/api/app/listing-assistant/status")
def listing_assistant_status(
    context: RequestContext = Depends(require_context),
):
    return {
        **listing_assistant.provider_status(),
        "manual_workflow_available": True,
    }


@router.post("/api/app/listing-assistant/analyze")
async def listing_assistant_analyze(
    request: Request,
    photos: list[UploadFile] = File(...),
    hints_json: str = Form("{}"),
    context: RequestContext = Depends(require_write_context),
):
    rate_limiter.check(
        f"listing-analysis:{context.user.id}",
        limit=30,
        window_seconds=900,
    )
    try:
        hints = json.loads(hints_json or "{}")
        if not isinstance(hints, dict):
            raise ValueError("Listing hints must be an object")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Listing hints are invalid JSON") from exc

    selected: list[tuple[str, bytes]] = []
    try:
        if len(photos) > listing_assistant.MAX_PHOTOS:
            raise ValueError(
                f"Use at most {listing_assistant.MAX_PHOTOS} photos"
            )
        for photo in photos:
            body = await photo.read(listing_assistant.MAX_PHOTO_BYTES + 1)
            if len(body) > listing_assistant.MAX_PHOTO_BYTES:
                raise ValueError("Each photo must be 8 MB or smaller")
            selected.append((str(photo.content_type or ""), body))
        result = listing_assistant.analyze_photos(selected, hints=hints)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "analysis": result,
        "photo_count": len(selected),
        "photos_stored": False,
    }


@router.post("/api/app/listing-assistant/create")
def listing_assistant_create(
    payload: QuickListingCreateRequest,
    context: RequestContext = Depends(require_write_context),
):
    values = payload.model_dump()
    values["listing_creation_source"] = (
        "photo_ai" if payload.analysis_used else "quick_listing"
    )
    try:
        with db.session_scope() as session:
            item = listing_assistant.create_master_item(
                session,
                context.workspace.id,
                values,
            )
            result = _serialize_item(item, [])
            package = listing_assistant.listing_package(item)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "ok": True,
        "item": result,
        "listing_package": package,
    }


@router.post("/api/app/stock-intake/barcode/decode")
async def stock_intake_barcode_decode(
    image: UploadFile = File(...),
    context: RequestContext = Depends(require_context),
):
    rate_limiter.check(
        f"barcode-decode:{context.user.id}",
        limit=1200,
        window_seconds=900,
    )
    body = await image.read(stock_intake.MAX_BARCODE_IMAGE_BYTES + 1)
    try:
        barcodes = stock_intake.decode_barcode_image(
            str(image.content_type or ""),
            body,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"barcodes": barcodes, "count": len(barcodes)}


@router.post("/api/app/stock-intake/barcode/lookup")
def stock_intake_barcode_lookup(
    payload: BarcodeLookupRequest,
    context: RequestContext = Depends(require_context),
):
    rate_limiter.check(
        f"barcode-lookup:{context.user.id}",
        limit=240,
        window_seconds=900,
    )
    info = stock_intake.classify_barcode(payload.code)
    if not info["code"]:
        raise HTTPException(status_code=400, detail="Barcode is empty")

    metadata = None
    warning = None
    if info["kind"] == "isbn":
        try:
            metadata = stock_intake.lookup_isbn(str(info["isbn"]))
        except (ValueError, RuntimeError) as exc:
            warning = str(exc)

    existing: list[dict[str, Any]] = []
    isbn = str(info.get("isbn") or "").strip()
    if isbn:
        with db.session_scope() as session:
            rows = session.execute(
                select(models.InventoryItem).where(
                    models.InventoryItem.workspace_id == context.workspace.id,
                    models.InventoryItem.category == ItemCategory.BOOK,
                )
            ).scalars().all()
            for row in rows:
                attributes = dict(row.attributes or {})
                if stock_intake.normalize_barcode(attributes.get("isbn")) != isbn:
                    continue
                existing.append(
                    {
                        "id": str(row.id),
                        "sku": row.sku,
                        "title": row.title,
                        "condition": row.condition,
                        "location": row.location,
                        "status": row.status,
                    }
                )

    return {
        **info,
        "metadata": metadata,
        "metadata_warning": warning,
        "existing_copies": existing,
        "existing_copy_count": len(existing),
    }


@router.post("/api/app/stock-intake/items")
def stock_intake_create_items(
    payload: StockIntakeBatchRequest,
    context: RequestContext = Depends(require_write_context),
):
    created: list[dict[str, Any]] = []
    try:
        with db.session_scope() as session:
            for incoming in payload.items:
                values = incoming.model_dump()
                title = str(values.get("title") or "").strip()
                if not title:
                    raise ValueError("Every stock item needs a title")
                category = str(values.get("category") or ItemCategory.GENERAL).strip().lower()
                if category not in KNOWN_ITEM_CATEGORIES:
                    raise ValueError(f"Unknown item category: {category}")
                currency = str(values.get("currency") or "EUR").strip().upper()
                if len(currency) != 3 or not currency.isalpha():
                    raise ValueError("Currency must be a 3-letter code")
                sku = str(values.get("sku") or "").strip() or listing_assistant.auto_sku(
                    session,
                    context.workspace.id,
                    category,
                )
                duplicate = session.execute(
                    select(models.InventoryItem.id).where(
                        models.InventoryItem.workspace_id == context.workspace.id,
                        models.InventoryItem.sku == sku,
                    )
                ).scalar_one_or_none()
                if duplicate is not None:
                    raise ValueError(f"SKU already exists: {sku}")

                attributes: dict[str, Any] = {
                    "stock_intake_source": "barcode_scan",
                }
                barcode = stock_intake.normalize_barcode(values.get("barcode"))
                if barcode:
                    attributes["barcode"] = barcode
                barcode_format = str(values.get("barcode_format") or "").strip()
                if barcode_format:
                    attributes["barcode_format"] = barcode_format
                for key in (
                    "author",
                    "publisher",
                    "edition",
                    "cover_url",
                    "source_url",
                ):
                    value = values.get(key)
                    if value not in (None, ""):
                        attributes[key] = value
                if values.get("publication_year") is not None:
                    attributes["publication_year"] = int(values["publication_year"])
                isbn = stock_intake.normalize_barcode(values.get("isbn"))
                if isbn:
                    attributes["isbn"] = isbn
                if values.get("price_cents") is not None:
                    attributes["default_price_cents"] = int(values["price_cents"])

                item = models.InventoryItem(
                    workspace_id=context.workspace.id,
                    sku=sku,
                    title=title,
                    category=category,
                    quantity=1,
                    condition=str(values.get("condition") or "").strip() or None,
                    cost_cents=(
                        int(values["cost_cents"])
                        if values.get("cost_cents") is not None
                        else None
                    ),
                    currency=currency,
                    location=str(values.get("location") or "").strip() or None,
                    notes=str(values.get("notes") or "").strip() or None,
                    status=ItemStatus.ACTIVE,
                    attributes=attributes,
                )
                session.add(item)
                session.flush()
                created.append(_serialize_item(item, []))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "created": created, "count": len(created)}


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


@router.post("/api/app/inventory/bulk")
def bulk_update_inventory(
    payload: InventoryBulkRequest,
    context: RequestContext = Depends(require_write_context),
):
    item_ids = list(dict.fromkeys(payload.item_ids))
    if not item_ids:
        raise HTTPException(status_code=400, detail="Select at least one inventory item")
    if len(item_ids) > 1000:
        raise HTTPException(status_code=400, detail="Bulk edit is limited to 1000 items")
    values = payload.model_dump(exclude_unset=True)
    values.pop("item_ids", None)
    if not values:
        raise HTTPException(status_code=400, detail="Choose at least one field to update")
    if "category" in values:
        if values["category"] not in KNOWN_ITEM_CATEGORIES:
            raise HTTPException(status_code=400, detail="Unknown item category")
    if "status" in values:
        if values["status"] not in {
            ItemStatus.ACTIVE,
            ItemStatus.SOLD,
            ItemStatus.ARCHIVED,
        }:
            raise HTTPException(status_code=400, detail="Unknown item status")
    if "currency" in values:
        currency = str(values["currency"] or "").strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise HTTPException(status_code=400, detail="Currency must be a 3-letter code")
        values["currency"] = currency
    for money_field in ("cost_cents", "default_price_cents"):
        if values.get(money_field) is not None and int(values[money_field]) < 0:
            raise HTTPException(status_code=400, detail=f"{money_field} cannot be negative")

    with db.session_scope() as session:
        items = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == context.workspace.id,
                models.InventoryItem.id.in_(item_ids),
            )
        ).scalars().all()
        if len(items) != len(item_ids):
            raise HTTPException(status_code=404, detail="One or more inventory items were not found")
        for item in items:
            for field in ("category", "condition", "cost_cents", "currency", "location", "status"):
                if field in values:
                    setattr(item, field, values[field])
            if "default_price_cents" in values:
                attributes = dict(item.attributes or {})
                attributes["default_price_cents"] = values["default_price_cents"]
                item.attributes = attributes
        session.flush()
    return {"ok": True, "updated": len(items)}


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
        if "quantity" in values and "status" not in values:
            item.status = ItemStatus.ACTIVE if int(item.quantity or 0) > 0 else ItemStatus.ARCHIVED
        session.flush()
        listings = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.inventory_item_id == item.id,
                models.ChannelListing.workspace_id == context.workspace.id,
            )
        ).scalars().all()
        result = _serialize_item(item, listings)
    return {"ok": True, "item": result}


@router.get("/api/app/inventory/{item_id}/publish/biblio")
def biblio_publish_preview(
    item_id: uuid.UUID,
    source_listing_id: uuid.UUID | None = None,
    context: RequestContext = Depends(require_context),
):
    try:
        with db.session_scope() as session:
            candidate = publishing.build_biblio_candidate(
                session,
                context.workspace.id,
                item_id,
                source_listing_id=source_listing_id,
                enrich_isbn=True,
            )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    configured = _biblio_configured_for_workspace(context.workspace)
    return {
        **candidate,
        "configured": configured,
        "publish_ready": configured and bool(candidate.get("ready")),
        "action": "update" if candidate.get("already_listed") else "publish",
    }


@router.post("/api/app/inventory/{item_id}/publish/biblio")
def biblio_publish(
    item_id: uuid.UUID,
    payload: BiblioPublishRequest,
    context: RequestContext = Depends(require_write_context),
):
    if not _biblio_configured_for_workspace(context.workspace):
        raise HTTPException(
            status_code=400,
            detail="Connect BIBLIO in Connections before publishing.",
        )
    try:
        with db.session_scope() as session:
            candidate = publishing.build_biblio_candidate(
                session,
                context.workspace.id,
                item_id,
                source_listing_id=payload.source_listing_id,
                enrich_isbn=True,
            )
            overrides = payload.model_dump(exclude_none=True)
            overrides.pop("source_listing_id", None)
            if overrides:
                candidate = publishing.apply_biblio_overrides(candidate, overrides)
            if candidate.get("missing"):
                raise ValueError(
                    "BIBLIO listing is missing: "
                    + ", ".join(str(value) for value in candidate["missing"])
                )
            workspace = session.get(models.Workspace, context.workspace.id)
            if workspace is None:
                raise ValueError("Workspace does not exist")
            listing = publishing.upsert_biblio_listing(
                session,
                workspace,
                item_id,
                candidate,
            )
            listing_id = str(listing.id)
        job_id = jobs.enqueue("biblio_sync", {"listing_id": listing_id}, context.workspace.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "listing_id": listing_id,
        "job_id": str(job_id),
        "queued": True,
        "candidate": candidate,
    }


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
        previous_item_id = listing.inventory_item_id
        listing.inventory_item_id = item.id
        session.flush()
        recompute_inventory_item(session, item)
        if previous_item_id and previous_item_id != item.id:
            previous = session.get(models.InventoryItem, previous_item_id)
            if previous is not None and previous.workspace_id == context.workspace.id:
                recompute_inventory_item(session, previous)
    return {"ok": True}


@router.get("/api/app/reconciliation")
def reconciliation(context: RequestContext = Depends(require_context)):
    with db.session_scope() as session:
        suggestions = reconciliation_suggestions(session, context.workspace.id)
    return {
        "suggestions": suggestions,
        "count": len(suggestions),
        "high_confidence": sum(1 for row in suggestions if row["confidence"] == "high"),
        "policy": {
            "automatic_merges": False,
            "fuzzy_matching": False,
            "signals": [
                "exact SKU",
                "exact ISBN",
                "exact title + author",
                "exact title + publisher + publication year",
                "exact title + brand + size",
            ],
        },
    }


@router.post("/api/app/reconciliation/apply")
def apply_reconciliation(
    payload: ReconciliationApplyRequest,
    context: RequestContext = Depends(require_write_context),
):
    merge_pairs = [(row.target_item_id, row.source_item_id) for row in payload.merges]
    try:
        with db.session_scope() as session:
            results = apply_reconciliation_merges(
                session,
                context.workspace.id,
                merge_pairs,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "merged": results,
        "count": sum(len(row["merged_source_item_ids"]) for row in results),
    }


@router.get("/api/app/today")
def today(context: RequestContext = Depends(require_context)):
    settings = strategy_settings(context.workspace.settings)
    now = datetime.now(timezone.utc)
    with db.session_scope() as session:
        behavior = build_vinted_analytics(
            session,
            context.workspace.id,
            days=30,
            strategy=settings,
            now=now,
            listing_limit=None,
        )
        segment_actions = {
            "low_interest_stale": ("Refresh listing", 80),
            "high_interest_stale": ("Review price", 75),
        }
        actions: list[dict[str, Any]] = []
        work_queue: list[dict[str, Any]] = []

        for row in behavior.get("listings") or []:
            suggestion = segment_actions.get(row.get("segment"))
            if suggestion is None:
                continue
            label, priority = suggestion
            action = {
                "listing_id": row["listing_id"],
                "item_id": row.get("item_id"),
                "title": row["title"],
                "channel": Channel.VINTED,
                "action": label,
                "priority": priority,
                "age_days": row["age_days"],
                "favourites": row["favourites"],
                "favourites_gain": row["favourites_gain_7d"],
                "views_gain": row["views_gain_7d"],
                "url": row.get("url"),
            }
            actions.append(action)
            detail_parts = [f'{row["age_days"]} days online']
            if row.get("favourites") is not None:
                detail_parts.append(f'{row["favourites"]} favourites')
            if row.get("views_gain_7d"):
                detail_parts.append(f'+{row["views_gain_7d"]} views in 7d')
            work_queue.append(
                {
                    "id": f'listing:{row["listing_id"]}',
                    "kind": "listing",
                    "priority": priority,
                    "title": row["title"],
                    "label": label,
                    "detail": " · ".join(detail_parts),
                    "view": "listings",
                    "url": row.get("url"),
                }
            )

        actions.sort(key=lambda row: (-row["priority"], -row["age_days"]))

        cross_channel_actions = serialize_actions(session, context.workspace.id, limit=50)
        stock_attention = [
            row for row in cross_channel_actions
            if row["status"] in {"queued", "running", "attention", "error"}
        ]
        for row in stock_attention:
            title = (row.get("item") or {}).get("title") or (row.get("listing") or {}).get("title") or "Sold item"
            priority = 120 if row["status"] == "error" else 115 if row["status"] == "attention" else 90
            work_queue.append(
                {
                    "id": f'stock:{row["id"]}',
                    "kind": "stock_action",
                    "priority": priority,
                    "title": title,
                    "label": "Close sold stock listing",
                    "detail": f'{row["channel"]} · {row["status"]}'
                    + (f' · {row["last_error"]}' if row.get("last_error") else ""),
                    "view": "reconcile",
                    "stock_action": row,
                }
            )

        sale_reconciliation = unlinked_sale_reconciliation(session, context.workspace.id)
        if sale_reconciliation["review_count"]:
            count = int(sale_reconciliation["review_count"])
            work_queue.append(
                {
                    "id": "reconcile:sales",
                    "kind": "reconcile",
                    "priority": 110,
                    "title": f'Resolve {count} ambiguous sold order' + ("" if count == 1 else "s"),
                    "label": "Review matches",
                    "detail": "The exact title matches more than one physical stock item.",
                    "view": "reconcile",
                }
            )

        cost_matches = purchase_cost_suggestions(session, context.workspace.id)
        if cost_matches["count"]:
            count = int(cost_matches["count"])
            work_queue.append(
                {
                    "id": "costs:purchases",
                    "kind": "purchase_cost",
                    "priority": 85,
                    "title": f'Review {count} purchase cost match' + ("" if count == 1 else "es"),
                    "label": "Record acquisition costs",
                    "detail": "Vinted purchases can fill missing inventory cost after review.",
                    "view": "sales",
                }
            )

        recent_open_sales = session.execute(
            select(models.Sale)
            .where(
                models.Sale.workspace_id == context.workspace.id,
                models.Sale.direction == "sell",
                models.Sale.occurred_at >= now - timedelta(days=30),
            )
            .order_by(models.Sale.occurred_at.desc())
            .limit(100)
        ).scalars().all()
        open_sales = [
            row for row in recent_open_sales
            if sale_counts_as_sold(row) and not row.is_closed
        ]
        if open_sales:
            count = len(open_sales)
            work_queue.append(
                {
                    "id": "sales:open",
                    "kind": "open_sales",
                    "priority": 100,
                    "title": f'{count} sold order' + ("" if count == 1 else "s") + " still open",
                    "label": "Check order status",
                    "detail": "Review payment, shipment or transaction status.",
                    "view": "sales",
                }
            )

        recent_jobs = session.execute(
            select(BackgroundJob)
            .where(BackgroundJob.workspace_id == context.workspace.id)
            .order_by(BackgroundJob.created_at.desc())
            .limit(100)
        ).scalars().all()
        latest_by_type: dict[str, BackgroundJob] = {}
        for job in recent_jobs:
            latest_by_type.setdefault(job.job_type, job)
        for job_type, job in latest_by_type.items():
            if job.status != "error" or job_type in {"noop", "cross_channel_close"}:
                continue
            label = job_type.removesuffix("_sync").replace("_", " ").strip().title()
            work_queue.append(
                {
                    "id": f'job:{job.id}',
                    "kind": "connector_error",
                    "priority": 105,
                    "title": f'{label} needs attention',
                    "label": "Open connections",
                    "detail": job.last_error or "The latest background job failed.",
                    "view": "connections",
                }
            )

        work_queue.sort(key=lambda row: (-int(row["priority"]), str(row["title"]).casefold()))

    return {
        "actions": actions[:50],
        "count": len(actions),
        "strategy": settings,
        "cross_channel_actions": stock_attention,
        "unlinked_sell_count": sale_reconciliation["review_count"],
        "purchase_cost_suggestion_count": cost_matches["count"],
        "open_sell_order_count": len(open_sales),
        "work_queue": work_queue[:75],
        "work_queue_count": len(work_queue),
    }


@router.get("/api/app/cross-channel-actions")
def cross_channel_actions(context: RequestContext = Depends(require_context)):
    with db.session_scope() as session:
        rows = serialize_actions(session, context.workspace.id)
        sale_reconciliation = unlinked_sale_reconciliation(
            session,
            context.workspace.id,
        )
    return {
        "actions": rows,
        "unlinked_sales": sale_reconciliation["review"],
        "unlinked_sell_count": sale_reconciliation["review_count"],
        "historical_unmatched_sell_count": sale_reconciliation["historical_unmatched_count"],
    }


@router.post("/api/app/cross-channel-actions/{action_id}/acknowledge")
def acknowledge_cross_channel_action(
    action_id: uuid.UUID,
    context: RequestContext = Depends(require_write_context),
):
    try:
        with db.session_scope() as session:
            action = acknowledge_manual_action(session, context.workspace.id, action_id)
            result = {"id": str(action.id), "status": action.status}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "action": result}


@router.post("/api/app/cross-channel-actions/{action_id}/retry")
def retry_cross_channel_action(
    action_id: uuid.UUID,
    context: RequestContext = Depends(require_write_context),
):
    try:
        with db.session_scope() as session:
            action = retry_action(session, context.workspace.id, action_id)
            result = {"id": str(action.id), "status": action.status}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "action": result}


@router.post("/api/app/sales/{sale_id}/link")
def link_sale_to_inventory(
    sale_id: uuid.UUID,
    payload: SaleLinkRequest,
    context: RequestContext = Depends(require_write_context),
):
    with db.session_scope() as session:
        sale = session.get(models.Sale, sale_id)
        item = session.get(models.InventoryItem, payload.inventory_item_id)
        if (
            sale is None
            or item is None
            or sale.workspace_id != context.workspace.id
            or item.workspace_id != context.workspace.id
        ):
            raise HTTPException(status_code=404, detail="Sale or inventory item not found")
        sale.inventory_item_id = item.id
        session.flush()
        created = reconcile_sale_state(session, sale)
        result = {
            "sale_id": str(sale.id),
            "inventory_item_id": str(item.id),
            "actions_created": len(created),
        }
    return {"ok": True, **result}


@router.get("/api/app/purchase-cost-suggestions")
def purchase_cost_suggestion_rows(
    context: RequestContext = Depends(require_context),
):
    with db.session_scope() as session:
        return purchase_cost_suggestions(session, context.workspace.id)


@router.post("/api/app/purchases/{purchase_id}/apply-cost")
def apply_purchase_cost_to_inventory(
    purchase_id: uuid.UUID,
    payload: PurchaseCostApplyRequest,
    context: RequestContext = Depends(require_write_context),
):
    try:
        with db.session_scope() as session:
            result = apply_purchase_cost(
                session,
                context.workspace.id,
                purchase_id=purchase_id,
                inventory_item_id=payload.inventory_item_id,
                cost_cents=payload.cost_cents,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


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
                "inventory_item_id": str(row.inventory_item_id) if row.inventory_item_id else None,
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
        sale_rows = session.execute(
            select(models.Sale).where(
                models.Sale.workspace_id == context.workspace.id,
                models.Sale.direction == "sell",
                models.Sale.occurred_at >= year_start,
            )
        ).scalars().all()
        sold_rows = [row for row in sale_rows if sale_counts_as_sold(row)]
        followers = session.execute(
            select(models.ProfileObservation)
            .join(models.ChannelAccount, models.ChannelAccount.id == models.ProfileObservation.channel_account_id)
            .where(models.ChannelAccount.workspace_id == context.workspace.id)
            .order_by(models.ProfileObservation.captured_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        active_items = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == context.workspace.id,
                models.InventoryItem.status == ItemStatus.ACTIVE,
            )
        ).scalars().all()
        active_item_ids = [item.id for item in active_items]
        active_item_listings = (
            session.execute(
                select(models.ChannelListing).where(
                    models.ChannelListing.workspace_id == context.workspace.id,
                    models.ChannelListing.inventory_item_id.in_(active_item_ids),
                    models.ChannelListing.status == ListingStatus.ACTIVE,
                )
            ).scalars().all()
            if active_item_ids
            else []
        )
        sold_item_ids = {
            row.inventory_item_id
            for row in sold_rows
            if row.inventory_item_id is not None
        }
        sold_items = (
            {
                row.id: row
                for row in session.execute(
                    select(models.InventoryItem).where(
                        models.InventoryItem.workspace_id == context.workspace.id,
                        models.InventoryItem.id.in_(sold_item_ids),
                    )
                ).scalars().all()
            }
            if sold_item_ids
            else {}
        )
    listings_by_item: dict[uuid.UUID, list[models.ChannelListing]] = {}
    for listing in active_item_listings:
        if listing.inventory_item_id is not None:
            listings_by_item.setdefault(listing.inventory_item_id, []).append(listing)

    ask_rows: list[tuple[models.InventoryItem, int, str]] = []
    costed_items: list[models.InventoryItem] = []
    margin_rows: list[tuple[models.InventoryItem, int]] = []
    for item in active_items:
        effective_ask, source = _effective_item_ask(
            item,
            listings_by_item.get(item.id, []),
        )
        if effective_ask is not None and source is not None:
            ask_rows.append((item, effective_ask, source))
        if item.cost_cents is not None:
            costed_items.append(item)
        if effective_ask is not None and item.cost_cents is not None:
            margin_rows.append((item, effective_ask))

    inventory_cost_cents = sum(
        int(item.cost_cents or 0) * max(0, int(item.quantity or 0))
        for item in costed_items
    )
    inventory_ask_cents = sum(
        int(ask) * max(0, int(item.quantity or 0))
        for item, ask, _source in ask_rows
    )
    inventory_potential_margin_cents = sum(
        (int(ask) - int(item.cost_cents or 0)) * max(0, int(item.quantity or 0))
        for item, ask in margin_rows
    )
    revenue_known_sales = [
        row for row in sold_rows
        if row.total_cents is not None
    ]
    complete_sales = [
        row for row in revenue_known_sales
        if row.inventory_item_id in sold_items
        and sold_items[row.inventory_item_id].cost_cents is not None
    ]
    sales_ytd_cost_cents = sum(
        int(sold_items[row.inventory_item_id].cost_cents)
        for row in complete_sales
    )
    sales_ytd_costed_revenue_cents = sum(
        int(row.total_cents)
        for row in complete_sales
    )
    sales_ytd_gross_profit_cents = (
        sales_ytd_costed_revenue_cents - sales_ytd_cost_cents
    )
    sales_ytd_gross_margin_pct = (
        round(sales_ytd_gross_profit_cents * 100 / sales_ytd_costed_revenue_cents, 1)
        if sales_ytd_costed_revenue_cents > 0
        else None
    )
    sales_ytd_roi_pct = (
        round(sales_ytd_gross_profit_cents * 100 / sales_ytd_cost_cents, 1)
        if sales_ytd_cost_cents > 0
        else None
    )
    return {
        "active_inventory": int(inventory_count or 0),
        "active_listings": int(listings_count or 0),
        "priced_inventory_count": len(ask_rows),
        "manual_priced_inventory_count": sum(1 for _item, _ask, source in ask_rows if source == "default"),
        "market_priced_inventory_count": sum(1 for _item, _ask, source in ask_rows if source == "marketplace"),
        "costed_inventory_count": len(costed_items),
        "margin_inventory_count": len(margin_rows),
        "inventory_cost_cents": inventory_cost_cents,
        "inventory_ask_cents": inventory_ask_cents,
        "inventory_potential_margin_cents": inventory_potential_margin_cents,
        "sales_ytd_count": len(sold_rows),
        "sales_ytd_revenue_known_count": len(revenue_known_sales),
        "sales_ytd_cents": sum(int(row.total_cents) for row in revenue_known_sales),
        "sales_ytd_costed_count": len(complete_sales),
        "sales_ytd_cost_coverage_pct": (
            round(len(complete_sales) * 100 / len(revenue_known_sales), 1)
            if revenue_known_sales
            else None
        ),
        "sales_ytd_cost_cents": sales_ytd_cost_cents,
        "sales_ytd_costed_revenue_cents": sales_ytd_costed_revenue_cents,
        "sales_ytd_gross_profit_cents": sales_ytd_gross_profit_cents,
        "sales_ytd_gross_margin_pct": sales_ytd_gross_margin_pct,
        "sales_ytd_roi_pct": sales_ytd_roi_pct,
        "currency": next(
            (item.currency for item in active_items if item.currency),
            next((row.currency for row in sold_rows if row.currency), "EUR"),
        ),
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
        downsampled = daily_snapshot_series(session, listing_ids, since)
        snapshots = [
            point
            for series in downsampled.values()
            for point in series
        ]
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

    by_listing: dict[Any, list[Any]] = {}
    daily: dict[str, dict[str, int]] = {}
    for snap in snapshots:
        by_listing.setdefault(snap.listing_id, []).append(snap)

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


@router.get("/api/app/analytics/vinted")
def vinted_behavior_analytics(
    days: int = 90,
    context: RequestContext = Depends(require_context),
):
    days = max(7, min(int(days or 90), 365))
    with db.session_scope() as session:
        return build_vinted_analytics(
            session,
            context.workspace.id,
            days=days,
            strategy=strategy_settings(context.workspace.settings),
        )


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
    default_category: str = Form(ItemCategory.GENERAL),
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
                    default_category=default_category,
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
                options={
                    "full_snapshot": full_snapshot,
                    "default_category": default_category,
                },
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
    default_category: str = Form(ItemCategory.GENERAL),
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
                default_category=default_category,
            )
            job = ImportJob(
                workspace_id=context.workspace.id,
                user_id=context.user.id,
                filename=file.filename or "inventory",
                file_type=table.file_type,
                status="applied",
                mapping=mapping,
                options={
                    "full_snapshot": full_snapshot,
                    "default_category": default_category,
                },
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
                existing.options = {
                    "full_snapshot": full_snapshot,
                    "default_category": default_category,
                }
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
    channel: str = "",
    context: RequestContext = Depends(require_context),
):
    kind = format.lower()
    if kind not in {"csv", "xlsx"}:
        raise HTTPException(status_code=400, detail="Export format must be csv or xlsx")
    channel = channel.strip().lower()
    known_channels = {row["channel"] for row in connector_catalog()}
    if channel and channel not in known_channels:
        raise HTTPException(status_code=400, detail="Unknown export channel")

    with db.session_scope() as session:
        query = select(models.InventoryItem).where(
            models.InventoryItem.workspace_id == context.workspace.id
        )
        if status:
            query = query.where(models.InventoryItem.status == status)
        items = session.execute(query.order_by(models.InventoryItem.sku)).scalars().all()

        headers = None
        if not channel:
            rows = inventory_export_rows(items)
        else:
            item_by_id = {item.id: item for item in items}
            listings = session.execute(
                select(models.ChannelListing).where(
                    models.ChannelListing.workspace_id == context.workspace.id,
                    models.ChannelListing.channel == channel,
                    models.ChannelListing.inventory_item_id.in_(list(item_by_id)) if item_by_id else False,
                )
                .order_by(models.ChannelListing.title)
            ).scalars().all() if item_by_id else []
            rows = []
            for listing in listings:
                item = item_by_id.get(listing.inventory_item_id)
                if item is None:
                    continue
                row = inventory_export_rows([item])[0]
                row.update(
                    {
                        "Channel": listing.channel,
                        "Listing ID": listing.external_id,
                        "External SKU": listing.external_sku or "",
                        "Listing Title": listing.title,
                        "Listing Price": (
                            f"{int(listing.price_cents) / 100:.2f}"
                            if listing.price_cents is not None else ""
                        ),
                        "Listing Currency": listing.currency or item.currency or "",
                        "Listing Status": listing.status,
                        "Listing URL": listing.url or "",
                    }
                )
                rows.append(row)
            headers = list(EXPORT_HEADERS) + [
                "Channel",
                "Listing ID",
                "External SKU",
                "Listing Title",
                "Listing Price",
                "Listing Currency",
                "Listing Status",
                "Listing URL",
            ]

        job = ExportJob(
            workspace_id=context.workspace.id,
            user_id=context.user.id,
            file_type=kind,
            status="completed",
            mapping={},
            options={"status": status or "all", "channel": channel or "master"},
            row_count=len(rows),
            completed_at=utcnow(),
        )
        session.add(job)

    body = render_xlsx(rows, headers=headers) if kind == "xlsx" else render_csv(rows, headers=headers)
    media = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if kind == "xlsx" else "text/csv; charset=utf-8"
    )
    suffix = f"-{channel}" if channel else ""
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="inventory{suffix}.{kind}"'},
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
            row.channel: row
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
        note = None

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
        elif channel == Channel.ETSY:
            configured = channel in stored_credentials
            values: dict[str, Any] = {}
            if configured:
                try:
                    values = decrypt_json(stored_credentials[channel].encrypted_payload)
                except ValueError:
                    values = {}
            operational = configured and _etsy_oauth_authorized(values)
            if configured and not operational:
                note = "App details saved; Etsy authorization is still required."
        elif channel == Channel.DEPOP:
            configured = channel in stored_credentials
            operational = configured
            note = "Private Selling API - Depop partner approval is required for an API key."
        elif channel in {
            Channel.WOOCOMMERCE,
            Channel.SHOPIFY,
            Channel.BIGCOMMERCE,
            Channel.SQUARESPACE,
            Channel.WIX,
        }:
            configured = channel in stored_credentials
            operational = configured

        result.append(
            {
                **info,
                "configured": configured,
                "operational": operational,
                "sync_available": operational and channel in {
                    Channel.BIBLIO,
                    Channel.EBAY,
                    Channel.ETSY,
                    Channel.WOOCOMMERCE,
                    Channel.SHOPIFY,
                    Channel.BIGCOMMERCE,
                    Channel.SQUARESPACE,
                    Channel.WIX,
                    Channel.DEPOP,
                },
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
                "note": note,
                "authorization_required": (
                    channel == Channel.ETSY and configured and not operational
                ),
                "oauth_redirect_uri": (
                    _etsy_oauth_redirect_uri() if channel == Channel.ETSY else None
                ),
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
    operational = True
    with db.session_scope() as session:
        row = session.execute(
            select(ConnectorCredential).where(
                ConnectorCredential.workspace_id == context.workspace.id,
                ConnectorCredential.channel == channel,
            )
        ).scalar_one_or_none()
        existing: dict[str, Any] = {}
        if row is not None:
            try:
                existing = decrypt_json(row.encrypted_payload)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        merged = {**existing, **cleaned}
        if channel == Channel.BIBLIO:
            if not str(merged.get("username") or "").strip() or not str(merged.get("password") or "").strip():
                raise HTTPException(status_code=400, detail="BIBLIO needs username and password")
        elif channel == Channel.EBAY:
            direct = bool(str(merged.get("oauth_token") or "").strip())
            refreshable = all(
                str(merged.get(key) or "").strip()
                for key in ("client_id", "client_secret", "refresh_token")
            )
            if not direct and not refreshable:
                raise HTTPException(
                    status_code=400,
                    detail="eBay needs oauth_token, or client_id + client_secret + refresh_token",
                )
        elif channel == Channel.ETSY:
            required = ("keystring", "shared_secret", "shop_id")
            if not all(str(merged.get(key) or "").strip() for key in required):
                raise HTTPException(
                    status_code=400,
                    detail="Etsy needs keystring, shared_secret and shop_id",
                )
            operational = _etsy_oauth_authorized(merged)
        elif channel == Channel.WOOCOMMERCE:
            required = ("store_url", "consumer_key", "consumer_secret")
            if not all(str(merged.get(key) or "").strip() for key in required):
                raise HTTPException(
                    status_code=400,
                    detail="WooCommerce needs store_url, consumer_key and consumer_secret",
                )
            store_url = str(merged.get("store_url") or "").strip()
            if not store_url.startswith("https://"):
                raise HTTPException(
                    status_code=400,
                    detail="WooCommerce store_url must use HTTPS",
                )
        elif channel == Channel.SHOPIFY:
            store_domain = str(merged.get("store_domain") or "").strip().lower()
            if store_domain.startswith("https://"):
                store_domain = store_domain[8:].rstrip("/")
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", store_domain):
                raise HTTPException(
                    status_code=400,
                    detail="Shopify store_domain must be a myshopify.com hostname",
                )
            if not str(merged.get("access_token") or "").strip():
                raise HTTPException(status_code=400, detail="Shopify needs access_token")
            version = str(merged.get("api_version") or "2026-10").strip()
            if not re.fullmatch(r"\d{4}-\d{2}", version):
                raise HTTPException(
                    status_code=400,
                    detail="Shopify api_version must look like 2026-10",
                )
        elif channel == Channel.BIGCOMMERCE:
            store_hash = str(merged.get("store_hash") or "").strip().lower()
            if not re.fullmatch(r"[a-z0-9]+", store_hash):
                raise HTTPException(
                    status_code=400,
                    detail="BigCommerce store_hash must contain only letters and numbers",
                )
            if not str(merged.get("access_token") or "").strip():
                raise HTTPException(status_code=400, detail="BigCommerce needs access_token")
        elif channel == Channel.SQUARESPACE:
            if not str(merged.get("access_token") or "").strip():
                raise HTTPException(
                    status_code=400,
                    detail="Squarespace needs an API key or OAuth access token",
                )
        elif channel == Channel.WIX:
            if not str(merged.get("api_key") or "").strip():
                raise HTTPException(status_code=400, detail="Wix needs api_key and site_id")
            site_id = str(merged.get("site_id") or "").strip()
            try:
                uuid.UUID(site_id)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail="Wix site_id must be a valid site UUID",
                ) from exc
        elif channel == Channel.DEPOP:
            if not str(merged.get("api_key") or "").strip():
                raise HTTPException(status_code=400, detail="Depop needs a partner API key")
            environment = str(merged.get("environment") or "production").strip().lower()
            if environment not in {"production", "staging"}:
                raise HTTPException(
                    status_code=400,
                    detail="Depop environment must be production or staging",
                )
        if not merged:
            raise HTTPException(status_code=400, detail="No credentials supplied")
        if row is None:
            row = ConnectorCredential(
                workspace_id=context.workspace.id,
                channel=channel,
                encrypted_payload=encrypt_json(merged),
            )
            session.add(row)
        else:
            row.encrypted_payload = encrypt_json(merged)
        account, _ = get_or_create_channel_account(session, context.workspace, channel, {})
        account.status = ChannelAccountStatus.DISCONNECTED
        account.config = {
            **dict(account.config or {}),
            "credentials_stored": True,
            "integration_state": "configured" if operational else "authorization_required",
        }
    return {
        "ok": True,
        "stored_keys": sorted(merged),
        "encrypted": True,
        "operational": operational,
    }


@router.post("/api/app/connectors/etsy/oauth/start")
def start_etsy_oauth(
    context: RequestContext = Depends(require_write_context),
):
    redirect_uri = _etsy_oauth_redirect_uri()
    if redirect_uri is None:
        raise HTTPException(
            status_code=400,
            detail="Set PUBLIC_APP_URL to the public HTTPS origin before authorizing Etsy",
        )

    state = f"{context.workspace.id}.{secrets.token_urlsafe(32)}"
    verifier = secrets.token_urlsafe(64)
    challenge = _etsy_pkce_challenge(verifier)
    with db.session_scope() as session:
        row = session.execute(
            select(ConnectorCredential).where(
                ConnectorCredential.workspace_id == context.workspace.id,
                ConnectorCredential.channel == Channel.ETSY,
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=400, detail="Save the Etsy app details first")
        try:
            values = decrypt_json(row.encrypted_payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        required = ("keystring", "shared_secret", "shop_id")
        if not all(str(values.get(key) or "").strip() for key in required):
            raise HTTPException(status_code=400, detail="Save the Etsy app details first")
        values.update(
            {
                "_oauth_state": state,
                "_oauth_code_verifier": verifier,
                "_oauth_redirect_uri": redirect_uri,
                "_oauth_started_at": utcnow().isoformat(),
                "_oauth_user_id": str(context.user.id),
            }
        )
        row.encrypted_payload = encrypt_json(values)
        account, _ = get_or_create_channel_account(
            session, context.workspace, Channel.ETSY, {}
        )
        account.status = ChannelAccountStatus.DISCONNECTED
        account.config = {
            **dict(account.config or {}),
            "credentials_stored": True,
            "integration_state": "authorization_pending",
        }

    authorization_url = "https://www.etsy.com/oauth/connect?" + urlencode(
        {
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(ETSY_OAUTH_SCOPES),
            "client_id": values["keystring"],
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return {
        "authorization_url": authorization_url,
        "redirect_uri": redirect_uri,
        "scopes": list(ETSY_OAUTH_SCOPES),
    }


@router.get(ETSY_OAUTH_CALLBACK_PATH)
def complete_etsy_oauth(
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    context: RequestContext = Depends(require_context),
):
    if not state:
        raise HTTPException(status_code=400, detail="Missing Etsy OAuth state")
    try:
        workspace_id = uuid.UUID(state.split(".", 1)[0])
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Etsy OAuth state") from exc

    with db.session_scope() as session:
        membership = session.execute(
            select(models.Membership).where(
                models.Membership.user_id == context.user.id,
                models.Membership.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()
        if membership is None:
            raise HTTPException(status_code=403, detail="Etsy OAuth workspace access denied")
        row = session.execute(
            select(ConnectorCredential).where(
                ConnectorCredential.workspace_id == workspace_id,
                ConnectorCredential.channel == Channel.ETSY,
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=400, detail="Etsy credentials are not configured")
        try:
            values = decrypt_json(row.encrypted_payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not secrets.compare_digest(str(values.get("_oauth_state") or ""), state):
            raise HTTPException(status_code=400, detail="Invalid or expired Etsy OAuth state")
        if str(values.get("_oauth_user_id") or "") != str(context.user.id):
            raise HTTPException(status_code=403, detail="Etsy OAuth user mismatch")
        try:
            started_at = datetime.fromisoformat(str(values.get("_oauth_started_at") or ""))
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            started_at = started_at.astimezone(timezone.utc)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Etsy OAuth state") from exc
        now = utcnow()
        if started_at > now + timedelta(minutes=1) or now - started_at > ETSY_OAUTH_TTL:
            raise HTTPException(status_code=400, detail="Etsy OAuth state expired")
        verifier = str(values.get("_oauth_code_verifier") or "")
        redirect_uri = str(values.get("_oauth_redirect_uri") or "")

    if error:
        with db.session_scope() as session:
            row = session.execute(
                select(ConnectorCredential).where(
                    ConnectorCredential.workspace_id == workspace_id,
                    ConnectorCredential.channel == Channel.ETSY,
                )
            ).scalar_one()
            pending = decrypt_json(row.encrypted_payload)
            if secrets.compare_digest(str(pending.get("_oauth_state") or ""), state):
                for key in (
                    "_oauth_state", "_oauth_code_verifier", "_oauth_redirect_uri",
                    "_oauth_started_at", "_oauth_user_id",
                ):
                    pending.pop(key, None)
                row.encrypted_payload = encrypt_json(pending)
        return RedirectResponse(url="/?connector=etsy&oauth=denied", status_code=303)
    if not code:
        raise HTTPException(status_code=400, detail="Missing Etsy authorization code")

    try:
        token_values = exchange_etsy_authorization_code(
            values,
            code=code,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
        )
    except RuntimeError as exc:
        with db.session_scope() as session:
            row = session.execute(
                select(ConnectorCredential).where(
                    ConnectorCredential.workspace_id == workspace_id,
                    ConnectorCredential.channel == Channel.ETSY,
                )
            ).scalar_one()
            pending = decrypt_json(row.encrypted_payload)
            if secrets.compare_digest(str(pending.get("_oauth_state") or ""), state):
                for key in (
                    "_oauth_state", "_oauth_code_verifier", "_oauth_redirect_uri",
                    "_oauth_started_at", "_oauth_user_id",
                ):
                    pending.pop(key, None)
                row.encrypted_payload = encrypt_json(pending)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with db.session_scope() as session:
        row = session.execute(
            select(ConnectorCredential).where(
                ConnectorCredential.workspace_id == workspace_id,
                ConnectorCredential.channel == Channel.ETSY,
            )
        ).scalar_one()
        current = decrypt_json(row.encrypted_payload)
        if not secrets.compare_digest(str(current.get("_oauth_state") or ""), state):
            raise HTTPException(status_code=400, detail="Etsy OAuth state was already consumed")
        current.update(token_values)
        for key in (
            "_oauth_state", "_oauth_code_verifier", "_oauth_redirect_uri",
            "_oauth_started_at", "_oauth_user_id",
        ):
            current.pop(key, None)
        row.encrypted_payload = encrypt_json(current)
        workspace = session.get(models.Workspace, workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="Workspace not found")
        account, _ = get_or_create_channel_account(session, workspace, Channel.ETSY, {})
        account.status = ChannelAccountStatus.DISCONNECTED
        account.config = {
            **dict(account.config or {}),
            "credentials_stored": True,
            "integration_state": "configured",
        }

    return RedirectResponse(url="/?connector=etsy&oauth=connected", status_code=303)


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
    if channel not in {
        Channel.BIBLIO,
        Channel.EBAY,
        Channel.ETSY,
        Channel.WOOCOMMERCE,
        Channel.SHOPIFY,
        Channel.BIGCOMMERCE,
        Channel.SQUARESPACE,
        Channel.WIX,
        Channel.DEPOP,
    }:
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
    if channel == Channel.ETSY:
        if not stored:
            raise HTTPException(status_code=400, detail="etsy credentials are not configured")
        with db.session_scope() as session:
            credential = session.execute(
                select(ConnectorCredential).where(
                    ConnectorCredential.workspace_id == context.workspace.id,
                    ConnectorCredential.channel == Channel.ETSY,
                )
            ).scalar_one()
            try:
                values = decrypt_json(credential.encrypted_payload)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not _etsy_oauth_authorized(values):
            raise HTTPException(status_code=400, detail="Etsy authorization is not complete")
    elif channel in {
        Channel.WOOCOMMERCE,
        Channel.SHOPIFY,
        Channel.BIGCOMMERCE,
        Channel.SQUARESPACE,
        Channel.WIX,
        Channel.DEPOP,
    } and not stored:
        raise HTTPException(
            status_code=400,
            detail=f"{channel} credentials are not configured",
        )
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


@router.post("/api/app/connectors/{channel}/test-connection")
def generic_connector_test(
    channel: str,
    context: RequestContext = Depends(require_write_context),
):
    if channel == Channel.ETSY:
        tester = test_etsy_workspace
    elif channel == Channel.WOOCOMMERCE:
        tester = test_woocommerce_workspace
    elif channel == Channel.SHOPIFY:
        tester = test_shopify_workspace
    elif channel == Channel.BIGCOMMERCE:
        tester = test_bigcommerce_workspace
    elif channel == Channel.SQUARESPACE:
        tester = test_squarespace_workspace
    elif channel == Channel.WIX:
        tester = test_wix_workspace
    elif channel == Channel.DEPOP:
        tester = test_depop_workspace
    else:
        raise HTTPException(status_code=400, detail="This connector has no generic connection test")
    if not has_workspace_connector_credentials(context.workspace.id, channel):
        raise HTTPException(status_code=400, detail=f"{channel} credentials are not configured")
    try:
        return tester(context.workspace.id)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
        workspace = session.get(models.Workspace, pairing.workspace_id)
        session.flush()
        workspace_name = workspace.name if workspace else "Workspace"
    return {
        "ok": True,
        "token": raw_token,
        "workspace": workspace_name,
        "app_name": APP_NAME,
    }


def extension_source_version() -> str:
    manifest_path = Path(__file__).resolve().parent / "extension" / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        version = str(manifest.get("version") or "").strip()
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        version = ""
    return version or "0.0.0"


@router.get("/api/extension/status")
def extension_status(context: RequestContext = Depends(extension_context)):
    latest = os.getenv("EXTENSION_LATEST_VERSION", "").strip() or extension_source_version()
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
    if not billing.workspace_can_write(context.workspace):
        raise HTTPException(
            status_code=402,
            detail="Workspace is read-only until the subscription is active or trialing",
        )
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
            **billing.write_access(context.workspace),
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
