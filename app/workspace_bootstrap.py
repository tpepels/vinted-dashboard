"""Shared helpers for resolving the bootstrap workspace/owner/channel-account
rows in the workspace/inventory ORM schema (``app.models``).

Both the one-time legacy data backfill (:mod:`app.legacy_migration`) and live
connector syncs (:mod:`app.connectors`) need to resolve the *same* bootstrap
workspace, owner and per-channel account rows - keeping that logic here
(rather than duplicated, or imported from one into the other) avoids the two
code paths ever disagreeing about which rows those are. This module is
intentionally dependency-free of ``app.channels``/``app.legacy_migration``/
``app.connectors`` so none of them create an import cycle by depending on it.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.constants import BillingStatus, Channel, ChannelAccountStatus, MembershipRole

#: One-time bootstrap workspace created on first use (legacy backfill or
#: first live sync, whichever happens first) and reused afterwards. A
#: deployment with no multi-tenant needs can leave these at their defaults.
BOOTSTRAP_WORKSPACE_NAME = os.getenv("BOOTSTRAP_WORKSPACE_NAME", "Personal Workspace")
BOOTSTRAP_WORKSPACE_SLUG = os.getenv("BOOTSTRAP_WORKSPACE_SLUG", "personal")
BOOTSTRAP_OWNER_EMAIL = os.getenv("BOOTSTRAP_OWNER_EMAIL", "owner@example.com")

CHANNEL_DISPLAY_NAMES = {
    Channel.VINTED: "Vinted",
    Channel.EBAY: "eBay",
    Channel.BIBLIO: "BIBLIO",
}


def clean_isbn(value: Any) -> Optional[str]:
    """Normalizes a raw ISBN-ish value to bare ISBN-10/13 digits (plus a
    possible trailing check-digit ``X``), or ``None`` if it doesn't look
    like a real ISBN."""
    text = re.sub(r"[^0-9Xx]", "", str(value or ""))
    return text.upper() if len(text) in {10, 13} else None


def normalize_sku(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def get_or_create_workspace(session: Session, name: str, slug: str) -> models.Workspace:
    workspace = session.execute(
        select(models.Workspace).where(models.Workspace.slug == slug)
    ).scalar_one_or_none()
    if workspace is not None:
        return workspace
    workspace = models.Workspace(
        name=name, slug=slug, is_personal=True, billing_status=BillingStatus.DEV
    )
    session.add(workspace)
    session.flush()
    return workspace


def get_or_create_owner(session: Session, workspace: models.Workspace, email: str) -> models.User:
    user = session.execute(select(models.User).where(models.User.email == email)).scalar_one_or_none()
    if user is None:
        user = models.User(email=email, display_name="Owner", is_active=True)
        session.add(user)
        session.flush()
    membership = session.execute(
        select(models.Membership).where(
            models.Membership.user_id == user.id,
            models.Membership.workspace_id == workspace.id,
        )
    ).scalar_one_or_none()
    if membership is None:
        session.add(
            models.Membership(user_id=user.id, workspace_id=workspace.id, role=MembershipRole.OWNER)
        )
        session.flush()
    return user


def get_or_create_channel_account(
    session: Session,
    workspace: models.Workspace,
    channel: str,
    cache: dict[str, models.ChannelAccount],
) -> tuple[models.ChannelAccount, bool]:
    """Returns ``(account, created)``. ``cache`` is an in/out per-call cache
    (keyed by channel) so a caller processing many rows for the same channel
    only hits the database once."""
    cached = cache.get(channel)
    if cached is not None:
        return cached, False
    account = session.execute(
        select(models.ChannelAccount).where(
            models.ChannelAccount.workspace_id == workspace.id,
            models.ChannelAccount.channel == channel,
        )
    ).scalar_one_or_none()
    created = account is None
    if account is None:
        account = models.ChannelAccount(
            workspace_id=workspace.id,
            channel=channel,
            display_name=CHANNEL_DISPLAY_NAMES.get(channel, channel.title()),
            status=ChannelAccountStatus.CONNECTED,
        )
        session.add(account)
        session.flush()
    cache[channel] = account
    return account, created


def get_or_create_bootstrap_workspace(session: Session) -> models.Workspace:
    """Convenience wrapper combining :func:`get_or_create_workspace` and
    :func:`get_or_create_owner` for the single bootstrap workspace, using the
    module-level ``BOOTSTRAP_*`` defaults."""
    workspace = get_or_create_workspace(session, BOOTSTRAP_WORKSPACE_NAME, BOOTSTRAP_WORKSPACE_SLUG)
    get_or_create_owner(session, workspace, BOOTSTRAP_OWNER_EMAIL)
    return workspace
