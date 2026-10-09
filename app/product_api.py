"""Workspace-scoped product API.

Inventory, sales, analytics, connector and account endpoints live here.
Chrome bridge pairing/sync endpoints are owned by :mod:`app.bridge_api`.
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

from app import billing, db, jobs, listing_assistant, models, publishing, stock_intake, store_stock_audit
from app.stock_relations import record_physical_quantity, confirm_physical_relation, is_physical, is_provisional
from app.diagnostics import build_bundle, recent_logs, redact_text
from app.bridge_package import extension_source_version
from app.connectors.biblio_format import parse_biblio_inventory
from app.connectors.development import contract as marketplace_contract
from app.marketplace_operations import (
    queue_operation, retry_operation, serialize as serialize_marketplace_operation,
    start_inline, complete_operation, fail_operation,
)
from app.product_models import MarketplaceOperation, CrossChannelAction

from app.auth import (
    RequestContext,
    clear_session_cookies,
    create_session,
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
    SyncRunStatus,
    KNOWN_ITEM_CATEGORIES,
)
from app.connectors.base import Capability, connector_catalog
from app.connectors.hosted import (
    biblio_configured,
    biblio_pending_changes,
    biblio_photo_file_signature,
    biblio_upload_profile,
    exchange_etsy_authorization_code,
    ebay_configured,
    has_credentials as has_workspace_connector_credentials,
    import_biblio_workspace,
    verify_biblio_workspace,
    test_biblio_workspace,
    test_bigcommerce_workspace,
    test_depop_workspace,
    test_etsy_workspace,
    test_shopify_workspace,
    test_squarespace_workspace,
    test_wix_workspace,
    test_woocommerce_workspace,
    update_woocommerce_workspace_stock,
    read_woocommerce_workspace_stock,
    update_shopify_workspace_stock,
    read_shopify_workspace_stock,
)
from app.connectors.wix_stock import read_wix_workspace_stock, update_wix_workspace_stock
from app.connectors.wix_price import read_wix_workspace_price, update_wix_workspace_price
from app.connectors.woocommerce_price import read_woocommerce_workspace_price, update_woocommerce_workspace_price
from app.connectors.woocommerce_content import read_woocommerce_workspace_content, update_woocommerce_workspace_content
from app.connectors.woocommerce_close import (
    read_woocommerce_workspace_publication, unpublish_woocommerce_workspace_product,
)
from app import listing_content
from app.connectors.shopify_price import read_shopify_workspace_price, update_shopify_workspace_price
from app.connectors.workspace_sync import recompute_inventory_item
from app import cross_listing
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
    ImportJob,
    MappingPreset,
    BackgroundJob,
)
from app.reconciliation import apply_reconciliation_merges, reconciliation_suggestions
from app.purchase_costs import apply_purchase_cost, purchase_cost_suggestions
from app.runtime_config import is_production, public_app_origin, safe_runtime_summary
from app.stock_policy import sale_counts_as_sold
from app.strategy import strategy_settings
from app.vinted_analytics import build_vinted_analytics, daily_snapshot_series
import json
from app.workspace_bootstrap import (
    BOOTSTRAP_OWNER_EMAIL,
    get_or_create_channel_account,
)


router = APIRouter()
APP_NAME = os.getenv("APP_NAME", "Reseller Dashboard").strip() or "Reseller Dashboard"


ETSY_OAUTH_SCOPES = ("listings_r", "transactions_r")
ETSY_OAUTH_CALLBACK_PATH = "/api/app/connectors/etsy/oauth/callback"

CONNECTOR_PREFILL_KEYS: dict[str, tuple[str, ...]] = {
    Channel.BIBLIO: (
        "username", "filename_prefix", "upload_profile", "allow_plain_ftp", "auto_sync",
    ),
    Channel.EBAY: ("client_id", "site_id", "compatibility_level"),
    Channel.ETSY: ("keystring", "shop_id", "order_days", "currency"),
    Channel.WOOCOMMERCE: ("store_url", "order_days", "currency"),
    Channel.SHOPIFY: ("store_domain", "api_version", "order_days", "currency"),
    Channel.BIGCOMMERCE: ("store_hash", "order_days", "currency"),
    Channel.SQUARESPACE: ("order_days", "currency"),
    Channel.WIX: ("site_id", "order_days", "currency"),
    Channel.DEPOP: ("environment", "order_days", "currency"),
}
ETSY_OAUTH_TTL = timedelta(minutes=10)


def _etsy_oauth_redirect_uri() -> str | None:
    origin = public_app_origin()
    return f"{origin}{ETSY_OAUTH_CALLBACK_PATH}" if origin else None


def _biblio_configured_for_workspace(workspace: models.Workspace) -> bool:
    return biblio_configured(workspace.id)


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


class DiagnosticsDownloadRequest(BaseModel):
    browser_logs: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)


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


class ContentFieldsRequest(BaseModel):
    fields: list[str] = Field(min_length=1, max_length=2)


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
    barcode: str | None = None
    author: str | None = None
    isbn: str | None = None
    subtitle: str | None = None
    publisher: str | None = None
    edition: str | None = None
    binding: str | None = None
    language: str | None = None
    publish_date: str | None = None
    publication_year: int | None = None
    pages: int | None = Field(default=None, ge=0)
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
    book_id: str | None = None
    title: str | None = None
    author: str | None = None
    description: str | None = None
    isbn: str | None = None
    subtitle: str | None = None
    publisher: str | None = None
    edition: str | None = None
    binding: str | None = None
    language: str | None = None
    pages: int | None = Field(default=None, ge=0)
    publish_date: str | None = None
    condition: str | None = None
    publication_place: str | None = None
    first_edition: bool | None = None
    signed: bool | None = None
    dust_jacket_present: bool | None = None
    dust_jacket_condition: str | None = None
    dust_jacket_description: str | None = None
    illustrator: str | None = None
    keywords: str | None = None
    catalog_1: str | None = None
    catalog_2: str | None = None
    catalog_3: str | None = None
    catalog_4: str | None = None
    catalog_5: str | None = None
    catalog_6: str | None = None
    catalog_7: str | None = None
    catalog_8: str | None = None
    price_cents: int | None = Field(default=None, ge=0)


class CrossListPublishRequest(BaseModel):
    source_listing_id: uuid.UUID | None = None
    title: str | None = None
    description: str | None = None
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
    subtitle: str | None = None
    binding: str | None = None
    language: str | None = None
    publish_date: str | None = None
    publication_year: int | None = None
    pages: int | None = Field(default=None, ge=0)
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


class ConnectorCredentialsRequest(BaseModel):
    values: dict[str, str]


class SettingsRequest(BaseModel):
    name: str | None = None
    settings: dict[str, Any] | None = None


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
    rows = list(listings or [])
    attributes = dict(item.attributes or {})
    default_price = attributes.get("default_price_cents")
    effective_ask, effective_ask_source = _effective_item_ask(item, rows)
    vinted_source = next(
        (
            row
            for row in rows
            if row.channel == Channel.VINTED
            and row.status == ListingStatus.ACTIVE
        ),
        None,
    ) or next((row for row in rows if row.channel == Channel.VINTED), None)
    biblio_publishable = publishing.is_biblio_book_candidate(item, vinted_source)
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
        "stock_authority": ("physical" if is_physical(item) else "provisional" if is_provisional(item) else "legacy"),
        "relation_confirmed_at": attributes.get("relationship_confirmed_at"),
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
        "biblio_publishable": biblio_publishable,
        "biblio_source_listing_id": str(vinted_source.id) if vinted_source else None,
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
                "biblio_sync": (
                    {
                        "state": dict(row.extra or {}).get("publish_state"),
                        "queued_at": dict(row.extra or {}).get("publish_queued_at"),
                        "started_at": dict(row.extra or {}).get("publish_started_at"),
                        "completed_at": dict(row.extra or {}).get("publish_completed_at"),
                        "error": dict(row.extra or {}).get("publish_error"),
                        "inventory_synced_at": dict(row.extra or {}).get("inventory_synced_at"),
                        "photo_state": dict(row.extra or {}).get("photo_sync_state"),
                        "photo_count": dict(row.extra or {}).get("photo_count"),
                        "photo_synced_at": dict(row.extra or {}).get("photo_synced_at"),
                        "photo_error": dict(row.extra or {}).get("photo_sync_error"),
                    }
                    if row.channel == Channel.BIBLIO
                    else None
                ),
            }
            for row in rows
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
        "bridge_version": extension_source_version(),
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
        item_by_id = {item.id: item for item in linked_items}
        item_category_by_id = {item.id: item.category for item in linked_items}

    result = []
    now = datetime.now(timezone.utc)
    for listing, snapshot in rows:
        extra = dict(listing.extra or {})
        linked_item = item_by_id.get(listing.inventory_item_id)
        linked_attrs = dict(linked_item.attributes or {}) if linked_item is not None else {}
        biblio_enrichment = (
            dict(extra.get("bibliographic_enrichment") or {})
            if isinstance(extra.get("bibliographic_enrichment"), dict)
            else {}
        )
        listed_at = extra.get("listed_at")
        listed_at_source = (
            str(extra.get("listed_at_source") or "vinted")
            if listed_at
            else None
        )
        listed_age_seconds = None
        age_source = str(extra.get("listed_age_source") or "").strip()
        raw_age = extra.get("listed_age_seconds")
        trusted_relative_age = (
            listing.channel != Channel.VINTED
            or age_source.startswith("vinted_page")
        )
        try:
            if trusted_relative_age and raw_age not in (None, ""):
                listed_age_seconds = max(0, int(float(raw_age)))
                observed_raw = extra.get("listed_age_observed_at")
                if observed_raw:
                    observed = datetime.fromisoformat(
                        str(observed_raw).replace("Z", "+00:00")
                    )
                    if observed.tzinfo is None:
                        observed = observed.replace(tzinfo=timezone.utc)
                    listed_age_seconds += max(
                        0,
                        int((now - observed.astimezone(timezone.utc)).total_seconds()),
                    )
        except (TypeError, ValueError, OverflowError):
            listed_age_seconds = None
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
                "biblio_publishable": (
                    publishing.is_biblio_book_candidate(
                        item_by_id[listing.inventory_item_id],
                        listing if listing.channel == Channel.VINTED else None,
                    )
                    if listing.inventory_item_id in item_by_id
                    else False
                ),
                "biblio_gate": (
                    "link_required"
                    if listing.channel == Channel.VINTED and not listing.inventory_item_id
                    else (
                        "ready"
                        if listing.channel == Channel.VINTED
                        and listing.inventory_item_id in item_by_id
                        and publishing.is_biblio_book_candidate(
                            item_by_id[listing.inventory_item_id],
                            listing,
                        )
                        else (
                            "book_review"
                            if listing.channel == Channel.VINTED
                            and listing.inventory_item_id in item_by_id
                            else None
                        )
                    )
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
                "listed_age_seconds": listed_age_seconds,
                "listed_age_source": (
                    age_source
                    if listed_age_seconds is not None and age_source
                    else None
                ),
                "listed_age_text": (
                    extra.get("listed_age_text")
                    if listed_age_seconds is not None
                    else None
                ),
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
                "biblio_details": (
                    {
                        "source": "local_submission",
                        "remote_verified": bool(extra.get("remote_verified")),
                        "remote_verified_at": extra.get("remote_verified_at"),
                        "remote_verified_source": extra.get("remote_verified_source"),
                        "remote_verified_status": extra.get("remote_verified_status"),
                        "remote_matches_local": extra.get("remote_matches_local"),
                        "remote_mismatch_fields": list(extra.get("remote_mismatch_fields") or []),
                        "remote_verification_stale": bool(extra.get("remote_verification_stale")),
                        "remote_stale_since": extra.get("remote_stale_since"),
                        "remote_missing_at": extra.get("remote_missing_at"),
                        "author": extra.get("author") or linked_attrs.get("author"),
                        "isbn": extra.get("isbn") or linked_attrs.get("isbn"),
                        "description": extra.get("description") or linked_attrs.get("description") or (
                            linked_item.notes if linked_item is not None else None
                        ),
                        "subtitle": biblio_enrichment.get("subtitle") or linked_attrs.get("subtitle"),
                        "publisher": biblio_enrichment.get("publisher") or linked_attrs.get("publisher"),
                        "edition": biblio_enrichment.get("edition") or linked_attrs.get("edition"),
                        "binding": (
                            biblio_enrichment.get("binding")
                            or linked_attrs.get("binding")
                            or linked_attrs.get("physical_format")
                        ),
                        "language": biblio_enrichment.get("language") or linked_attrs.get("language"),
                        "publish_date": (
                            biblio_enrichment.get("publish_date")
                            or linked_attrs.get("publish_date")
                            or linked_attrs.get("publication_date")
                            or linked_attrs.get("publication_year")
                        ),
                        "pages": (
                            biblio_enrichment.get("pages")
                            or linked_attrs.get("pages")
                            or linked_attrs.get("number_of_pages")
                        ),
                        "condition": (
                            biblio_enrichment.get("condition")
                            or (linked_item.condition if linked_item is not None else None)
                        ),
                    }
                    if listing.channel == Channel.BIBLIO
                    else None
                ),
                "biblio_sync": (
                    {
                        "state": extra.get("publish_state"),
                        "queued_at": extra.get("publish_queued_at"),
                        "started_at": extra.get("publish_started_at"),
                        "completed_at": extra.get("publish_completed_at"),
                        "error": extra.get("publish_error"),
                        "inventory_synced_at": extra.get("inventory_synced_at"),
                        "photo_state": extra.get("photo_sync_state"),
                        "photo_count": extra.get("photo_count"),
                        "photo_synced_at": extra.get("photo_synced_at"),
                        "photo_error": extra.get("photo_sync_error"),
                    }
                    if listing.channel == Channel.BIBLIO
                    else None
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
                    "subtitle",
                    "binding",
                    "language",
                    "publish_date",
                    "cover_url",
                    "source_url",
                ):
                    value = values.get(key)
                    if value not in (None, ""):
                        attributes[key] = value
                if values.get("publication_year") is not None:
                    attributes["publication_year"] = int(values["publication_year"])
                if values.get("pages") is not None:
                    attributes["pages"] = int(values["pages"])
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
                record_physical_quantity(session, item, 1)
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
        record_physical_quantity(session, item, payload.quantity)
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
        if "attributes" in values and values["attributes"] is not None:
            # Internal stock relation provenance is not client-editable.
            protected = {"stock_authority", "stock_base_quantity", "relationship_confirmed_at",
                         "connector_import_placeholder", "connector_import_channel",
                         "connector_import_external_id", "connector_import_sku"}
            values["attributes"] = {
                **{k: v for k, v in dict(item.attributes or {}).items() if k in protected},
                **{k: v for k, v in dict(values["attributes"]).items() if k not in protected},
            }
        for key, value in values.items():
            setattr(item, key, value)
        if "quantity" in values and values["quantity"] is not None:
            record_physical_quantity(session, item, int(item.quantity or 0))
        session.flush()
        listings = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.inventory_item_id == item.id,
                models.ChannelListing.workspace_id == context.workspace.id,
            )
        ).scalars().all()
        result = _serialize_item(item, listings)
    return {"ok": True, "item": result}


def _cross_list_destination_status(
    session,
    workspace: models.Workspace,
    item_id: uuid.UUID,
    candidate: dict[str, Any],
    info: dict[str, Any],
) -> dict[str, Any]:
    channel = str(info["channel"])
    existing = cross_listing.existing_channel_listing(
        session,
        workspace.id,
        item_id,
        channel,
    )

    if channel == Channel.BIBLIO:
        configured = _biblio_configured_for_workspace(workspace)
        try:
            biblio = publishing.build_biblio_candidate(
                session,
                workspace.id,
                item_id,
                source_listing_id=(
                    uuid.UUID(candidate["source"]["listing_id"])
                    if candidate.get("source", {}).get("listing_id")
                    else None
                ),
                enrich_isbn=False,
            )
        except ValueError as exc:
            detail = str(exc)
            if detail == "Only book inventory can be published to BIBLIO":
                return {
                    "channel": channel,
                    "display_name": info["display_name"],
                    "status": "review",
                    "reason": (
                        "BIBLIO only accepts books. Confirm this item is a book, "
                        "then review the BIBLIO fields without leaving Cross-list."
                    ),
                    "action": "biblio_classify",
                    "configured": configured,
                    "writable": True,
                }
            return {
                "channel": channel,
                "display_name": info["display_name"],
                "status": "review",
                "reason": detail,
                "action": "biblio",
                "configured": configured,
                "writable": True,
            }
        if existing is not None:
            status, reason, action = (
                "listed",
                "Already listed on BIBLIO. Open the book preflight to update it from the current Vinted/master data.",
                "biblio",
            )
        elif not configured:
            status, reason, action = (
                "connect",
                "Connect BIBLIO before publishing.",
                "connect",
            )
        elif biblio.get("missing"):
            status, reason, action = (
                "needs_fields",
                "Missing: " + ", ".join(str(value) for value in biblio["missing"]),
                "biblio",
            )
        else:
            status, reason, action = (
                "ready",
                "Ready to publish from the linked Vinted/master data.",
                "biblio",
            )
        return {
            "channel": channel,
            "display_name": info["display_name"],
            "status": status,
            "reason": reason,
            "action": action,
            "configured": configured,
            "writable": True,
        }

    if existing is not None:
        return {
            "channel": channel,
            "display_name": info["display_name"],
            "status": "listed",
            "reason": "This physical item is already linked to a listing on this destination.",
            "action": "open" if existing.url else None,
            "listing_id": str(existing.id),
            "url": existing.url,
            "configured": True,
            "writable": Capability.CREATE_LISTING in set(info.get("capabilities") or []),
        }

    configured = (
        ebay_configured(workspace.id)
        if channel == Channel.EBAY
        else has_workspace_connector_credentials(workspace.id, channel)
    )
    writable = Capability.CREATE_LISTING in set(info.get("capabilities") or [])
    if writable:
        if not configured:
            return {
                "channel": channel,
                "display_name": info["display_name"],
                "status": "connect",
                "reason": f"Connect {info['display_name']} with write-capable credentials first.",
                "action": "connect",
                "configured": False,
                "writable": True,
            }
        if candidate.get("missing"):
            return {
                "channel": channel,
                "display_name": info["display_name"],
                "status": "needs_fields",
                "reason": "Missing: " + ", ".join(str(value) for value in candidate["missing"]),
                "action": "edit",
                "configured": True,
                "writable": True,
            }
        return {
            "channel": channel,
            "display_name": info["display_name"],
            "status": "ready",
            "reason": "Ready to create a new listing from the Vinted/master data.",
            "action": "publish",
            "configured": True,
            "writable": True,
        }

    if channel == Channel.EBAY:
        reason = (
            "Direct create needs an eBay category and seller listing policies "
            "(shipping, returns and payment) before it can be published safely."
        )
    elif channel == Channel.ETSY:
        reason = (
            "Direct create needs Etsy listings_w permission plus taxonomy, "
            "maker and creation-era fields."
        )
    elif channel == Channel.DEPOP:
        reason = (
            "This connector currently has approved-partner read access only; "
            "a supported product-create contract is not configured."
        )
    else:
        reason = (
            "This connector currently imports catalog data but its product-create "
            "adapter has not been implemented yet."
        )
    return {
        "channel": channel,
        "display_name": info["display_name"],
        "status": "not_writable",
        "reason": reason,
        "action": None,
        "configured": configured,
        "writable": False,
    }


@router.get("/api/app/inventory/{item_id}/marketplace-status")
def item_marketplace_status(
    item_id: uuid.UUID,
    context: RequestContext = Depends(require_context),
):
    """Item-specific channel relationships, proofs and operation history.

    Never guess that a remote marketplace accepted a write from a successful
    transport job. No network calls or stock changes happen on this GET.
    """
    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        if item is None or item.workspace_id != context.workspace.id:
            raise HTTPException(status_code=404, detail="Inventory item not found")
        listings = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == context.workspace.id,
                models.ChannelListing.inventory_item_id == item_id,
            ).order_by(models.ChannelListing.channel, models.ChannelListing.external_id)
        ).scalars().all()
        listing_ids = [row.id for row in listings]
        operation_filter = MarketplaceOperation.inventory_item_id == item_id
        if listing_ids:
            operation_filter = operation_filter | MarketplaceOperation.channel_listing_id.in_(listing_ids)
        operations = session.execute(
            select(MarketplaceOperation).where(
                MarketplaceOperation.workspace_id == context.workspace.id,
                operation_filter,
            ).order_by(MarketplaceOperation.created_at.desc(), MarketplaceOperation.id.desc()).limit(40)
        ).scalars().all()
        actions = session.execute(
            select(CrossChannelAction).where(
                CrossChannelAction.workspace_id == context.workspace.id,
                CrossChannelAction.inventory_item_id == item_id,
            ).order_by(CrossChannelAction.created_at.desc()).limit(30)
        ).scalars().all()
        related_ops: dict[uuid.UUID, list[MarketplaceOperation]] = {}
        for op in operations:
            if op.channel_listing_id:
                related_ops.setdefault(op.channel_listing_id, []).append(op)
        pending_closes = {
            action.channel_listing_id: action
            for action in reversed(actions)
            if action.channel == Channel.WOOCOMMERCE
            and action.status in {"attention", "error", "running"}
        }
        rows = []
        for listing in listings:
            extra = dict(listing.extra or {})
            recent = related_ops.get(listing.id, [])
            is_biblio = listing.channel == Channel.BIBLIO
            verified = bool(extra.get("remote_verified")) if is_biblio else False
            stale = bool(extra.get("remote_verification_stale")) if is_biblio else False
            match = extra.get("remote_matches_local") if is_biblio else None
            photo_state = str(extra.get("photo_sync_state") or "") if is_biblio else ""
            is_store_stock = listing.channel in {Channel.WOOCOMMERCE, Channel.SHOPIFY, Channel.WIX}
            latest_stock_check = extra.get("stock_last_checked_at") or extra.get("stock_synced_at")
            stock_check_quantity = extra.get("stock_last_remote_quantity")
            if stock_check_quantity is None and extra.get("stock_remote_readback_verified"):
                stock_check_quantity = extra.get("stock_synced_quantity")
            stock_verification = "not_checked"
            if is_store_stock and latest_stock_check:
                if type(stock_check_quantity) is int and stock_check_quantity != int(item.quantity or 0):
                    stock_verification = "stock_mismatch"
                elif (
                    extra.get("stock_remote_readback_verified")
                    and type(stock_check_quantity) is int
                    and stock_check_quantity == int(item.quantity or 0)
                ):
                    stock_verification = "stock_checked"
                else:
                    stock_verification = "stock_stale"
            needs_attention = (
                any(op.status in {"attention", "failed"} for op in recent)
                or stock_verification in {"stock_mismatch", "stock_stale"}
                or (listing.channel in {Channel.WOOCOMMERCE, Channel.SHOPIFY, Channel.WIX} and bool(extra.get("price_last_checked_at")) and (
                    extra.get("price_last_master_cents") != (item.attributes or {}).get("default_price_cents")
                    or extra.get("price_last_remote_cents") != (item.attributes or {}).get("default_price_cents")
                ))
                or (is_biblio and (
                    stale or (verified and match is False)
                    or photo_state == "error" or bool(extra.get("photo_sync_error"))
                    or str(extra.get("publish_state") or "") == "error"
                ))
            )
            close_action = pending_closes.get(listing.id)
            close_check = extra.get("close_check") or {}
            can_check_close = (
                listing.channel == Channel.WOOCOMMERCE
                and close_action is not None
                and is_physical(item)
                and int(item.quantity or 0) == 0
                and bool(re.fullmatch(r"[1-9][0-9]*", str(listing.external_id or "")))
                and bool(str(listing.external_sku or "").strip())
            )
            rows.append({
                "close_action_id": str(close_action.id) if can_check_close else None,
                "can_check_woocommerce_close": can_check_close,
                "close_remote_status": close_check.get("status") if can_check_close else None,
                "close_checked_at": close_check.get("checked_at") if can_check_close else None,
                "listing_id": str(listing.id),
                "channel": listing.channel,
                "external_id": listing.external_id,
                "external_sku": listing.external_sku,
                "title": listing.title,
                "status": listing.status,
                "quantity": listing.quantity,
                "url": listing.url,
                "last_seen_at": listing.last_seen_at.isoformat() if listing.last_seen_at else None,
                "link_source": extra.get("master_link_source"),
                "last_operation": serialize_marketplace_operation(recent[0]) if recent else None,
                "attention": needs_attention,
                "verification": (
                    "stale" if stale else
                    "matches" if verified and match is True else
                    "differs" if verified and match is False else
                    "compared" if verified else "not_verified"
                ) if is_biblio else stock_verification,
                "verified_at": (
                    extra.get("remote_verified_at") if is_biblio
                    else latest_stock_check if is_store_stock else None
                ),
                "remote_stock_quantity": (
                    stock_check_quantity if type(stock_check_quantity) is int else None
                ) if is_store_stock else None,
                "photo_state": photo_state if is_biblio else None,
                "photo_error": redact_text(str(extra.get("photo_sync_error") or ""))[:500] if is_biblio else None,
                "photo_count": len(extra.get("image_urls") or []) if is_biblio else None,
                "can_inspect_photos": is_biblio and bool(listing.external_id),
                "can_open_remote": bool(listing.url),
                "price_verification": (
                    "not_checked" if not extra.get("price_last_checked_at")
                    else "price_stale" if (
                        extra.get("price_last_master_cents") != (item.attributes or {}).get("default_price_cents")
                        or extra.get("price_last_external_id") != listing.external_id
                        or extra.get("price_last_sku") != listing.external_sku
                        or extra.get("price_last_currency") != str(item.currency or "").strip().upper()
                        or not extra.get("price_remote_readback_verified")
                        and extra.get("price_last_remote_cents") == (item.attributes or {}).get("default_price_cents")
                    )
                    else "price_mismatch" if extra.get("price_last_remote_cents") != (item.attributes or {}).get("default_price_cents")
                    else "price_checked"
                ) if listing.channel in {Channel.WOOCOMMERCE, Channel.SHOPIFY, Channel.WIX} else None,
                "remote_price_cents": extra.get("price_last_remote_cents") if listing.channel in {Channel.WOOCOMMERCE, Channel.SHOPIFY, Channel.WIX} else None,
                "price_verified_at": extra.get("price_last_checked_at") if listing.channel in {Channel.WOOCOMMERCE, Channel.SHOPIFY, Channel.WIX} else None,
                "can_sync_woocommerce_price": (
                    listing.channel == Channel.WOOCOMMERCE
                    and is_physical(item)
                    and type((item.attributes or {}).get("default_price_cents")) is int
                    and 0 < (item.attributes or {}).get("default_price_cents") <= 2000000
                    and bool(str(item.currency or "").strip())
                    and (not listing.currency or str(listing.currency).upper() == str(item.currency).upper())
                    and bool(str(listing.external_sku or "").strip())
                    and bool(re.fullmatch(r"(?:[1-9][0-9]*:)?[1-9][0-9]*", str(listing.external_id or "")))
                ),
                "can_sync_shopify_price": (
                    listing.channel == Channel.SHOPIFY
                    and is_physical(item)
                    and type((item.attributes or {}).get("default_price_cents")) is int
                    and 0 < (item.attributes or {}).get("default_price_cents") <= 2000000
                    and bool(re.fullmatch(r"[A-Z]{3}", str(item.currency or "").strip().upper()))
                    and (not listing.currency or str(listing.currency).upper() == str(item.currency).upper())
                    and bool(str(listing.external_sku or "").strip())
                    and bool(re.fullmatch(r"gid://shopify/ProductVariant/[1-9][0-9]*",
                                          str(listing.external_id or "")))
                ),
                "can_sync_wix_price": (
                    listing.channel == Channel.WIX
                    and is_physical(item)
                    and type((item.attributes or {}).get("default_price_cents")) is int
                    and 0 < (item.attributes or {}).get("default_price_cents") <= 2000000
                    and bool(re.fullmatch(r"[A-Z]{3}", str(item.currency or "").strip().upper()))
                    and (not listing.currency or str(listing.currency).upper() == str(item.currency).upper())
                    and bool(str(listing.external_sku or "").strip())
                    and bool(re.fullmatch(r"[0-9a-fA-F-]{36}:[0-9a-fA-F-]{36}",
                                          str(listing.external_id or "")))
                ),
                "can_sync_woocommerce_stock": (
                    listing.channel == Channel.WOOCOMMERCE
                    and is_physical(item)
                    and bool(re.fullmatch(r"(?:[1-9][0-9]*:)?[1-9][0-9]*", str(listing.external_id or "")))
                    and (":" not in str(listing.external_id) or bool(str(listing.external_sku or "").strip()))
                ),
                "can_sync_shopify_stock": (
                    listing.channel == Channel.SHOPIFY
                    and is_physical(item)
                    and bool(re.fullmatch(r"gid://shopify/ProductVariant/[1-9][0-9]*", str(listing.external_id or "")))
                    and bool(str(listing.external_sku or "").strip())
                ),
                "can_sync_wix_stock": (
                    listing.channel == Channel.WIX
                    and is_physical(item)
                    and bool(str(listing.external_sku or "").strip())
                    and len(str(listing.external_id or "").split(":")) == 2
                ),
            })
        return {
            "item": {"id": str(item.id), "title": item.title, "sku": item.sku,
                     "quantity": item.quantity, "status": item.status,
                     "currency": item.currency,
                     "default_price_cents": (item.attributes or {}).get("default_price_cents")},
            "listings": rows,
            "operations": [serialize_marketplace_operation(op) for op in operations],
            "closure_actions": [
                {"id": str(action.id), "channel": action.channel, "status": action.status,
                 "type": action.action_type, "listing_id": str(action.channel_listing_id),
                 "needs_reopen": bool((action.detail or {}).get("needs_reopen")),
                 "reopen_reason": (action.detail or {}).get("reopen_reason")}
                for action in actions
            ],
            "notes": {
                "publish": "Use Publish to other marketplaces to review required fields and supported destinations.",
                "update": "Edit the physical item first. BIBLIO supports sending changed records; other channels need a supported edit adapter.",
                "close": "Closing another marketplace listing must follow stock/sale reconciliation. A manual remote close is not inferred from this page.",
                "verify": "BIBLIO requires a downloaded seller inventory file or a direct marketplace check to verify publication.",
            },
        }


def _woo_price_link(session, workspace_id: uuid.UUID, item_id: uuid.UUID):
    item = session.get(models.InventoryItem, item_id)
    if item is None or item.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    if not is_physical(item):
        raise HTTPException(status_code=409, detail="Confirm physical stock before changing store prices")
    amount = (item.attributes or {}).get("default_price_cents")
    if type(amount) is not int or not 0 < amount <= 2000000:
        raise HTTPException(status_code=409, detail="Set a valid default asking price before checking WooCommerce")
    currency = str(item.currency or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise HTTPException(status_code=409, detail="Set the item's three-letter currency first")
    listings = session.execute(select(models.ChannelListing).where(
        models.ChannelListing.workspace_id == workspace_id,
        models.ChannelListing.inventory_item_id == item_id,
        models.ChannelListing.channel == Channel.WOOCOMMERCE,
    )).scalars().all()
    if len(listings) != 1:
        raise HTTPException(status_code=409, detail="Expected one linked WooCommerce listing")
    listing = listings[0]
    if (not re.fullmatch(r"(?:[1-9][0-9]*:)?[1-9][0-9]*", str(listing.external_id or ""))
            or not str(listing.external_sku or "").strip()):
        raise HTTPException(status_code=409, detail="A WooCommerce product/variation ID and confirmed SKU are required")
    if listing.currency and str(listing.currency).upper() != currency:
        raise HTTPException(status_code=409, detail="Linked listing and inventory currencies differ")
    return item, listing, amount, currency


def _content_link(session, workspace_id: uuid.UUID, item_id: uuid.UUID):
    item = session.get(models.InventoryItem, item_id)
    if item is None or item.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    if not is_physical(item):
        raise HTTPException(status_code=409, detail="Confirm the physical item before remote content updates")
    listings = session.execute(select(models.ChannelListing).where(
        models.ChannelListing.workspace_id == workspace_id,
        models.ChannelListing.inventory_item_id == item_id,
        models.ChannelListing.channel == Channel.WOOCOMMERCE,
    )).scalars().all()
    if len(listings) != 1:
        raise HTTPException(status_code=409, detail="Exactly one WooCommerce listing is required")
    listing = listings[0]
    if (not re.fullmatch(r"(?:[1-9][0-9]*:)?[1-9][0-9]*", str(listing.external_id or ""))
            or not str(listing.external_sku or "").strip()):
        raise HTTPException(status_code=409, detail="A confirmed WooCommerce product ID and SKU are required")
    return item, listing


@router.get("/api/app/inventory/{item_id}/content-comparison")
def item_content_comparison(
    item_id: uuid.UUID, context: RequestContext = Depends(require_context),
):
    """Compare only recorded listing snapshots; never suggest these are live."""
    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        if item is None or item.workspace_id != context.workspace.id:
            raise HTTPException(status_code=404, detail="Inventory item not found")
        master = listing_content.master_values(item)
        listings = session.execute(select(models.ChannelListing).where(
            models.ChannelListing.workspace_id == context.workspace.id,
            models.ChannelListing.inventory_item_id == item_id,
        ).order_by(models.ChannelListing.channel)).scalars().all()
        result = []
        for listing in listings:
            extra = dict(listing.extra or {})
            cached = extra.get("content_check") or {}
            checked = cached.get("checked_at")
            current_master = listing_content.fingerprint(master)
            confirmed = (
                listing.channel == Channel.WOOCOMMERCE
                and cached.get("external_id") == listing.external_id
                and cached.get("sku") == listing.external_sku
                and cached.get("master_fingerprint") == current_master
            )
            if confirmed and isinstance(cached.get("fields"), dict):
                remote = cached["fields"]
                source = "previous_live_check"
                editable = set(cached.get("writable_fields") or [])
            else:
                remote = listing_content.saved_listing_values(listing)
                source = "imported_snapshot"
                editable = set()
                checked = None
            result.append({
                "listing_id": str(listing.id), "channel": listing.channel,
                "source": source, "checked_at": checked,
                "remote_id": listing.external_id,
                "fields": listing_content.rows(
                    master, remote, source=source, writable=editable,
                ),
                "can_check_live": (
                    listing.channel == Channel.WOOCOMMERCE
                    and is_physical(item)
                    and bool(re.fullmatch(r"(?:[1-9][0-9]*:)?[1-9][0-9]*",
                                          str(listing.external_id or "")))
                    and bool(str(listing.external_sku or "").strip())
                ),
                "notes": ("Only a live WooCommerce check can enable editing."
                          if listing.channel == Channel.WOOCOMMERCE else
                          "Imported snapshot only; this is not a live marketplace check."),
            })
    return {"item_id": str(item_id), "master": master, "listings": result}


@router.post("/api/app/inventory/{item_id}/marketplaces/woocommerce/check-content")
def check_woocommerce_item_content(
    item_id: uuid.UUID, context: RequestContext = Depends(require_write_context),
):
    """Read the exact remote fields; store snapshot with provenance for review."""
    with db.session_scope() as session:
        item, listing = _content_link(session, context.workspace.id, item_id)
        expected = (
            listing.id, listing.external_id, listing.external_sku,
            listing_content.fingerprint(listing_content.master_values(item)),
        )
    try:
        remote = read_woocommerce_workspace_content(
            context.workspace.id, external_id=expected[1],
            expected_sku=expected[2],
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not inspect WooCommerce listing content") from exc
    with db.session_scope() as session:
        item, listing = _content_link(session, context.workspace.id, item_id)
        master = listing_content.master_values(item)
        if (listing.id != expected[0] or listing.external_id != expected[1]
                or listing.external_sku != expected[2]
                or listing_content.fingerprint(master) != expected[3]):
            raise HTTPException(status_code=409, detail="Item or marketplace link changed during inspection")
        checked = utcnow().isoformat()
        extra = dict(listing.extra or {})
        extra["content_check"] = {
            "checked_at": checked, "external_id": expected[1], "sku": expected[2],
            "remote_fingerprint": remote["fingerprint"],
            "master_fingerprint": expected[3],
            "fields": remote["fields"],
            "writable_fields": remote["writable_fields"],
        }
        listing.extra = extra
        # A content check can reconcile only a previously uncertain content
        # operation, and only by observing the exact fields previously sent.
        previous = session.execute(select(MarketplaceOperation).where(
            MarketplaceOperation.workspace_id == context.workspace.id,
            MarketplaceOperation.channel_listing_id == listing.id,
            MarketplaceOperation.operation_type == "update",
            MarketplaceOperation.target_key == f"{listing.id}:content",
            MarketplaceOperation.status.in_(["attention", "needs_verification"]),
        ).order_by(MarketplaceOperation.created_at.desc())).scalars().first()
        if previous:
            sent = (previous.job_payload or {}).get("changes")
            if isinstance(sent, dict) and sent:
                matches = all(remote["fields"].get(key) == value
                              for key, value in sent.items())
                previous.status = "succeeded" if matches else "failed"
                previous.verification = "remote_verified" if matches else "remote_mismatch"
                previous.last_error = None if matches else "Content still differs after remote inspection"
                previous.completed_at = utcnow()
                previous.active_key = None
        return {
            "ok": True, "source": "live_woocommerce",
            "checked_at": checked,
            "fields": listing_content.rows(
                master, remote["fields"], source="live_woocommerce",
                writable=set(remote["writable_fields"]),
            ),
            "note": "Read-only content check; no remote fields changed.",
        }


@router.post("/api/app/inventory/{item_id}/marketplaces/woocommerce/content")
def update_woocommerce_item_content(
    item_id: uuid.UUID, payload: ContentFieldsRequest,
    context: RequestContext = Depends(require_write_context),
):
    """Explicit field-selection write. No unselected field is sent remotely."""
    selected = list(dict.fromkeys(payload.fields))
    if any(key not in {"title", "description"} for key in selected):
        raise HTTPException(status_code=422, detail="Only title and description are editable")
    with db.session_scope() as session:
        item, listing = _content_link(session, context.workspace.id, item_id)
        master = listing_content.master_values(item)
        extra = dict(listing.extra or {})
        check = extra.get("content_check") or {}
        try:
            checked_at = datetime.fromisoformat(str(check.get("checked_at")))
            recent = checked_at.tzinfo is not None and (
                timedelta(0) <= utcnow() - checked_at <= timedelta(minutes=5)
            )
        except (ValueError, TypeError):
            recent = False
        if (not recent or check.get("external_id") != listing.external_id
                or check.get("sku") != listing.external_sku
                or check.get("master_fingerprint") != listing_content.fingerprint(master)
                or not isinstance(check.get("remote_fingerprint"), str)
                or not set(selected).issubset(set(check.get("writable_fields") or []))):
            raise HTTPException(status_code=409, detail="Check WooCommerce content again before updating")
        changes = {key: master.get(key) for key in selected}
        if any(not value or len(value) > listing_content.MAX_TEXT[key]
               for key, value in changes.items()):
            raise HTTPException(status_code=409, detail="Selected master fields must be nonempty and within length limits")
        remote = check.get("fields") or {}
        if any(changes[key] == remote.get(key) for key in selected):
            raise HTTPException(status_code=409, detail="Select only fields that differ")
        listing_id, external_id, sku = listing.id, listing.external_id, listing.external_sku
        expected_master = listing_content.fingerprint(master)
        expected_remote = check["remote_fingerprint"]
    try:
        op_id = start_inline(
            context.workspace.id, Channel.WOOCOMMERCE, "update",
            f"{listing_id}:content", inventory_item_id=item_id,
            channel_listing_id=listing_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    with db.session_scope() as session:
        op = session.get(MarketplaceOperation, op_id)
        op.job_payload = {"scope": "content", "changes": changes}
    try:
        outcome = update_woocommerce_workspace_content(
            context.workspace.id, external_id=external_id, expected_sku=sku,
            expected_fingerprint=expected_remote, changes=changes,
        )
        with db.session_scope() as session:
            item, listing = _content_link(session, context.workspace.id, item_id)
            if (listing.id != listing_id or listing.external_id != external_id
                    or listing.external_sku != sku
                    or listing_content.fingerprint(listing_content.master_values(item)) != expected_master):
                raise RuntimeError("Item or WooCommerce link changed after content write; inspect remote listing")
            extra = dict(listing.extra or {})
            extra.pop("content_check", None)  # Force a new read for subsequent edits.
            if "description" in changes:
                extra["description"] = changes["description"]
            listing.extra = extra
            if "title" in changes:
                listing.title = changes["title"]
        complete_operation(op_id, outcome)
        return {"ok": True, "remote_verified": True,
                "updated_fields": selected, "operation_id": str(op_id)}
    except Exception as exc:
        fail_operation(op_id, str(exc))
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(status_code=502, detail=(
            "WooCommerce content update not confirmed. Check the live listing "
            "before attempting another write."
        )) from exc



def _woocommerce_close_link(session, workspace_id: uuid.UUID, item_id: uuid.UUID):
    """Require a single linked simple product and an actual consuming sale."""
    item = session.get(models.InventoryItem, item_id)
    if item is None or item.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    if not is_physical(item):
        raise HTTPException(status_code=409, detail="Confirm the physical stock record first")
    recompute_inventory_item(session, item)
    if int(item.quantity or 0) != 0:
        raise HTTPException(status_code=409, detail="Physical stock is available; cannot close the listing")
    consuming = session.execute(select(models.Sale).where(
        models.Sale.workspace_id == workspace_id,
        models.Sale.inventory_item_id == item_id,
        models.Sale.direction == "sell",
    )).scalars().all()
    if not any(sale_counts_as_sold(sale) for sale in consuming):
        raise HTTPException(status_code=409, detail="No confirmed sale consumed this physical stock")
    listings = session.execute(select(models.ChannelListing).where(
        models.ChannelListing.workspace_id == workspace_id,
        models.ChannelListing.inventory_item_id == item_id,
        models.ChannelListing.channel == Channel.WOOCOMMERCE,
    )).scalars().all()
    if len(listings) != 1:
        raise HTTPException(status_code=409, detail="Exactly one WooCommerce listing is required")
    listing = listings[0]
    if (not re.fullmatch(r"[1-9][0-9]*", str(listing.external_id or ""))
            or not str(listing.external_sku or "").strip()):
        raise HTTPException(status_code=409, detail="Only simple WooCommerce products with a linked SKU are supported")
    actions = session.execute(select(CrossChannelAction).where(
        CrossChannelAction.workspace_id == workspace_id,
        CrossChannelAction.inventory_item_id == item_id,
        CrossChannelAction.channel_listing_id == listing.id,
        CrossChannelAction.channel == Channel.WOOCOMMERCE,
    ).order_by(CrossChannelAction.created_at.desc())).scalars().all()
    action = next((a for a in actions if a.status in {"attention", "error", "running"}), None)
    if action is None:
        raise HTTPException(status_code=409, detail="No outstanding sold-out closure task")
    return item, listing, action


@router.post("/api/app/inventory/{item_id}/marketplaces/woocommerce/check-close")
def check_woocommerce_item_closure(
    item_id: uuid.UUID, context: RequestContext = Depends(require_write_context),
):
    """Read publication status. Reconcile an uncertain write without resending it."""
    with db.session_scope() as session:
        _item, listing, action = _woocommerce_close_link(session, context.workspace.id, item_id)
        expected = listing.id, listing.external_id, listing.external_sku, action.id
    try:
        remote = read_woocommerce_workspace_publication(
            context.workspace.id, external_id=expected[1], expected_sku=expected[2],
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not check WooCommerce publication status") from exc
    with db.session_scope() as session:
        _item, listing, action = _woocommerce_close_link(session, context.workspace.id, item_id)
        if (listing.id, listing.external_id, listing.external_sku, action.id) != expected:
            raise HTTPException(status_code=409, detail="Closure task or link changed during inspection")
        checked = utcnow()
        extra = dict(listing.extra or {})
        extra["close_check"] = {
            "checked_at": checked.isoformat(), "external_id": expected[1],
            "sku": expected[2], "status": remote["status"],
            "fingerprint": remote["fingerprint"],
        }
        listing.extra = extra
        unresolved = session.execute(select(MarketplaceOperation).where(
            MarketplaceOperation.workspace_id == context.workspace.id,
            MarketplaceOperation.channel_listing_id == listing.id,
            MarketplaceOperation.operation_type == "close",
            MarketplaceOperation.target_key == f"{listing.id}:unpublish",
            MarketplaceOperation.status.in_(["attention", "needs_verification"]),
        ).order_by(MarketplaceOperation.created_at.desc())).scalars().first()
        if unresolved is not None:
            verified = remote["status"] == "draft"
            unresolved.status = "succeeded" if verified else "failed"
            unresolved.verification = "remote_verified" if verified else "remote_mismatch"
            unresolved.last_error = None if verified else "Product is still published after uncertain write"
            unresolved.active_key = None
            unresolved.completed_at = checked
        if remote["status"] == "draft":
            listing.status = ListingStatus.ENDED
            listing.quantity = 0
            action.status = "success"
            action.mode = "remote"
            action.completed_at = checked
            action.last_error = None
            action.detail = {**dict(action.detail or {}), "remote": "draft_verified",
                             "checked_at": checked.isoformat()}
        elif unresolved is not None:
            action.status = "attention"
            action.mode = "manual"
            action.last_error = "WooCommerce remains published; review before attempting closure again"
        return {"ok": True, "status": remote["status"],
                "can_unpublish": remote["can_unpublish"],
                "verified_closed": remote["status"] == "draft",
                "checked_at": checked.isoformat(),
                "note": "Read-only WooCommerce publication check."}


@router.post("/api/app/inventory/{item_id}/marketplaces/woocommerce/close")
def unpublish_woocommerce_item_after_sale(
    item_id: uuid.UUID, context: RequestContext = Depends(require_write_context),
):
    """An explicitly confirmed, single-record unpublish after a recent remote check."""
    with db.session_scope() as session:
        _item, listing, action = _woocommerce_close_link(session, context.workspace.id, item_id)
        check = (listing.extra or {}).get("close_check") or {}
        try:
            checked_at = datetime.fromisoformat(str(check.get("checked_at")))
            recent = checked_at.tzinfo is not None and (
                timedelta(0) <= utcnow() - checked_at <= timedelta(minutes=5)
            )
        except (TypeError, ValueError):
            recent = False
        if (not recent or listing.status not in {ListingStatus.ACTIVE, ListingStatus.RESERVED}
                or action.status != "attention"
                or check.get("external_id") != listing.external_id
                or check.get("sku") != listing.external_sku
                or check.get("status") != "publish"
                or not isinstance(check.get("fingerprint"), str)):
            raise HTTPException(status_code=409, detail="Check the published WooCommerce listing again before closing")
        listing_id, external_id, sku, action_id = (
            listing.id, listing.external_id, listing.external_sku, action.id
        )
        expected_fingerprint = check["fingerprint"]
    try:
        op_id = start_inline(
            context.workspace.id, Channel.WOOCOMMERCE, "close",
            f"{listing_id}:unpublish", inventory_item_id=item_id,
            channel_listing_id=listing_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    with db.session_scope() as session:
        op = session.get(MarketplaceOperation, op_id)
        op.job_payload = {"scope": "unpublish", "expected_status": "draft"}
        action = session.get(CrossChannelAction, action_id)
        action.status = "running"
        action.mode = "remote"
        action.attempts += 1
    try:
        result = unpublish_woocommerce_workspace_product(
            context.workspace.id, external_id=external_id, expected_sku=sku,
            expected_fingerprint=expected_fingerprint,
        )
        with db.session_scope() as session:
            listing = session.get(models.ChannelListing, listing_id)
            action = session.get(CrossChannelAction, action_id)
            item = session.get(models.InventoryItem, item_id)
            if (listing is None or listing.workspace_id != context.workspace.id
                    or listing.inventory_item_id != item_id or listing.external_id != external_id
                    or listing.external_sku != sku or item is None
                    or item.workspace_id != context.workspace.id):
                raise RuntimeError("WooCommerce link changed after unpublishing; inspect remote product")
            listing.status = ListingStatus.ENDED
            listing.quantity = 0
            extra = dict(listing.extra or {})
            extra.pop("close_check", None)
            extra["remote_unpublished_at"] = utcnow().isoformat()
            listing.extra = extra
            action.status = "success"
            action.completed_at = utcnow()
            action.last_error = None
            action.detail = {**dict(action.detail or {}), "remote": "draft_verified"}
            recompute_inventory_item(session, item)
            if int(item.quantity or 0) > 0:
                action.detail = {
                    **dict(action.detail or {}), "needs_reopen": True,
                    "reopen_reason": "Stock restored while WooCommerce unpublish was in flight",
                }
        complete_operation(op_id, result)
        return {"ok": True, "remote_verified": True,
                "status": "draft", "operation_id": str(op_id)}
    except Exception as exc:
        fail_operation(op_id, str(exc))
        with db.session_scope() as session:
            action = session.get(CrossChannelAction, action_id)
            if action and action.status == "running":
                action.status = "error"
                action.last_error = "Remote unpublish not confirmed; check publication status"
                action.completed_at = utcnow()
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(status_code=502, detail=(
            "WooCommerce closure was not confirmed. Check publication status before attempting another write."
        )) from exc


@router.post("/api/app/inventory/{item_id}/marketplaces/woocommerce/check-price")
def verify_woocommerce_item_price(
    item_id: uuid.UUID, context: RequestContext = Depends(require_write_context),
):
    """Observe the linked store regular price, without changing it."""
    with db.session_scope() as session:
        item, listing, desired, currency = _woo_price_link(
            session, context.workspace.id, item_id,
        )
        listing_id, external_id, sku = listing.id, listing.external_id, listing.external_sku
    try:
        remote = read_woocommerce_workspace_price(
            context.workspace.id, external_id=external_id, expected_sku=sku,
            expected_currency=currency,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not read WooCommerce regular price") from exc
    with db.session_scope() as session:
        _item, current, latest_desired, current_currency = _woo_price_link(
            session, context.workspace.id, item_id,
        )
        if (current.id != listing_id or current.external_id != external_id
                or current.external_sku != sku or current_currency != currency
                or latest_desired != desired):
            raise HTTPException(status_code=409, detail="Item or store link changed during price inspection")
        observed = remote["regular_price_cents"]
        matches = observed == desired
        extra = dict(current.extra or {})
        now = utcnow().isoformat()
        extra.update({
            "price_last_checked_at": now, "price_last_remote_cents": observed,
            "price_last_master_cents": desired,
            "price_last_external_id": external_id, "price_last_sku": sku,
            "price_last_currency": currency,
            "price_remote_readback_verified": matches,
        })
        current.extra = extra
        # Only price checks can clear a previous ambiguous price write.
        unresolved = session.execute(select(MarketplaceOperation).where(
            MarketplaceOperation.workspace_id == context.workspace.id,
            MarketplaceOperation.channel_listing_id == listing_id,
            MarketplaceOperation.operation_type == "update",
            MarketplaceOperation.target_key == f"{listing_id}:price",
            MarketplaceOperation.status.in_(["attention", "needs_verification"]),
        ).order_by(MarketplaceOperation.created_at.desc())).scalars().first()
        if unresolved is not None:
            was_sent = (unresolved.job_payload or {}).get("price_cents")
            if type(was_sent) is int:
                confirmed = observed == was_sent
                unresolved.status = "succeeded" if confirmed else "failed"
                unresolved.verification = "remote_verified" if confirmed else "remote_mismatch"
                unresolved.last_error = (
                    None if confirmed else "WooCommerce regular price differs after uncertain update"
                )
                unresolved.active_key = None
                unresolved.completed_at = utcnow()
        if matches:
            current.price_cents = observed
        return {
            "ok": True, "matches": matches, "local_price_cents": desired,
            "remote_price_cents": observed, "currency": currency,
            "note": "Read-only WooCommerce price check; no remote change.",
        }


@router.post("/api/app/inventory/{item_id}/marketplaces/woocommerce/price")
def update_woocommerce_item_price(
    item_id: uuid.UUID, context: RequestContext = Depends(require_write_context),
):
    """Explicit one-field price update after a recent, matching remote snapshot."""
    with db.session_scope() as session:
        item, listing, desired, currency = _woo_price_link(
            session, context.workspace.id, item_id,
        )
        extra = dict(listing.extra or {})
        checked = extra.get("price_last_checked_at")
        try:
            checked_at = datetime.fromisoformat(str(checked))
            recent = checked_at.tzinfo is not None and (
                timedelta(0) <= utcnow() - checked_at <= timedelta(minutes=5)
            )
        except (ValueError, TypeError):
            recent = False
        observed = extra.get("price_last_remote_cents")
        if (not recent or type(observed) is not int
                or extra.get("price_last_master_cents") != desired
                or extra.get("price_last_external_id") != listing.external_id
                or extra.get("price_last_sku") != listing.external_sku
                or extra.get("price_last_currency") != currency):
            raise HTTPException(status_code=409, detail="Check the current WooCommerce price before updating")
        listing_id, external_id, sku = listing.id, listing.external_id, listing.external_sku
    try:
        op_id = start_inline(
            context.workspace.id, Channel.WOOCOMMERCE, "update",
            f"{listing_id}:price", inventory_item_id=item_id,
            channel_listing_id=listing_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # Preserve the desired price for safe reconciliation if the remote PUT
    # succeeds but our response/readback becomes uncertain.
    with db.session_scope() as session:
        op = session.get(MarketplaceOperation, op_id)
        op.job_payload = {"scope": "price", "price_cents": desired,
                          "old_price_cents": observed}
    try:
        result = update_woocommerce_workspace_price(
            context.workspace.id, external_id=external_id,
            expected_sku=sku, expected_currency=currency,
            old_price_cents=observed, new_price_cents=desired,
        )
        with db.session_scope() as session:
            _item, current, latest_desired, latest_currency = _woo_price_link(
                session, context.workspace.id, item_id,
            )
            if (current.id != listing_id or current.external_id != external_id
                    or current.external_sku != sku or latest_currency != currency
                    or latest_desired != desired):
                raise RuntimeError("Item or store link changed during WooCommerce price update")
            current.price_cents = desired
            extra = dict(current.extra or {})
            now = utcnow().isoformat()
            extra.update({
                "price_last_checked_at": now, "price_last_remote_cents": desired,
                "price_last_master_cents": desired,
                "price_last_external_id": external_id, "price_last_sku": sku,
                "price_last_currency": currency,
                "price_remote_readback_verified": True,
            })
            current.extra = extra
        complete_operation(op_id, result)
        return {"ok": True, "operation_id": str(op_id), **result}
    except Exception as exc:
        fail_operation(op_id, str(exc))
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(status_code=502, detail=(
            "WooCommerce price update was not confirmed. Check the remote "
            "price before attempting another update."
        )) from exc


@router.post("/api/app/inventory/{item_id}/marketplaces/woocommerce/stock")
def update_woocommerce_item_stock(
    item_id: uuid.UUID,
    context: RequestContext = Depends(require_write_context),
):
    """Update the linked WooCommerce simple product or stock-managed variation."""
    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        if item is None or item.workspace_id != context.workspace.id:
            raise HTTPException(status_code=404, detail="Inventory item not found")
        if not is_physical(item):
            raise HTTPException(status_code=409, detail="Confirm this physical stock before remote updates")
        listings = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == context.workspace.id,
                models.ChannelListing.inventory_item_id == item_id,
                models.ChannelListing.channel == Channel.WOOCOMMERCE,
            )
        ).scalars().all()
        if len(listings) != 1:
            raise HTTPException(status_code=409, detail="Expected exactly one linked WooCommerce listing")
        listing = listings[0]
        if not re.fullmatch(r"(?:[1-9][0-9]*:)?[1-9][0-9]*", str(listing.external_id or "")):
            raise HTTPException(status_code=409, detail="Expected a WooCommerce product ID or parent:variation IDs")
        if ":" in str(listing.external_id) and not str(listing.external_sku or "").strip():
            raise HTTPException(status_code=409, detail="A linked variation SKU is required")
        external_id, expected_sku = listing.external_id, listing.external_sku
        quantity = int(item.quantity or 0)
        listing_id = listing.id

    try:
        operation_id = start_inline(
            context.workspace.id, Channel.WOOCOMMERCE, "update", str(listing_id),
            inventory_item_id=item_id,
            channel_listing_id=listing_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        result = update_woocommerce_workspace_stock(
            context.workspace.id,
            external_id=external_id,
            expected_sku=expected_sku,
            quantity=quantity,
        )
        with db.session_scope() as session:
            listing = session.get(models.ChannelListing, listing_id)
            item = session.get(models.InventoryItem, item_id)
            if (
                listing is None or item is None
                or item.workspace_id != context.workspace.id
                or listing.workspace_id != context.workspace.id
                or listing.channel != Channel.WOOCOMMERCE
                or listing.inventory_item_id != item_id
                or listing.external_id != external_id
                or listing.external_sku != expected_sku
                or not is_physical(item)
                or int(item.quantity or 0) != quantity
            ):
                raise RuntimeError(
                    "Physical inventory changed during the remote update; "
                    "reconcile WooCommerce stock before the next write"
                )
            listing.quantity = quantity
            listing.status = result["status"]
            extra = dict(listing.extra or {})
            extra["stock_synced_at"] = utcnow().isoformat()
            extra["stock_synced_quantity"] = quantity
            extra["stock_remote_readback_verified"] = True
            extra["stock_last_checked_at"] = utcnow().isoformat()
            extra["stock_last_remote_quantity"] = quantity
            listing.extra = extra
        complete_operation(operation_id, result)
        return {"ok": True, "operation_id": str(operation_id), **result}
    except Exception as exc:
        fail_operation(operation_id, str(exc))
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(
            status_code=502,
            detail="WooCommerce stock update was not confirmed. Inspect the remote product "
                   "before sending another update.",
        ) from exc


@router.post("/api/app/inventory/{item_id}/marketplaces/woocommerce/check-stock")
def verify_woocommerce_item_stock(
    item_id: uuid.UUID,
    context: RequestContext = Depends(require_write_context),
):
    """Remote GET-only observation; resolve ambiguous update attempts safely."""
    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        if item is None or item.workspace_id != context.workspace.id:
            raise HTTPException(status_code=404, detail="Inventory item not found")
        if not is_physical(item):
            raise HTTPException(status_code=409, detail="Stock has not been confirmed")
        listings = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == context.workspace.id,
                models.ChannelListing.inventory_item_id == item_id,
                models.ChannelListing.channel == Channel.WOOCOMMERCE,
            )
        ).scalars().all()
        if len(listings) != 1:
            raise HTTPException(status_code=409, detail="Expected one linked WooCommerce listing")
        listing = listings[0]
        listing_id = listing.id
        external_id, expected_sku = listing.external_id, listing.external_sku
    try:
        remote = read_woocommerce_workspace_stock(
            context.workspace.id, external_id=external_id, expected_sku=expected_sku
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not read WooCommerce stock") from exc
    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        listing = session.get(models.ChannelListing, listing_id)
        if (
            item is None or listing is None
            or item.workspace_id != context.workspace.id
            or listing.workspace_id != context.workspace.id
            or not is_physical(item)
            or listing.channel != Channel.WOOCOMMERCE
            or listing.inventory_item_id != item_id
            or listing.external_id != external_id
            or listing.external_sku != expected_sku
        ):
            raise HTTPException(status_code=409, detail="Stock link changed during inspection")
        desired = int(item.quantity or 0)
        matches = (
            remote["manage_stock"] and remote["quantity"] == desired
            and remote["stock_status"] == ("instock" if desired else "outofstock")
        )
        relevant = session.execute(
            select(MarketplaceOperation).where(
                MarketplaceOperation.workspace_id == context.workspace.id,
                MarketplaceOperation.channel_listing_id == listing_id,
                MarketplaceOperation.operation_type == "update",
                MarketplaceOperation.target_key == str(listing_id),
                MarketplaceOperation.status.in_(["attention", "needs_verification"]),
            ).order_by(MarketplaceOperation.created_at.desc())
        ).scalars().first()
        if relevant is not None:
            # Remote readback establishes what exists, releasing a previous
            # ambiguous write without ever resending it automatically.
            relevant.status = "succeeded" if matches else "failed"
            relevant.verification = "remote_verified" if matches else "remote_mismatch"
            relevant.last_error = (
                None if matches else
                "Remote quantity differs from confirmed physical stock after a read-only check"
            )
            relevant.active_key = None
            relevant.completed_at = utcnow()
        if matches:
            listing.quantity = desired
            listing.status = remote["status"]
            extra = dict(listing.extra or {})
            extra["stock_synced_at"] = utcnow().isoformat()
            extra["stock_synced_quantity"] = desired
            extra["stock_remote_readback_verified"] = True
            listing.extra = extra
        else:
            extra = dict(listing.extra or {})
            extra["stock_remote_readback_verified"] = False
            listing.extra = extra
        extra = dict(listing.extra or {})
        extra["stock_last_checked_at"] = utcnow().isoformat()
        extra["stock_last_remote_quantity"] = remote["quantity"]
        listing.extra = extra
        return {
            "ok": True, "matches": bool(matches),
            "local_quantity": desired, "remote_quantity": remote["quantity"],
            "remote_status": remote["status"],
            "manage_stock": remote["manage_stock"],
            "note": "Read-only marketplace check; no remote product was changed.",
        }


@router.post("/api/app/inventory/{item_id}/marketplaces/shopify/stock")
def update_shopify_item_stock(
    item_id: uuid.UUID,
    context: RequestContext = Depends(require_write_context),
):
    """Synchronize confirmed physical stock to a single-location Shopify variant."""
    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        if item is None or item.workspace_id != context.workspace.id:
            raise HTTPException(status_code=404, detail="Inventory item not found")
        if not is_physical(item):
            raise HTTPException(status_code=409, detail="Confirm this physical stock before remote updates")
        listings = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == context.workspace.id,
                models.ChannelListing.inventory_item_id == item_id,
                models.ChannelListing.channel == Channel.SHOPIFY,
            )
        ).scalars().all()
        if len(listings) != 1:
            raise HTTPException(status_code=409, detail="Expected exactly one linked Shopify variant")
        listing = listings[0]
        if not re.fullmatch(r"gid://shopify/ProductVariant/[1-9][0-9]*", str(listing.external_id or "")):
            raise HTTPException(status_code=409, detail="A Shopify variant ID is required")
        if not str(listing.external_sku or "").strip():
            raise HTTPException(status_code=409, detail="A linked Shopify SKU is required")
        external_id, expected_sku = listing.external_id, listing.external_sku
        quantity = int(item.quantity or 0)
        listing_id = listing.id

    try:
        operation_id = start_inline(
            context.workspace.id, Channel.SHOPIFY, "update", str(listing_id),
            inventory_item_id=item_id,
            channel_listing_id=listing_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        result = update_shopify_workspace_stock(
            context.workspace.id,
            external_id=external_id,
            expected_sku=expected_sku,
            quantity=quantity,
            idempotency_key=str(operation_id),
        )
        with db.session_scope() as session:
            listing = session.get(models.ChannelListing, listing_id)
            item = session.get(models.InventoryItem, item_id)
            if (
                listing is None or item is None
                or item.workspace_id != context.workspace.id
                or listing.workspace_id != context.workspace.id
                or listing.channel != Channel.SHOPIFY
                or listing.inventory_item_id != item_id
                or listing.external_id != external_id
                or listing.external_sku != expected_sku
                or not is_physical(item)
                or int(item.quantity or 0) != quantity
            ):
                raise RuntimeError(
                    "Physical inventory changed during the remote update; "
                    "reconcile Shopify stock before the next write"
                )
            listing.quantity = quantity
            listing.status = result["status"]
            extra = dict(listing.extra or {})
            extra["stock_synced_at"] = utcnow().isoformat()
            extra["stock_synced_quantity"] = quantity
            extra["stock_remote_readback_verified"] = True
            extra["stock_last_checked_at"] = utcnow().isoformat()
            extra["stock_last_remote_quantity"] = quantity
            listing.extra = extra
        complete_operation(operation_id, result)
        return {"ok": True, "operation_id": str(operation_id), **result}
    except Exception as exc:
        fail_operation(operation_id, str(exc))
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(
            status_code=502,
            detail="Shopify stock update was not confirmed. Inspect the remote product "
                   "before sending another update.",
        ) from exc


@router.post("/api/app/inventory/{item_id}/marketplaces/shopify/check-stock")
def verify_shopify_item_stock(
    item_id: uuid.UUID,
    context: RequestContext = Depends(require_write_context),
):
    """Check one Shopify variant without changing it; resolve ambiguous writes."""
    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        if item is None or item.workspace_id != context.workspace.id:
            raise HTTPException(status_code=404, detail="Inventory item not found")
        if not is_physical(item):
            raise HTTPException(status_code=409, detail="Stock has not been confirmed")
        listings = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == context.workspace.id,
                models.ChannelListing.inventory_item_id == item_id,
                models.ChannelListing.channel == Channel.SHOPIFY,
            )
        ).scalars().all()
        if len(listings) != 1:
            raise HTTPException(status_code=409, detail="Expected exactly one linked Shopify variant")
        listing = listings[0]
        listing_id = listing.id
        external_id, expected_sku = listing.external_id, listing.external_sku
    try:
        remote = read_shopify_workspace_stock(
            context.workspace.id, external_id=external_id, expected_sku=expected_sku
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not read Shopify stock") from exc
    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        listing = session.get(models.ChannelListing, listing_id)
        if (
            item is None or listing is None
            or item.workspace_id != context.workspace.id
            or listing.workspace_id != context.workspace.id
            or not is_physical(item)
            or listing.channel != Channel.SHOPIFY
            or listing.inventory_item_id != item_id
            or listing.external_id != external_id
            or listing.external_sku != expected_sku
        ):
            raise HTTPException(status_code=409, detail="Stock link changed during inspection")
        desired = int(item.quantity or 0)
        matches = (
            remote["manage_stock"] and remote["quantity"] == desired
            and remote["stock_status"] == ("instock" if desired else "outofstock")
        )
        relevant = session.execute(
            select(MarketplaceOperation).where(
                MarketplaceOperation.workspace_id == context.workspace.id,
                MarketplaceOperation.channel_listing_id == listing_id,
                MarketplaceOperation.operation_type == "update",
                MarketplaceOperation.target_key == str(listing_id),
                MarketplaceOperation.status.in_(["attention", "needs_verification"]),
            ).order_by(MarketplaceOperation.created_at.desc())
        ).scalars().first()
        if relevant is not None:
            # Remote readback establishes what exists, releasing a previous
            # ambiguous write without ever resending it automatically.
            relevant.status = "succeeded" if matches else "failed"
            relevant.verification = "remote_verified" if matches else "remote_mismatch"
            relevant.last_error = (
                None if matches else
                "Remote quantity differs from confirmed physical stock after a read-only check"
            )
            relevant.active_key = None
            relevant.completed_at = utcnow()
        if matches:
            listing.quantity = desired
            listing.status = remote["status"]
            extra = dict(listing.extra or {})
            extra["stock_synced_at"] = utcnow().isoformat()
            extra["stock_synced_quantity"] = desired
            extra["stock_remote_readback_verified"] = True
            listing.extra = extra
        else:
            extra = dict(listing.extra or {})
            extra["stock_remote_readback_verified"] = False
            listing.extra = extra
        extra = dict(listing.extra or {})
        extra["stock_last_checked_at"] = utcnow().isoformat()
        extra["stock_last_remote_quantity"] = remote["quantity"]
        listing.extra = extra
        return {
            "ok": True, "matches": bool(matches),
            "local_quantity": desired, "remote_quantity": remote["quantity"],
            "remote_status": remote["status"],
            "manage_stock": remote["manage_stock"],
            "note": "Read-only marketplace check; no remote product was changed.",
        }


def _shopify_price_link(session, workspace_id: uuid.UUID, item_id: uuid.UUID):
    item = session.get(models.InventoryItem, item_id)
    if item is None or item.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    if not is_physical(item):
        raise HTTPException(status_code=409, detail="Confirm physical stock before changing store prices")
    amount = (item.attributes or {}).get("default_price_cents")
    if type(amount) is not int or not 0 < amount <= 2000000:
        raise HTTPException(status_code=409, detail="Set a valid default asking price before checking Shopify")
    currency = str(item.currency or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise HTTPException(status_code=409, detail="Set the item's three-letter currency first")
    listings = session.execute(select(models.ChannelListing).where(
        models.ChannelListing.workspace_id == workspace_id,
        models.ChannelListing.inventory_item_id == item_id,
        models.ChannelListing.channel == Channel.SHOPIFY,
    )).scalars().all()
    if len(listings) != 1:
        raise HTTPException(status_code=409, detail="Expected one linked Shopify listing")
    listing = listings[0]
    if (not re.fullmatch(r"gid://shopify/ProductVariant/[1-9][0-9]*", str(listing.external_id or ""))
            or not str(listing.external_sku or "").strip()):
        raise HTTPException(status_code=409, detail="A Shopify variant ID and confirmed SKU are required")
    if listing.currency and str(listing.currency).upper() != currency:
        raise HTTPException(status_code=409, detail="Linked listing and inventory currencies differ")
    return item, listing, amount, currency


@router.post("/api/app/inventory/{item_id}/marketplaces/shopify/check-price")
def verify_shopify_item_price(
    item_id: uuid.UUID, context: RequestContext = Depends(require_write_context),
):
    """Observe the linked Shopify variant base price without changing it."""
    with db.session_scope() as session:
        item, listing, desired, currency = _shopify_price_link(
            session, context.workspace.id, item_id,
        )
        listing_id, external_id, sku = listing.id, listing.external_id, listing.external_sku
    try:
        remote = read_shopify_workspace_price(
            context.workspace.id, external_id=external_id, expected_sku=sku,
            expected_currency=currency,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not read Shopify base price") from exc
    with db.session_scope() as session:
        _item, current, latest_desired, current_currency = _shopify_price_link(
            session, context.workspace.id, item_id,
        )
        if (current.id != listing_id or current.external_id != external_id
                or current.external_sku != sku or current_currency != currency
                or latest_desired != desired):
            raise HTTPException(status_code=409, detail="Item or store link changed during price inspection")
        observed = remote["regular_price_cents"]
        matches = observed == desired
        extra = dict(current.extra or {})
        now = utcnow().isoformat()
        extra.update({
            "price_last_checked_at": now, "price_last_remote_cents": observed,
            "price_last_master_cents": desired,
            "price_last_external_id": external_id, "price_last_sku": sku,
            "price_last_currency": currency,
            "price_remote_readback_verified": matches,
        })
        current.extra = extra
        # Only price checks can clear a previous ambiguous price write.
        unresolved = session.execute(select(MarketplaceOperation).where(
            MarketplaceOperation.workspace_id == context.workspace.id,
            MarketplaceOperation.channel_listing_id == listing_id,
            MarketplaceOperation.operation_type == "update",
            MarketplaceOperation.target_key == f"{listing_id}:price",
            MarketplaceOperation.status.in_(["attention", "needs_verification"]),
        ).order_by(MarketplaceOperation.created_at.desc())).scalars().first()
        if unresolved is not None:
            was_sent = (unresolved.job_payload or {}).get("price_cents")
            if type(was_sent) is int:
                confirmed = observed == was_sent
                unresolved.status = "succeeded" if confirmed else "failed"
                unresolved.verification = "remote_verified" if confirmed else "remote_mismatch"
                unresolved.last_error = (
                    None if confirmed else "Shopify base price differs after uncertain update"
                )
                unresolved.active_key = None
                unresolved.completed_at = utcnow()
        if matches:
            current.price_cents = observed
        return {
            "ok": True, "matches": matches, "local_price_cents": desired,
            "remote_price_cents": observed, "currency": currency,
            "note": "Read-only Shopify price check; no remote change.",
        }


@router.post("/api/app/inventory/{item_id}/marketplaces/shopify/price")
def update_shopify_item_price(
    item_id: uuid.UUID, context: RequestContext = Depends(require_write_context),
):
    """Explicit one-field price update after a recent, matching remote snapshot."""
    with db.session_scope() as session:
        item, listing, desired, currency = _shopify_price_link(
            session, context.workspace.id, item_id,
        )
        extra = dict(listing.extra or {})
        checked = extra.get("price_last_checked_at")
        try:
            checked_at = datetime.fromisoformat(str(checked))
            recent = checked_at.tzinfo is not None and (
                timedelta(0) <= utcnow() - checked_at <= timedelta(minutes=5)
            )
        except (ValueError, TypeError):
            recent = False
        observed = extra.get("price_last_remote_cents")
        if (not recent or type(observed) is not int
                or extra.get("price_last_master_cents") != desired
                or extra.get("price_last_external_id") != listing.external_id
                or extra.get("price_last_sku") != listing.external_sku
                or extra.get("price_last_currency") != currency):
            raise HTTPException(status_code=409, detail="Check the current Shopify price before updating")
        listing_id, external_id, sku = listing.id, listing.external_id, listing.external_sku
    try:
        op_id = start_inline(
            context.workspace.id, Channel.SHOPIFY, "update",
            f"{listing_id}:price", inventory_item_id=item_id,
            channel_listing_id=listing_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # Preserve the desired price for safe reconciliation if the GraphQL mutation
    # succeeds but the response/readback becomes uncertain.
    with db.session_scope() as session:
        op = session.get(MarketplaceOperation, op_id)
        op.job_payload = {"scope": "price", "price_cents": desired,
                          "old_price_cents": observed}
    try:
        result = update_shopify_workspace_price(
            context.workspace.id, external_id=external_id,
            expected_sku=sku, expected_currency=currency,
            old_price_cents=observed, new_price_cents=desired,
        )
        with db.session_scope() as session:
            _item, current, latest_desired, latest_currency = _shopify_price_link(
                session, context.workspace.id, item_id,
            )
            if (current.id != listing_id or current.external_id != external_id
                    or current.external_sku != sku or latest_currency != currency
                    or latest_desired != desired):
                raise RuntimeError("Item or store link changed during Shopify price update")
            current.price_cents = desired
            extra = dict(current.extra or {})
            now = utcnow().isoformat()
            extra.update({
                "price_last_checked_at": now, "price_last_remote_cents": desired,
                "price_last_master_cents": desired,
                "price_last_external_id": external_id, "price_last_sku": sku,
                "price_last_currency": currency,
                "price_remote_readback_verified": True,
            })
            current.extra = extra
        complete_operation(op_id, result)
        return {"ok": True, "operation_id": str(op_id), **result}
    except Exception as exc:
        fail_operation(op_id, str(exc))
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(status_code=502, detail=(
            "Shopify price update was not confirmed. Check the remote "
            "price before attempting another update."
        )) from exc



def _wix_price_link(session, workspace_id: uuid.UUID, item_id: uuid.UUID):
    item = session.get(models.InventoryItem, item_id)
    if item is None or item.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    if not is_physical(item):
        raise HTTPException(status_code=409, detail="Confirm physical stock before changing store prices")
    amount = (item.attributes or {}).get("default_price_cents")
    if type(amount) is not int or not 0 < amount <= 2000000:
        raise HTTPException(status_code=409, detail="Set a valid default asking price before checking Wix")
    currency = str(item.currency or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise HTTPException(status_code=409, detail="Set the item's three-letter currency first")
    listings = session.execute(select(models.ChannelListing).where(
        models.ChannelListing.workspace_id == workspace_id,
        models.ChannelListing.inventory_item_id == item_id,
        models.ChannelListing.channel == Channel.WIX,
    )).scalars().all()
    if len(listings) != 1:
        raise HTTPException(status_code=409, detail="Expected one linked Wix listing")
    listing = listings[0]
    if (not re.fullmatch(r"[0-9a-fA-F-]{36}:[0-9a-fA-F-]{36}", str(listing.external_id or ""))
            or not str(listing.external_sku or "").strip()):
        raise HTTPException(status_code=409, detail="A Wix product:variant ID and confirmed SKU are required")
    if listing.currency and str(listing.currency).upper() != currency:
        raise HTTPException(status_code=409, detail="Linked listing and inventory currencies differ")
    return item, listing, amount, currency


@router.post("/api/app/inventory/{item_id}/marketplaces/wix/check-price")
def verify_wix_item_price(
    item_id: uuid.UUID, context: RequestContext = Depends(require_write_context),
):
    """Observe Wix Catalog V3 default-variant actual price without changing it."""
    with db.session_scope() as session:
        item, listing, desired, currency = _wix_price_link(
            session, context.workspace.id, item_id,
        )
        listing_id, external_id, sku = listing.id, listing.external_id, listing.external_sku
    try:
        remote = read_wix_workspace_price(
            context.workspace.id, external_id=external_id, expected_sku=sku,
            expected_currency=currency,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not read Wix actual price") from exc
    with db.session_scope() as session:
        _item, current, latest_desired, current_currency = _wix_price_link(
            session, context.workspace.id, item_id,
        )
        if (current.id != listing_id or current.external_id != external_id
                or current.external_sku != sku or current_currency != currency
                or latest_desired != desired):
            raise HTTPException(status_code=409, detail="Item or store link changed during price inspection")
        observed = remote["regular_price_cents"]
        matches = observed == desired
        extra = dict(current.extra or {})
        now = utcnow().isoformat()
        extra.update({
            "price_last_checked_at": now, "price_last_remote_cents": observed,
            "price_last_master_cents": desired,
            "price_last_external_id": external_id, "price_last_sku": sku,
            "price_last_currency": currency,
            "price_remote_readback_verified": matches,
        })
        current.extra = extra
        # Only price checks can clear a previous ambiguous price write.
        unresolved = session.execute(select(MarketplaceOperation).where(
            MarketplaceOperation.workspace_id == context.workspace.id,
            MarketplaceOperation.channel_listing_id == listing_id,
            MarketplaceOperation.operation_type == "update",
            MarketplaceOperation.target_key == f"{listing_id}:price",
            MarketplaceOperation.status.in_(["attention", "needs_verification"]),
        ).order_by(MarketplaceOperation.created_at.desc())).scalars().first()
        if unresolved is not None:
            was_sent = (unresolved.job_payload or {}).get("price_cents")
            if type(was_sent) is int:
                confirmed = observed == was_sent
                unresolved.status = "succeeded" if confirmed else "failed"
                unresolved.verification = "remote_verified" if confirmed else "remote_mismatch"
                unresolved.last_error = (
                    None if confirmed else "Wix actual price differs after uncertain update"
                )
                unresolved.active_key = None
                unresolved.completed_at = utcnow()
        if matches:
            current.price_cents = observed
        return {
            "ok": True, "matches": matches, "local_price_cents": desired,
            "remote_price_cents": observed, "currency": currency,
            "note": "Read-only Wix price check; no remote change.",
        }


@router.post("/api/app/inventory/{item_id}/marketplaces/wix/price")
def update_wix_item_price(
    item_id: uuid.UUID, context: RequestContext = Depends(require_write_context),
):
    """Update a default Wix variant price after a recent remote snapshot."""
    with db.session_scope() as session:
        item, listing, desired, currency = _wix_price_link(
            session, context.workspace.id, item_id,
        )
        extra = dict(listing.extra or {})
        checked = extra.get("price_last_checked_at")
        try:
            checked_at = datetime.fromisoformat(str(checked))
            recent = checked_at.tzinfo is not None and (
                timedelta(0) <= utcnow() - checked_at <= timedelta(minutes=5)
            )
        except (ValueError, TypeError):
            recent = False
        observed = extra.get("price_last_remote_cents")
        if (not recent or type(observed) is not int
                or extra.get("price_last_master_cents") != desired
                or extra.get("price_last_external_id") != listing.external_id
                or extra.get("price_last_sku") != listing.external_sku
                or extra.get("price_last_currency") != currency):
            raise HTTPException(status_code=409, detail="Check the current Wix price before updating")
        listing_id, external_id, sku = listing.id, listing.external_id, listing.external_sku
    try:
        op_id = start_inline(
            context.workspace.id, Channel.WIX, "update",
            f"{listing_id}:price", inventory_item_id=item_id,
            channel_listing_id=listing_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # Preserve the desired price for safe reconciliation if the revision-checked PATCH
    # succeeds but the response/readback becomes uncertain.
    with db.session_scope() as session:
        op = session.get(MarketplaceOperation, op_id)
        op.job_payload = {"scope": "price", "price_cents": desired,
                          "old_price_cents": observed}
    try:
        result = update_wix_workspace_price(
            context.workspace.id, external_id=external_id,
            expected_sku=sku, expected_currency=currency,
            old_price_cents=observed, new_price_cents=desired,
        )
        with db.session_scope() as session:
            _item, current, latest_desired, latest_currency = _wix_price_link(
                session, context.workspace.id, item_id,
            )
            if (current.id != listing_id or current.external_id != external_id
                    or current.external_sku != sku or latest_currency != currency
                    or latest_desired != desired):
                raise RuntimeError("Item or store link changed during Wix price update")
            current.price_cents = desired
            extra = dict(current.extra or {})
            now = utcnow().isoformat()
            extra.update({
                "price_last_checked_at": now, "price_last_remote_cents": desired,
                "price_last_master_cents": desired,
                "price_last_external_id": external_id, "price_last_sku": sku,
                "price_last_currency": currency,
                "price_remote_readback_verified": True,
            })
            current.extra = extra
        complete_operation(op_id, result)
        return {"ok": True, "operation_id": str(op_id), **result}
    except Exception as exc:
        fail_operation(op_id, str(exc))
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(status_code=502, detail=(
            "Wix price update was not confirmed. Check the remote "
            "price before attempting another update."
        )) from exc




@router.post("/api/app/inventory/{item_id}/marketplaces/wix/stock")
def update_wix_item_stock(
    item_id: uuid.UUID,
    context: RequestContext = Depends(require_write_context),
):
    """Explicit Wix V3 single-location stock sync using inventory revisions."""
    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        if item is None or item.workspace_id != context.workspace.id:
            raise HTTPException(status_code=404, detail="Inventory item not found")
        if not is_physical(item):
            raise HTTPException(status_code=409, detail="Confirm this physical stock before remote updates")
        listings = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == context.workspace.id,
                models.ChannelListing.inventory_item_id == item_id,
                models.ChannelListing.channel == Channel.WIX,
            )
        ).scalars().all()
        if len(listings) != 1:
            raise HTTPException(status_code=409, detail="Expected exactly one linked Wix variant")
        listing = listings[0]
        parts = str(listing.external_id or "").split(":")
        try:
            if len(parts) != 2 or any(str(uuid.UUID(value)) != value.lower() for value in parts):
                raise ValueError("Invalid Wix product:variant ID")
        except (ValueError, AttributeError):
            raise HTTPException(status_code=409, detail="A Wix product:variant identity is required")
        if not str(listing.external_sku or "").strip():
            raise HTTPException(status_code=409, detail="A linked Wix SKU is required")
        external_id, expected_sku = listing.external_id, listing.external_sku
        quantity = int(item.quantity or 0)
        listing_id = listing.id

    try:
        operation_id = start_inline(
            context.workspace.id, Channel.WIX, "update", str(listing_id),
            inventory_item_id=item_id,
            channel_listing_id=listing_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        result = update_wix_workspace_stock(
            context.workspace.id,
            external_id=external_id,
            expected_sku=expected_sku,
            quantity=quantity,
        )
        with db.session_scope() as session:
            listing = session.get(models.ChannelListing, listing_id)
            item = session.get(models.InventoryItem, item_id)
            if (
                listing is None or item is None
                or item.workspace_id != context.workspace.id
                or listing.workspace_id != context.workspace.id
                or listing.channel != Channel.WIX
                or listing.inventory_item_id != item_id
                or listing.external_id != external_id
                or listing.external_sku != expected_sku
                or not is_physical(item)
                or int(item.quantity or 0) != quantity
            ):
                raise RuntimeError(
                    "Physical inventory changed during the remote update; "
                    "reconcile Wix stock before the next write"
                )
            listing.quantity = quantity
            listing.status = result["status"]
            extra = dict(listing.extra or {})
            extra["stock_synced_at"] = utcnow().isoformat()
            extra["stock_synced_quantity"] = quantity
            extra["stock_remote_readback_verified"] = True
            extra["stock_last_checked_at"] = utcnow().isoformat()
            extra["stock_last_remote_quantity"] = quantity
            listing.extra = extra
        complete_operation(operation_id, result)
        return {"ok": True, "operation_id": str(operation_id), **result}
    except Exception as exc:
        fail_operation(operation_id, str(exc))
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(
            status_code=502,
            detail="Wix stock update was not confirmed. Inspect the remote product "
                   "before sending another update.",
        ) from exc


@router.post("/api/app/inventory/{item_id}/marketplaces/wix/check-stock")
def verify_wix_item_stock(
    item_id: uuid.UUID,
    context: RequestContext = Depends(require_write_context),
):
    """Check one Wix variant without changing it; resolve ambiguous writes."""
    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        if item is None or item.workspace_id != context.workspace.id:
            raise HTTPException(status_code=404, detail="Inventory item not found")
        if not is_physical(item):
            raise HTTPException(status_code=409, detail="Stock has not been confirmed")
        listings = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == context.workspace.id,
                models.ChannelListing.inventory_item_id == item_id,
                models.ChannelListing.channel == Channel.WIX,
            )
        ).scalars().all()
        if len(listings) != 1:
            raise HTTPException(status_code=409, detail="Expected exactly one linked Wix variant")
        listing = listings[0]
        listing_id = listing.id
        external_id, expected_sku = listing.external_id, listing.external_sku
    try:
        remote = read_wix_workspace_stock(
            context.workspace.id, external_id=external_id, expected_sku=expected_sku
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not read Wix stock") from exc
    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        listing = session.get(models.ChannelListing, listing_id)
        if (
            item is None or listing is None
            or item.workspace_id != context.workspace.id
            or listing.workspace_id != context.workspace.id
            or not is_physical(item)
            or listing.channel != Channel.WIX
            or listing.inventory_item_id != item_id
            or listing.external_id != external_id
            or listing.external_sku != expected_sku
        ):
            raise HTTPException(status_code=409, detail="Stock link changed during inspection")
        desired = int(item.quantity or 0)
        matches = (
            remote["manage_stock"] and remote["quantity"] == desired
            and remote["stock_status"] == ("instock" if desired else "outofstock")
        )
        relevant = session.execute(
            select(MarketplaceOperation).where(
                MarketplaceOperation.workspace_id == context.workspace.id,
                MarketplaceOperation.channel_listing_id == listing_id,
                MarketplaceOperation.operation_type == "update",
                MarketplaceOperation.target_key == str(listing_id),
                MarketplaceOperation.status.in_(["attention", "needs_verification"]),
            ).order_by(MarketplaceOperation.created_at.desc())
        ).scalars().first()
        if relevant is not None:
            # Remote readback establishes what exists, releasing a previous
            # ambiguous write without ever resending it automatically.
            relevant.status = "succeeded" if matches else "failed"
            relevant.verification = "remote_verified" if matches else "remote_mismatch"
            relevant.last_error = (
                None if matches else
                "Remote quantity differs from confirmed physical stock after a read-only check"
            )
            relevant.active_key = None
            relevant.completed_at = utcnow()
        if matches:
            listing.quantity = desired
            listing.status = remote["status"]
            extra = dict(listing.extra or {})
            extra["stock_synced_at"] = utcnow().isoformat()
            extra["stock_synced_quantity"] = desired
            extra["stock_remote_readback_verified"] = True
            listing.extra = extra
        else:
            extra = dict(listing.extra or {})
            extra["stock_remote_readback_verified"] = False
            listing.extra = extra
        extra = dict(listing.extra or {})
        extra["stock_last_checked_at"] = utcnow().isoformat()
        extra["stock_last_remote_quantity"] = remote["quantity"]
        listing.extra = extra
        return {
            "ok": True, "matches": bool(matches),
            "local_quantity": desired, "remote_quantity": remote["quantity"],
            "remote_status": remote["status"],
            "manage_stock": remote["manage_stock"],
            "note": "Read-only marketplace check; no remote product was changed.",
        }


@router.get("/api/app/inventory/{item_id}/cross-list")
def cross_list_preview(
    item_id: uuid.UUID,
    source_listing_id: uuid.UUID | None = None,
    context: RequestContext = Depends(require_context),
):
    try:
        with db.session_scope() as session:
            candidate = cross_listing.build_candidate(
                session,
                context.workspace.id,
                item_id,
                source_listing_id=source_listing_id,
            )
            destination_order = {
                Channel.BIBLIO: 0,
                Channel.WOOCOMMERCE: 1,
                Channel.SHOPIFY: 2,
                Channel.WIX: 3,
                Channel.EBAY: 4,
                Channel.ETSY: 5,
                Channel.BIGCOMMERCE: 6,
                Channel.SQUARESPACE: 7,
                Channel.DEPOP: 8,
            }
            destination_infos = [
                info
                for info in connector_catalog()
                if info["channel"] not in {
                    Channel.VINTED,
                    Channel.CSV,
                    Channel.EXCEL,
                }
            ]
            destination_infos.sort(
                key=lambda info: destination_order.get(info["channel"], 99)
            )
            destinations = [
                _cross_list_destination_status(
                    session,
                    context.workspace,
                    item_id,
                    candidate,
                    info,
                )
                for info in destination_infos
            ]
    except ValueError as exc:
        detail = str(exc)
        raise HTTPException(
            status_code=404 if detail == "Inventory item not found" else 400,
            detail=detail,
        ) from exc
    return {
        **candidate,
        "destinations": destinations,
    }


@router.post("/api/app/inventory/{item_id}/cross-list/{channel}")
def cross_list_publish(
    item_id: uuid.UUID,
    channel: str,
    payload: CrossListPublishRequest,
    context: RequestContext = Depends(require_write_context),
):
    channel = channel.strip().lower()
    if channel == Channel.BIBLIO:
        raise HTTPException(
            status_code=400,
            detail="Use the BIBLIO book preflight for BIBLIO publishing.",
        )
    if channel not in cross_listing.DIRECT_CREATE_CHANNELS:
        raise HTTPException(
            status_code=400,
            detail=f"{channel} direct publishing is not implemented yet.",
        )
    if not has_workspace_connector_credentials(context.workspace.id, channel):
        raise HTTPException(
            status_code=400,
            detail=f"Connect {channel} with write-capable credentials before publishing.",
        )
    try:
        with db.session_scope() as session:
            candidate = cross_listing.build_candidate(
                session,
                context.workspace.id,
                item_id,
                source_listing_id=payload.source_listing_id,
            )
        overrides = payload.model_dump(exclude_unset=True)
        overrides.pop("source_listing_id", None)
        if overrides:
            candidate = cross_listing.apply_overrides(candidate, overrides)
        if candidate.get("missing"):
            raise ValueError("Listing is missing: " + ", ".join(map(str, candidate["missing"])))
        with db.session_scope() as session:
            if cross_listing.existing_channel_listing(session, context.workspace.id, item_id, channel):
                raise ValueError("This physical item already has a listing on this marketplace.")
        operation_id = start_inline(
            context.workspace.id, channel, "publish", str(item_id),
            inventory_item_id=item_id,
        )
        try:
            result = cross_listing.publish(
                context.workspace.id, item_id, channel, candidate,
            )
        except Exception as exc:
            # The remote call may have succeeded before a response or local
            # linkage failed. Block blind duplicate creation on retry.
            fail_operation(operation_id, str(exc))
            raise
        complete_operation(operation_id, result)
        return {**result, "operation_id": str(operation_id)}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
        detail = str(exc)
        status_code = 404 if detail == "Inventory item not found" else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
    configured = _biblio_configured_for_workspace(context.workspace)
    return {
        **candidate,
        "configured": configured,
        "upload_profile": biblio_upload_profile(context.workspace.id),
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
            candidate = publishing.validate_biblio_candidate(
                session,
                context.workspace.id,
                item_id,
                candidate,
            )
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
        with db.session_scope() as session:
            queued_listing = session.get(models.ChannelListing, uuid.UUID(listing_id))
            operation, created = queue_operation(
                session, context.workspace.id, "biblio",
                "update" if candidate.get("already_listed") else "publish",
                listing_id, job_type="biblio_sync",
                payload={"listing_id": listing_id},
                inventory_item_id=item_id,
                channel_listing_id=uuid.UUID(listing_id),
            )
            job_id, operation_id = operation.job_id, operation.id
            if queued_listing is not None and queued_listing.workspace_id == context.workspace.id:
                extra = dict(queued_listing.extra or {})
                extra["publish_job_id"] = str(job_id)
                extra["publish_state"] = "queued"
                extra["publish_queued_at"] = utcnow().isoformat()
                queued_listing.extra = extra
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "listing_id": listing_id,
        "job_id": str(job_id),
        "operation_id": str(operation_id),
        "already_queued": not created,
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
        if previous_item_id and previous_item_id != item.id:
            associated_sales = session.execute(
                select(models.Sale.id).where(
                    models.Sale.workspace_id == context.workspace.id,
                    models.Sale.inventory_item_id == previous_item_id,
                ).limit(1)
            ).first()
            if associated_sales:
                raise HTTPException(
                    status_code=409,
                    detail="This listing's previous stock record has linked sales. "
                           "Use Reconcile → merge items so sales and listings move together.",
                )
        confirm_physical_relation(session, item)
        listing.inventory_item_id = item.id
        extra = dict(listing.extra or {})
        extra["master_link_confirmed_at"] = utcnow().isoformat()
        extra["master_link_source"] = "user"
        listing.extra = extra
        session.flush()
        recompute_inventory_item(session, item)
        if previous_item_id and previous_item_id != item.id:
            previous = session.get(models.InventoryItem, previous_item_id)
            if previous is not None and previous.workspace_id == context.workspace.id:
                recompute_inventory_item(session, previous)
    return {"ok": True}


@router.get("/api/app/inventory/store-stock-audit")
def get_store_stock_audit(context: RequestContext = Depends(require_context)):
    """Return last bulk read-only audit; never contacts marketplaces on GET."""
    return store_stock_audit.latest(context.workspace.id)


@router.post("/api/app/inventory/store-stock-audit")
def start_store_stock_audit(context: RequestContext = Depends(require_write_context)):
    """Queue one background read-only remote stock scan, never a stock update."""
    with db.session_scope() as session:
        count = len(store_stock_audit.candidates(session, context.workspace.id))
    if count == 0:
        raise HTTPException(status_code=409, detail="No linked physical stock in supported stores")
    if count > store_stock_audit.MAX_TARGETS:
        raise HTTPException(
            status_code=409,
            detail="Too many linked store listings for one check (limit 100). No scan was started.",
        )
    job_id = jobs.enqueue_unique(
        store_stock_audit.JOB_TYPE, {"version": 1}, context.workspace.id,
    )
    return {"ok": True, "job_id": str(job_id), "eligible": count,
            "message": "Stock check queued. No marketplace quantities will be changed."}


@router.get("/api/app/inventory/relationship-audit")
def inventory_relationship_audit(context: RequestContext = Depends(require_context)):
    """Read-only, workspace-scoped inventory relationship warnings.

    Exact-SKU matches and same-title/ISBN suggestions are *not* merges.
    Only the user's explicit reconciliation may assert physical identity.
    """
    with db.session_scope() as session:
        items = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == context.workspace.id,
            )
        ).scalars().all()
        listings = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == context.workspace.id,
            )
        ).scalars().all()
        by_id = {item.id: item for item in items}
        provisional = [
            {
                "item_id": str(item.id), "sku": item.sku, "title": item.title,
                "channel": (item.attributes or {}).get("connector_import_channel"),
            } for item in items if is_provisional(item)
        ]
        orphan = [
            {"listing_id": str(row.id), "channel": row.channel, "external_id": row.external_id}
            for row in listings if row.inventory_item_id not in by_id
        ]
        sku_refs: dict[str, set[uuid.UUID]] = {}
        for row in listings:
            sku = str(row.external_sku or "").strip().upper()
            if sku and row.inventory_item_id in by_id:
                sku_refs.setdefault(sku, set()).add(row.inventory_item_id)
        shared_skus = [
            {"sku": sku, "item_ids": sorted(str(value) for value in item_ids)}
            for sku, item_ids in sku_refs.items() if len(item_ids) > 1
        ]
        shared_skus.sort(key=lambda row: row["sku"])
        per_market: dict[tuple[uuid.UUID, str], list[str]] = {}
        for row in listings:
            if row.inventory_item_id in by_id and row.status == ListingStatus.ACTIVE:
                per_market.setdefault((row.inventory_item_id, row.channel), []).append(row.external_id)
        multi_active = [
            {
                "item_id": str(item_id),
                "channel": channel,
                "external_ids": sorted(external_ids),
            }
            for (item_id, channel), external_ids in per_market.items()
            if len(external_ids) > 1
        ]
        suggestions = reconciliation_suggestions(session, context.workspace.id)
    return {
        "stock_items": len(items),
        "physical": sum(is_physical(item) for item in items),
        "provisional": provisional,
        "legacy_unclassified": sum(not is_physical(item) and not is_provisional(item) for item in items),
        "unlinked_listings": orphan,
        "shared_marketplace_skus": shared_skus,
        "multi_active_same_market": multi_active,
        "duplicate_candidates": [
            {
                "confidence": row["confidence"],
                "reasons": row["reasons"],
                "item_a_id": row["item_a"]["id"],
                "item_b_id": row["item_b"]["id"],
            }
            for row in suggestions
        ],
        "policy": "Suggestions and shared SKUs never merge stock automatically.",
    }


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


@router.get("/api/app/marketplace-operations")
def marketplace_operations_list(
    channel: str = "",
    limit: int = 100,
    context: RequestContext = Depends(require_context),
):
    """Current and historical marketplace attempts for this workspace only."""
    limit = max(1, min(int(limit), 200))
    with db.session_scope() as session:
        query = select(MarketplaceOperation).where(
            MarketplaceOperation.workspace_id == context.workspace.id
        )
        if channel:
            query = query.where(MarketplaceOperation.channel == channel.strip().lower())
        rows = session.execute(
            query.order_by(MarketplaceOperation.created_at.desc(), MarketplaceOperation.id.desc()).limit(limit)
        ).scalars().all()
        return {"operations": [serialize_marketplace_operation(row) for row in rows]}


@router.get("/api/app/marketplace-operations/{operation_id}")
def marketplace_operation_detail(
    operation_id: uuid.UUID,
    context: RequestContext = Depends(require_context),
):
    with db.session_scope() as session:
        row = session.get(MarketplaceOperation, operation_id)
        if row is None or row.workspace_id != context.workspace.id:
            raise HTTPException(status_code=404, detail="Marketplace operation not found")
        return serialize_marketplace_operation(row)


@router.post("/api/app/marketplace-operations/{operation_id}/retry")
def retry_marketplace_operation(
    operation_id: uuid.UUID,
    context: RequestContext = Depends(require_write_context),
):
    try:
        with db.session_scope() as session:
            row = retry_operation(session, context.workspace.id, operation_id)
            return {"ok": True, "operation": serialize_marketplace_operation(row)}
    except ValueError as exc:
        detail = str(exc)
        raise HTTPException(
            status_code=404 if detail == "Marketplace operation not found" else 409,
            detail=detail,
        ) from exc


@router.get("/api/app/connectors/development")
def connector_development_status(context: RequestContext = Depends(require_context)):
    """Reviewable code capabilities alongside live, workspace-scoped evidence.

    Authentication/configuration, test coverage, FTP transport completion,
    and remotely verified marketplace state are deliberately distinct.
    """
    definition = marketplace_contract()
    connections = {row["channel"]: row for row in connectors(context)["connectors"]}
    with db.session_scope() as session:
        listings = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == context.workspace.id
            )
        ).scalars().all()
        items = {
            row.id: row
            for row in session.execute(
                select(models.InventoryItem).where(
                    models.InventoryItem.workspace_id == context.workspace.id
                )
            ).scalars().all()
        }
        sales = session.execute(
            select(models.Sale).where(
                models.Sale.workspace_id == context.workspace.id,
                models.Sale.direction == "sell",
            )
        ).scalars().all()
        runs = session.execute(
            select(models.ConnectorSyncRun).where(
                models.ConnectorSyncRun.workspace_id == context.workspace.id
            ).order_by(models.ConnectorSyncRun.started_at.desc())
        ).scalars().all()

    recent_runs = {}
    for run in runs:
        recent_runs.setdefault(run.channel, run)
    for row in definition["channels"]:
        channel = row["channel"]
        connection = connections.get(channel, {})
        channel_listings = [listing for listing in listings if listing.channel == channel]
        channel_sales = [sale for sale in sales if sale.channel == channel]
        recent = recent_runs.get(channel)
        row["runtime"] = {
            "configured": bool(connection.get("configured")),
            "credential_ready": bool(connection.get("operational")),
            "connection_status": connection.get("status", "unknown"),
            "last_successful_sync_at": connection.get("last_synced_at"),
            "last_run": (
                {
                    "type": recent.run_type,
                    "status": recent.status,
                    "started_at": recent.started_at.isoformat() if recent.started_at else None,
                    "completed_at": recent.completed_at.isoformat() if recent.completed_at else None,
                } if recent else None
            ),
            "listing_count": len(channel_listings),
            "master_references": sum(listing.inventory_item_id in items for listing in channel_listings),
            "unlinked_listings": sum(listing.inventory_item_id not in items for listing in channel_listings),
            "import_placeholders": sum(
                bool(dict(items[listing.inventory_item_id].attributes or {}).get("connector_import_placeholder"))
                for listing in channel_listings
                if listing.inventory_item_id in items
            ),
            "seller_sales": len(channel_sales),
            "sales_unlinked_to_master": sum(sale.inventory_item_id not in items for sale in channel_sales),
            "remote_verified": False,
        }
    return definition


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

        saved_values: dict[str, str] = {}
        credential = stored_credentials.get(channel)
        if credential is not None:
            try:
                raw_values = decrypt_json(credential.encrypted_payload)
            except ValueError:
                raw_values = {}
            for key in CONNECTOR_PREFILL_KEYS.get(channel, ()):
                value = raw_values.get(key)
                if value not in (None, ""):
                    saved_values[key] = str(value)
        if channel == Channel.BIBLIO:
            saved_values["upload_profile"] = biblio_upload_profile(context.workspace.id)

        result.append(
            {
                **info,
                "configured": configured,
                "saved_values": saved_values,
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


def _serialize_biblio_activity_run(run: models.ConnectorSyncRun) -> dict[str, Any]:
    detail = dict(run.detail or {})
    return {
        "id": str(run.id),
        "status": run.status,
        "stage": detail.get("stage"),
        "message": detail.get("message"),
        "mode": detail.get("mode"),
        "upload_profile": detail.get("upload_profile"),
        "transport": detail.get("transport"),
        "listing_id": detail.get("listing_id"),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "active_count": run.active_count,
        "delete_count": run.delete_count,
        "inventory_filename": detail.get("inventory_filename"),
        "deletes_filename": detail.get("deletes_filename"),
        "inventory_total": detail.get("inventory_total"),
        "inventory_uploaded": detail.get("inventory_uploaded"),
        "deletes_total": detail.get("deletes_total"),
        "deletes_uploaded": detail.get("deletes_uploaded"),
        "photos_total": detail.get("photos_total"),
        "photos_uploaded": detail.get("photos_uploaded"),
        "photos_skipped": detail.get("photos_skipped"),
        "photos_pending_listings": detail.get("photos_pending_listings"),
        "photo_retry_scheduled": detail.get("photo_retry_scheduled"),
        "photo_errors": list(detail.get("photo_errors") or []),
        "photo_results": list(detail.get("photo_results") or []),
        "error": run.error,
    }


@router.get("/api/app/connectors/biblio/activity")
def biblio_activity(
    limit: int = 20,
    context: RequestContext = Depends(require_context),
):
    limit = max(1, min(int(limit or 20), 50))
    with db.session_scope() as session:
        runs = session.execute(
            select(models.ConnectorSyncRun)
            .where(
                models.ConnectorSyncRun.workspace_id == context.workspace.id,
                models.ConnectorSyncRun.channel == Channel.BIBLIO,
                models.ConnectorSyncRun.run_type == "ftp_sync",
            )
            .order_by(models.ConnectorSyncRun.started_at.desc())
            .limit(limit)
        ).scalars().all()
        job_rows = session.execute(
            select(BackgroundJob)
            .where(
                BackgroundJob.workspace_id == context.workspace.id,
                BackgroundJob.job_type == "biblio_sync",
            )
            .order_by(BackgroundJob.created_at.desc())
            .limit(limit)
        ).scalars().all()

        listing_ids: set[uuid.UUID] = set()
        for job in job_rows:
            raw = dict(job.payload or {}).get("listing_id")
            if raw:
                try:
                    listing_ids.add(uuid.UUID(str(raw)))
                except ValueError:
                    pass
        for run in runs:
            raw = dict(run.detail or {}).get("listing_id")
            if raw:
                try:
                    listing_ids.add(uuid.UUID(str(raw)))
                except ValueError:
                    pass
        listing_titles = {
            str(row.id): row.title
            for row in (
                session.execute(
                    select(models.ChannelListing).where(
                        models.ChannelListing.workspace_id == context.workspace.id,
                        models.ChannelListing.id.in_(listing_ids),
                    )
                ).scalars().all()
                if listing_ids
                else []
            )
        }

        biblio_listings = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == context.workspace.id,
                models.ChannelListing.channel == Channel.BIBLIO,
            )
        ).scalars().all()
        active_biblio = [
            row for row in biblio_listings
            if row.status == ListingStatus.ACTIVE and int(row.quantity or 0) > 0
        ]
        verified = [
            row for row in active_biblio
            if bool(dict(row.extra or {}).get("remote_verified"))
        ]
        stale_verification = [
            row for row in active_biblio
            if bool(dict(row.extra or {}).get("remote_verification_stale"))
        ]
        verified_matches = [
            row for row in verified
            if dict(row.extra or {}).get("remote_matches_local") is True
        ]
        verified_mismatches = [
            row for row in verified
            if dict(row.extra or {}).get("remote_matches_local") is False
        ]
        verified_uncompared = [
            row for row in verified
            if dict(row.extra or {}).get("remote_matches_local") is None
        ]
        photo_problem = [
            row for row in active_biblio
            if str(dict(row.extra or {}).get("photo_sync_state") or "")
            in {"queued", "uploading", "retry_scheduled", "error"}
        ]
        publish_problem = [
            row for row in active_biblio
            if str(dict(row.extra or {}).get("publish_state") or "")
            in {"queued", "uploading", "error"}
        ]
        verified_times = [
            str(dict(row.extra or {}).get("remote_verified_at") or "")
            for row in biblio_listings
            if str(dict(row.extra or {}).get("remote_verified_at") or "")
        ]
        pending_changes = biblio_pending_changes(context.workspace.id)
        health = {
            "active_listings": len(active_biblio),
            "inventory_changes_pending": int(pending_changes.get("inventory") or 0),
            "deletes_pending": int(pending_changes.get("deletes") or 0),
            "remote_verified": len(verified),
            "remote_verified_matching": len(verified_matches),
            "remote_verified_mismatching": len(verified_mismatches),
            "remote_verified_uncompared": len(verified_uncompared),
            "remote_unverified": max(0, len(active_biblio) - len(verified)),
            "remote_verification_stale": len(stale_verification),
            "photo_attention": len(photo_problem),
            "publish_attention": len(publish_problem),
            "last_remote_verification_at": max(verified_times) if verified_times else None,
            "safety": {
                "ftps_preferred": True,
                "plain_ftp_requires_opt_in": True,
                "ftp_host_locked": True,
                "ftp_root_locked": True,
                "ftp_host": "ftp.biblio.com",
                "upload_profile": biblio_upload_profile(context.workspace.id),
                "incremental_change_only": True,
                "verification_non_destructive": True,
                "authoritative_import_requires_opt_in": True,
                "remote_delete_requires_sold_out": True,
            },
            "orders": {
                "automation_available": False,
                "status": "requires_biblio_enablement",
                "detail": (
                    "BIBLIO Bulk Order Management must be enabled on the seller account "
                    "and its private protocol documentation supplied before order automation can be implemented safely."
                ),
            },
        }

    serialized_runs = []
    for run in runs:
        row = _serialize_biblio_activity_run(run)
        if row.get("listing_id"):
            row["listing_title"] = listing_titles.get(str(row["listing_id"]))
        serialized_runs.append(row)

    serialized_jobs = []
    for job in job_rows:
        payload = dict(job.payload or {})
        listing_id = str(payload.get("listing_id") or "") or None
        serialized_jobs.append(
            {
                "id": str(job.id),
                "status": job.status,
                "attempts": job.attempts,
                "listing_id": listing_id,
                "listing_title": listing_titles.get(listing_id) if listing_id else None,
                "full_sync": bool(payload.get("full_sync")),
                "photos_only": bool(payload.get("photos_only")),
                "automatic_photo_retry": bool(payload.get("automatic_photo_retry")),
                "available_at": job.available_at.isoformat() if job.available_at else None,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "locked_at": job.locked_at.isoformat() if job.locked_at else None,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "error": job.last_error,
            }
        )

    running_run = next(
        (row for row in serialized_runs if row["status"] == SyncRunStatus.RUNNING),
        None,
    )
    now = utcnow()
    ready_job_ids = {
        str(job.id)
        for job in job_rows
        if (
            job.status == "running"
            or (
                job.status == "queued"
                and (job.available_at is None or job.available_at <= now)
            )
        )
    }
    active_job = next(
        (
            row
            for row in serialized_jobs
            if row["id"] in ready_job_ids
        ),
        None,
    )
    current = running_run or active_job or (serialized_runs[0] if serialized_runs else None)
    return {
        "current": current,
        "runs": serialized_runs,
        "jobs": serialized_jobs,
        "health": health,
    }



def _biblio_photo_status(workspace_id: uuid.UUID, book_id: str) -> dict[str, Any]:
    """Local preflight; FTP completion never proves BIBLIO has processed an image."""
    book_id = book_id.strip()
    if not book_id or len(book_id) > 200:
        raise HTTPException(status_code=400, detail="Invalid Book ID")
    with db.session_scope() as session:
        rows = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.channel == Channel.BIBLIO,
                models.ChannelListing.external_id == book_id,
            )
        ).scalars().all()
        if not rows:
            raise HTTPException(status_code=404, detail="BIBLIO Book ID not found in this workspace")
        if len(rows) != 1:
            raise HTTPException(status_code=409, detail="Ambiguous BIBLIO Book ID")
        listing = rows[0]
        extra = dict(listing.extra or {})
        urls = [str(url).strip() for url in extra.get("image_urls") or [] if str(url).strip()][:12]
        source = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.inventory_item_id == listing.inventory_item_id,
                models.ChannelListing.channel == Channel.VINTED,
            )
        ).scalars().all()
        source_counts = [
            len([url for url in (row.extra or {}).get("image_urls") or [] if url])
            for row in source
        ]
        receipts = dict(extra.get("photo_file_receipts") or {})
        file_progress = []
        for index, url in enumerate(urls):
            filename = book_id + (f"_{index}" if index else "") + ".jpg"
            known = receipts.get(filename) == biblio_photo_file_signature(book_id, index, url)
            file_progress.append({
                "filename": filename,
                "sent_to_ftp": bool(known),
            })
        successful_transfers = sum(row["sent_to_ftp"] for row in file_progress)
        jobs_found = session.execute(
            select(BackgroundJob).where(
                BackgroundJob.workspace_id == workspace_id,
                BackgroundJob.job_type == "biblio_sync",
            ).order_by(BackgroundJob.created_at.desc()).limit(100)
        ).scalars().all()
        matching_job = next(
            (job for job in jobs_found if str(dict(job.payload or {}).get("listing_id") or "") == str(listing.id)),
            None,
        )
        return {
            "book_id": book_id,
            "listing_id": str(listing.id),
            "title": listing.title,
            "active": listing.status == ListingStatus.ACTIVE and int(listing.quantity or 0) > 0,
            "biblio_source_photos": len(urls),
            "vinted_source_photos": max(source_counts) if source_counts else None,
            "filenames": [
                book_id + (f"_{index}" if index else "") + ".jpg"
                for index in range(len(urls))
            ],
            "file_progress": file_progress,
            "successful_file_transfers": successful_transfers,
            "unconfirmed_file_transfers": len(urls) - successful_transfers,
            "last_ftp_photo_count": extra.get("photo_count"),
            "last_ftp_photo_at": extra.get("photo_synced_at"),
            "photo_state": extra.get("photo_sync_state"),
            "photo_error": extra.get("photo_sync_error"),
            "job": (
                {
                    "status": matching_job.status,
                    "id": str(matching_job.id),
                    "error": matching_job.last_error,
                    "available_at": matching_job.available_at.isoformat() if matching_job.available_at else None,
                } if matching_job else None
            ),
        }


@router.get("/api/app/connectors/biblio/photo-status")
def biblio_photo_status(book_id: str, context: RequestContext = Depends(require_context)):
    return _biblio_photo_status(context.workspace.id, book_id)


class BiblioTargetedPhotoRetry(BaseModel):
    book_id: str = Field(min_length=1, max_length=200)
    failed_only: bool = False


@router.post("/api/app/connectors/biblio/retry-listing-photos")
def biblio_retry_listing_photos(
    payload: BiblioTargetedPhotoRetry,
    context: RequestContext = Depends(require_write_context),
):
    if not _biblio_configured_for_workspace(context.workspace):
        raise HTTPException(status_code=400, detail="BIBLIO FTP is not configured")
    info = _biblio_photo_status(context.workspace.id, payload.book_id)
    if not info["active"]:
        raise HTTPException(status_code=409, detail="Listing is not active with stock available")
    if not info["biblio_source_photos"] and not info["vinted_source_photos"]:
        raise HTTPException(status_code=409, detail="No source photographs available; refresh Vinted first")
    if payload.failed_only and (
        not info["photo_error"]
        or not info["successful_file_transfers"]
        or not info["unconfirmed_file_transfers"]
    ):
        raise HTTPException(
            status_code=409,
            detail="No verifiable partial photo transfer. Use Resend all photos for this book to check unconfirmed publication.",
        )
    try:
        with db.session_scope() as session:
            operation, created = queue_operation(
                session, context.workspace.id, "biblio", "photos",
                str(info["listing_id"]), job_type="biblio_sync",
                payload={
                    "listing_id": info["listing_id"],
                    "photos_only": True,
                    "force_photos": not payload.failed_only,
                    **({"failed_photos_only": True} if payload.failed_only else {}),
                },
                channel_listing_id=uuid.UUID(str(info["listing_id"])),
            )
            job_id, operation_id = operation.job_id, operation.id
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "job_id": str(job_id), "operation_id": str(operation_id),
            "already_queued": not created, **info}


@router.post("/api/app/connectors/biblio/full-sync")
def enqueue_biblio_full_sync(
    context: RequestContext = Depends(require_write_context),
):
    if not _biblio_configured_for_workspace(context.workspace):
        raise HTTPException(status_code=400, detail="BIBLIO FTP is not configured")
    try:
        with db.session_scope() as session:
            operation, created = queue_operation(
                session, context.workspace.id, "biblio", "sync", "all",
                job_type="biblio_sync",
                payload={"full_sync": True, "force_photos": True},
            )
            job_id, operation_id = operation.job_id, operation.id
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "job_id": str(job_id), "operation_id": str(operation_id),
            "queued": True, "already_queued": not created}


@router.post("/api/app/connectors/biblio/retry-photos")
def enqueue_biblio_photo_retry(
    context: RequestContext = Depends(require_write_context),
):
    if not _biblio_configured_for_workspace(context.workspace):
        raise HTTPException(status_code=400, detail="BIBLIO FTP is not configured")
    try:
        with db.session_scope() as session:
            operation, created = queue_operation(
                session, context.workspace.id, "biblio", "photos", "all",
                job_type="biblio_sync",
                payload={"photos_only": True, "force_photos": True},
            )
            job_id, operation_id = operation.job_id, operation.id
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "job_id": str(job_id), "operation_id": str(operation_id),
            "queued": True, "already_queued": not created}


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
            requested_host = str(merged.get("host") or "ftp.biblio.com").strip().lower().rstrip(".")
            if requested_host not in {"", "ftp.biblio.com"}:
                raise HTTPException(
                    status_code=400,
                    detail="BIBLIO FTP host is fixed to ftp.biblio.com",
                )
            requested_profile = str(merged.get("upload_profile") or "extended").strip().lower()
            if requested_profile not in {"core", "extended"}:
                raise HTTPException(
                    status_code=400,
                    detail="BIBLIO upload profile must be core or extended",
                )
            merged["host"] = "ftp.biblio.com"
            merged["allow_plain_ftp"] = (
                "true"
                if str(merged.get("allow_plain_ftp") or "").strip().lower()
                in {"1", "true", "yes", "on"}
                else "false"
            )
            merged["auto_sync"] = (
                "true"
                if str(merged.get("auto_sync") or "").strip().lower()
                in {"1", "true", "yes", "on"}
                else "false"
            )
            # Extended is canonical. Promote legacy saved "core" values when
            # connector settings are next saved.
            merged["upload_profile"] = "extended"
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
    try:
        with db.session_scope() as session:
            operation, created = queue_operation(
                session, context.workspace.id, channel, "sync", "all",
                job_type=f"{channel}_sync",
            )
            job_id = operation.job_id
            operation_id = operation.id
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "ok": True, "job_id": str(job_id), "operation_id": str(operation_id),
        "queued": True, "already_queued": not created,
    }


@router.post("/api/app/connectors/biblio/verify")
async def biblio_workspace_verify(
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
        result = verify_biblio_workspace(
            context.workspace.id,
            rows,
            filename=file.filename or "BIBLIO inventory",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


@router.post("/api/app/connectors/biblio/import")
async def biblio_workspace_import(
    file: UploadFile = File(...),
    authoritative: bool = Form(False),
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
            authoritative=bool(authoritative),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


@router.post("/api/app/connectors/biblio/test")
def biblio_workspace_test(
    context: RequestContext = Depends(require_write_context),
):
    try:
        return test_biblio_workspace(context.workspace.id)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



def _diagnostics_snapshot(workspace_id: uuid.UUID) -> dict[str, Any]:
    with db.session_scope() as session:
        job_rows = session.execute(
            select(BackgroundJob).where(
                BackgroundJob.workspace_id == workspace_id
            ).order_by(BackgroundJob.created_at.desc()).limit(50)
        ).scalars().all()
        run_rows = session.execute(
            select(models.ConnectorSyncRun).where(
                models.ConnectorSyncRun.workspace_id == workspace_id
            ).order_by(models.ConnectorSyncRun.started_at.desc()).limit(50)
        ).scalars().all()
    try:
        from app.service_status import status as service_status
        worker = service_status(
            "worker",
            max_age_seconds=max(
                30,
                int(os.getenv("WORKER_HEARTBEAT_MAX_AGE_SECONDS", "90")),
            ),
        )
    except Exception:
        worker = {"service": "worker", "healthy": False, "last_seen_at": None}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime": safe_runtime_summary(),
        "worker": worker,
        "jobs": [
            {
                "id": str(row.id),
                "type": row.job_type,
                "status": row.status,
                "attempts": row.attempts,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "available_at": row.available_at.isoformat() if row.available_at else None,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                "error": redact_text(row.last_error) if row.last_error else None,
            }
            for row in job_rows
        ],
        "connector_runs": [
            {
                "id": str(row.id),
                "channel": row.channel,
                "type": row.run_type,
                "status": row.status,
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                "items": row.item_count,
                "active": row.active_count,
                "deletes": row.delete_count,
                "error": redact_text(row.error) if row.error else None,
            }
            for row in run_rows
        ],
    }


@router.get("/api/app/diagnostics/status")
def diagnostics_status(context: RequestContext = Depends(require_context)):
    snapshot = _diagnostics_snapshot(context.workspace.id)
    return {
        "ok": True,
        "dev_console": not is_production(),
        **snapshot,
    }


@router.get("/api/app/diagnostics/logs")
def diagnostics_logs(
    limit: int = 300,
    context: RequestContext = Depends(require_context),
):
    if is_production():
        raise HTTPException(status_code=404, detail="Live logs are disabled in production")
    return {
        "ok": True,
        "logs": recent_logs(limit=max(20, min(int(limit), 1000))),
        **_diagnostics_snapshot(context.workspace.id),
    }


@router.post("/api/app/diagnostics/download")
def diagnostics_download(
    payload: DiagnosticsDownloadRequest,
    context: RequestContext = Depends(require_context),
):
    snapshot = _diagnostics_snapshot(context.workspace.id)
    content = build_bundle(
        browser_logs=payload.browser_logs,
        snapshot=snapshot,
        include_server_logs=not is_production(),
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="reseller-dashboard-diagnostics-{stamp}.zip"'
            ),
            "Cache-Control": "no-store",
        },
    )


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
