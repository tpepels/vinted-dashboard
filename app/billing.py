"""Small billing abstraction with an optional Stripe provider.

Billing is disabled by default so local/self-hosted installs require no
external service.  Production can enable Stripe with environment variables.
Business code consumes the generic helpers in this module rather than
depending on Stripe object shapes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app import db, models
from app.constants import BillingStatus


BILLING_ENABLED = os.getenv("BILLING_ENABLED", "false").strip().lower() in {
    "1", "true", "yes", "on"
}
BILLING_PROVIDER = os.getenv("BILLING_PROVIDER", "stripe").strip().lower() or "stripe"


@dataclass(frozen=True)
class BillingSummary:
    enabled: bool
    provider: str | None
    status: str
    customer_id: str | None


def summary(workspace: models.Workspace) -> BillingSummary:
    return BillingSummary(
        enabled=BILLING_ENABLED,
        provider=BILLING_PROVIDER if BILLING_ENABLED else None,
        status=workspace.billing_status,
        customer_id=workspace.stripe_customer_id,
    )


def _stripe_secret() -> str:
    value = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if not value:
        raise RuntimeError("Stripe is enabled but STRIPE_SECRET_KEY is not configured")
    return value


def _stripe_post(path: str, fields: dict[str, Any]) -> dict[str, Any]:
    encoded = urllib.parse.urlencode(
        [(key, str(value)) for key, value in fields.items() if value not in (None, "")]
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.stripe.com/v1/{path.lstrip('/')}",
        data=encoded,
        method="POST",
        headers={
            "Authorization": f"Bearer {_stripe_secret()}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("Billing provider request failed") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Billing provider returned an invalid response")
    return payload


def create_checkout(workspace: models.Workspace, email: str) -> str:
    if not BILLING_ENABLED:
        raise RuntimeError("Billing is disabled")
    if BILLING_PROVIDER != "stripe":
        raise RuntimeError("Configured billing provider is not supported")
    price_id = os.getenv("STRIPE_PRICE_ID", "").strip()
    app_url = os.getenv("PUBLIC_APP_URL", "").strip().rstrip("/")
    if not price_id or not app_url:
        raise RuntimeError("STRIPE_PRICE_ID and PUBLIC_APP_URL are required for checkout")
    fields: dict[str, Any] = {
        "mode": "subscription",
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": 1,
        "success_url": f"{app_url}/?billing=success",
        "cancel_url": f"{app_url}/?billing=cancel",
        "client_reference_id": str(workspace.id),
        "metadata[workspace_id]": str(workspace.id),
    }
    if workspace.stripe_customer_id:
        fields["customer"] = workspace.stripe_customer_id
    else:
        fields["customer_email"] = email
    result = _stripe_post("checkout/sessions", fields)
    url = str(result.get("url") or "")
    if not url:
        raise RuntimeError("Billing provider returned no checkout URL")
    return url


def create_portal(workspace: models.Workspace) -> str:
    if not BILLING_ENABLED:
        raise RuntimeError("Billing is disabled")
    if not workspace.stripe_customer_id:
        raise RuntimeError("No billing customer exists for this workspace")
    app_url = os.getenv("PUBLIC_APP_URL", "").strip().rstrip("/")
    if not app_url:
        raise RuntimeError("PUBLIC_APP_URL is required for the billing portal")
    result = _stripe_post(
        "billing_portal/sessions",
        {"customer": workspace.stripe_customer_id, "return_url": app_url},
    )
    url = str(result.get("url") or "")
    if not url:
        raise RuntimeError("Billing provider returned no portal URL")
    return url


def verify_stripe_webhook(payload: bytes, signature_header: str) -> dict[str, Any]:
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET is not configured")
    pieces: dict[str, list[str]] = {}
    for part in signature_header.split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        pieces.setdefault(key.strip(), []).append(value.strip())
    timestamp = (pieces.get("t") or [None])[0]
    signatures = pieces.get("v1") or []
    if not timestamp or not signatures:
        raise ValueError("Invalid Stripe signature header")
    try:
        signed_at = int(timestamp)
    except ValueError as exc:
        raise ValueError("Invalid Stripe signature timestamp") from exc
    if abs(int(time.time()) - signed_at) > 300:
        raise ValueError("Stripe signature is too old")
    expected = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("ascii") + b"." + payload,
        hashlib.sha256,
    ).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise ValueError("Invalid Stripe webhook signature")
    event = json.loads(payload.decode("utf-8"))
    if not isinstance(event, dict):
        raise ValueError("Invalid Stripe webhook body")
    return event


def apply_stripe_event(event: dict[str, Any]) -> None:
    event_type = str(event.get("type") or "")
    obj = dict(((event.get("data") or {}).get("object") or {}))
    workspace_id = None
    metadata = obj.get("metadata") or {}
    if isinstance(metadata, dict):
        workspace_id = metadata.get("workspace_id")
    customer_id = str(obj.get("customer") or "")

    with db.session_scope() as session:
        workspace = None
        if workspace_id:
            try:
                workspace = session.get(models.Workspace, workspace_id)
            except Exception:
                workspace = None
        if workspace is None and customer_id:
            workspace = session.execute(
                select(models.Workspace).where(models.Workspace.stripe_customer_id == customer_id)
            ).scalar_one_or_none()
        if workspace is None:
            return

        if customer_id:
            workspace.stripe_customer_id = customer_id

        if event_type == "checkout.session.completed":
            workspace.billing_status = BillingStatus.ACTIVE
            return
        if event_type.startswith("customer.subscription."):
            status = str(obj.get("status") or "")
            mapping = {
                "trialing": BillingStatus.TRIALING,
                "active": BillingStatus.ACTIVE,
                "past_due": BillingStatus.PAST_DUE,
                "unpaid": BillingStatus.PAST_DUE,
                "canceled": BillingStatus.CANCELED,
                "incomplete_expired": BillingStatus.CANCELED,
            }
            if event_type == "customer.subscription.deleted":
                workspace.billing_status = BillingStatus.CANCELED
            elif status in mapping:
                workspace.billing_status = mapping[status]
