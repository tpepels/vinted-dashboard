"""Read-only reconciliation of uncertain *known* marketplace writes.

Observe one exact linked remote record through the existing adapter. Compare
against the immutable intent saved before the original write, never current
wishful state alone. A mismatch cannot establish that a prior write failed:
another seller or later operation might have changed the record again.

No remote mutations, stock changes, remote retries, or data-import fallbacks.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app import db, models
from app.constants import Channel
from app.product_models import MarketplaceOperation
from app.stock_relations import is_physical
from app.connectors.hosted import (
    read_woocommerce_workspace_stock, read_shopify_workspace_stock,
)
from app.connectors.wix_stock import read_wix_workspace_stock
from app.connectors.woocommerce_price import read_woocommerce_workspace_price
from app.connectors.shopify_price import read_shopify_workspace_price
from app.connectors.wix_price import read_wix_workspace_price
from app.connectors.woocommerce_content import read_woocommerce_workspace_content
from app.connectors.woocommerce_close import read_woocommerce_workspace_publication
from app.connectors.shopify_close import read_shopify_workspace_publication

STOCK_READERS = {
    Channel.WOOCOMMERCE: read_woocommerce_workspace_stock,
    Channel.SHOPIFY: read_shopify_workspace_stock,
    Channel.WIX: read_wix_workspace_stock,
}
PRICE_READERS = {
    Channel.WOOCOMMERCE: read_woocommerce_workspace_price,
    Channel.SHOPIFY: read_shopify_workspace_price,
    Channel.WIX: read_wix_workspace_price,
}
CLOSE_READERS = {
    Channel.WOOCOMMERCE: read_woocommerce_workspace_publication,
    Channel.SHOPIFY: read_shopify_workspace_publication,
}
UNRESOLVED = frozenset({"attention", "needs_verification"})
# Older implementations marked mismatched remote observations 'failed'.
# These may be revisited, but never automatically used as retry permission.
REVISITABLE = UNRESOLVED | {"failed"}


class InspectionUnavailable(ValueError):
    """A remote check cannot safely attribute evidence to this attempt."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def kind(op: MarketplaceOperation) -> str | None:
    """Determine reader from strict historical target and intent shape."""
    if op.status not in REVISITABLE or op.channel_listing_id is None or op.inventory_item_id is None:
        return None
    target = str(op.channel_listing_id)
    intent = dict(op.job_payload or {})
    if op.operation_type == "update" and op.target_key == target and op.channel in STOCK_READERS:
        if (intent.get("scope") == "stock" and type(intent.get("quantity")) is int
                and intent["quantity"] >= 0):
            return "stock"
    if op.operation_type == "update" and op.target_key == f"{target}:price" and op.channel in PRICE_READERS:
        if (intent.get("scope") == "price" and type(intent.get("price_cents")) is int
                and intent["price_cents"] >= 0 and isinstance(intent.get("currency"), str)
                and intent["currency"].strip()):
            return "price"
    if op.operation_type == "update" and op.target_key == f"{target}:content" and op.channel == Channel.WOOCOMMERCE:
        changes = intent.get("changes")
        if (intent.get("scope") == "content" and isinstance(changes, dict) and changes
                and set(changes).issubset({"title", "description"})
                and all(isinstance(v, str) and bool(v) for v in changes.values())):
            return "content"
    if op.operation_type == "close" and op.target_key == f"{target}:unpublish" and op.channel in CLOSE_READERS:
        if intent.get("scope") == "unpublish" and intent.get("expected_status") == "draft":
            return "close"
    return None


def can_inspect(op: MarketplaceOperation) -> bool:
    """Conservative affordance; actual endpoint always revalidates identity."""
    return kind(op) is not None


def _target(session: Any, workspace_id: uuid.UUID, op: MarketplaceOperation):
    listing = session.get(models.ChannelListing, op.channel_listing_id)
    item = session.get(models.InventoryItem, op.inventory_item_id)
    if not listing or not item or (
        listing.workspace_id != workspace_id
        or item.workspace_id != workspace_id
        or listing.inventory_item_id != item.id
        or listing.channel != op.channel
        or not listing.external_id
        or not str(listing.external_sku or "").strip()
    ):
        raise InspectionUnavailable(
            "Marketplace link no longer identifies the original stock item. Review it manually."
        )
    # Even a correct SKU is insufficient if more than one channel link now
    # exists for the same item (e.g. a replaced and a stale Shopify variant).
    links = session.execute(select(models.ChannelListing.id).where(
        models.ChannelListing.workspace_id == workspace_id,
        models.ChannelListing.inventory_item_id == item.id,
        models.ChannelListing.channel == op.channel,
    )).scalars().all()
    if len(links) != 1:
        raise InspectionUnavailable("Several listings are linked to this item on that marketplace.")
    return item, listing


def _observe(workspace_id: uuid.UUID, scope: str, channel: str,
             external_id: str, sku: str, intent: dict[str, Any]):
    if scope == "stock":
        remote = STOCK_READERS[channel](
            workspace_id, external_id=external_id, expected_sku=sku,
        )
        desired = intent["quantity"]
        observed = remote.get("quantity")
        matched = (remote.get("manage_stock") is True
                   and type(observed) is int and observed == desired
                   and remote.get("stock_status") == ("instock" if desired else "outofstock"))
        return matched, {"expected_quantity": desired, "observed_quantity":
                         observed if type(observed) is int else None,
                         "tracking_enabled": remote.get("manage_stock") is True}
    if scope == "price":
        remote = PRICE_READERS[channel](
            workspace_id, external_id=external_id, expected_sku=sku,
            expected_currency=intent["currency"],
        )
        observed = remote.get("regular_price_cents")
        return observed == intent["price_cents"] and type(observed) is int, {
            "expected_price_cents": intent["price_cents"],
            "observed_price_cents": observed if type(observed) is int else None,
            "currency": intent["currency"],
        }
    if scope == "content":
        remote = read_woocommerce_workspace_content(
            workspace_id, external_id=external_id, expected_sku=sku,
        )
        actual = remote.get("fields") or {}
        fields = intent["changes"]
        mismatched = sorted(key for key, value in fields.items() if actual.get(key) != value)
        return not mismatched, {
            "checked_fields": sorted(fields), "different_fields": mismatched,
        }
    if scope == "close":
        remote = CLOSE_READERS[channel](
            workspace_id, external_id=external_id, expected_sku=sku,
        )
        return remote.get("status") == "draft", {
            "expected_status": "draft", "observed_status": str(remote.get("status") or "unknown")[:30],
        }
    raise InspectionUnavailable("This operation has no supported read-only inspection.")


def inspect(workspace_id: uuid.UUID, operation_id: uuid.UUID) -> dict[str, Any]:
    """Inspect exactly one uncertain operation; never execute a remote write.

    Results say whether the requested state exists *now*, not who caused it.
    Historical operations without immutable original intent remain manual.
    """
    with db.session_scope() as session:
        op = session.get(MarketplaceOperation, operation_id)
        if op is None or op.workspace_id != workspace_id:
            raise LookupError("Marketplace operation not found")
        scope = kind(op)
        if scope is None:
            raise InspectionUnavailable(
                "This operation has no complete original write snapshot. "
                "Review its remote result manually; do not repeat the upload."
            )
        item, listing = _target(session, workspace_id, op)
        intent = dict(op.job_payload or {})
        external_id = listing.external_id
        sku = listing.external_sku
        # Old operations may be observed but cannot be fully attributed when
        # there is no original link identity stored at initiation.
        identity_proven = (intent.get("external_id") == external_id
                           and intent.get("sku") == sku)
        item_id, listing_id = item.id, listing.id
        item_quantity = int(item.quantity or 0)
        master_unchanged = True
        if scope == "stock":
            master_unchanged = is_physical(item) and item_quantity == intent["quantity"]
        elif scope == "close":
            master_unchanged = is_physical(item) and item_quantity == 0
        # Retain no network-derived private strings in the operation record.
        original_status = op.status
        original_intent = dict(intent)

    # These helpers each issue only GET or GraphQL/query reads. No mutation
    # or fallback importer is dispatched even if the API request times out.
    matched, evidence = _observe(
        workspace_id, scope, op.channel, external_id, sku, intent,
    )
    with db.session_scope() as session:
        op = session.get(MarketplaceOperation, operation_id)
        if op is None or op.workspace_id != workspace_id:
            raise LookupError("Marketplace operation no longer exists")
        if (op.status != original_status or dict(op.job_payload or {}) != original_intent
                or op.channel_listing_id != listing_id or op.inventory_item_id != item_id):
            raise InspectionUnavailable("Operation changed during inspection. Refresh and review again.")
        current_item, current_listing = _target(session, workspace_id, op)
        if (current_listing.id != listing_id or current_listing.external_id != external_id
                or current_listing.external_sku != sku):
            raise InspectionUnavailable("Marketplace link changed during inspection.")
        if scope in {"stock", "close"} and (
            int(current_item.quantity or 0) != item_quantity
            or is_physical(current_item) != is_physical(item)
        ):
            raise InspectionUnavailable("Physical stock changed during inspection.")
        # A later attempt may have produced the observed state. Do not
        # retroactively attribute it to an older uncertain write.
        subsequent = session.execute(select(MarketplaceOperation.id).where(
            MarketplaceOperation.workspace_id == workspace_id,
            MarketplaceOperation.channel == op.channel,
            MarketplaceOperation.target_key == op.target_key,
            MarketplaceOperation.id != op.id,
            MarketplaceOperation.created_at > op.created_at,
        ).limit(1)).first() is not None
        reason = (
            "matched" if matched and identity_proven and master_unchanged and not subsequent
            else "different" if not matched
            else "link_unproven" if not identity_proven
            else "master_changed" if not master_unchanged
            else "later_attempt"
        )
        checked = utcnow()
        op.result = {
            **dict(op.result or {}),
            "inspection": {"checked_at": checked.isoformat(), "scope": scope,
                           "outcome": reason, **evidence},
        }
        if reason == "matched":
            op.status = "succeeded"
            op.verification = "remote_verified"
            op.last_error = None
            op.active_key = None
            op.completed_at = checked
        else:
            op.status = "attention"
            op.verification = "remote_mismatch" if reason == "different" else "manual_required"
            op.last_error = {
                "different": "Current marketplace state differs from the attempted change. The earlier outcome remains uncertain.",
                "link_unproven": "The original marketplace identity was not recorded; current matching state cannot prove this operation.",
                "master_changed": "Local physical stock differs from the original attempted state.",
                "later_attempt": "A later operation exists for this listing; its result must be reviewed separately.",
            }.get(reason, "Remote result requires manual review.")
            op.completed_at = checked
        # Intentionally do not mutate master stock, channel listing or
        # cross-channel action. They have separate sale and linking rules.
        return {
            "ok": True, "remote_write": False, "outcome": reason,
            "matched_now": matched, "resolved": reason == "matched",
            "checked_at": checked.isoformat(),
            "operation_id": str(op.id), "inspection": evidence,
            "note": ("Expected marketplace state is present now. This does not prove which request caused it."
                     if reason == "matched" else
                     "The historical write is still uncertain. No update or retry was sent."),
        }
