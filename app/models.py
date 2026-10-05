"""ORM models for the workspace/master-inventory data model.

The workspace database is the runtime source of truth. Pre-workspace data can
be imported additively by :mod:`app.legacy_migration` into a bootstrap
personal workspace.

Design notes:

- Every workspace-owned row carries an explicit ``workspace_id`` (even where
  it could be inferred transitively through a relationship) so isolation
  queries/tests can filter on a single, always-present column.
- Category-specific item fields (book vs. clothing vs. future categories)
  live in ``InventoryItem.attributes`` (JSON) rather than as dozens of
  nullable columns, per the product direction to keep the core schema
  category-neutral.
- Money is stored as integer minor units (``*_cents``) throughout the
  application.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.constants import (
    BillingStatus,
    ChannelAccountStatus,
    ItemCategory,
    ItemStatus,
    ListingStatus,
    MembershipRole,
    SyncRunStatus,
)
from app.db import Base, JSONVariant, UTCDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class Workspace(TimestampMixin, Base):
    """An account/tenant. Every user-owned object belongs to exactly one
    workspace; this is the unit of data isolation and billing."""

    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    is_personal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    billing_status: Mapped[str] = mapped_column(String(20), nullable=False, default=BillingStatus.DEV)
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    trial_ends_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    #: Workspace-level settings, e.g. "what should I do today" strategy
    #: thresholds, so they need not be hardcoded in view code.
    settings: Mapped[dict[str, Any]] = mapped_column(JSONVariant, nullable=False, default=dict)

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    inventory_items: Mapped[list["InventoryItem"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    channel_accounts: Mapped[list["ChannelAccount"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    #: Nullable so an imported bootstrap owner can exist before that person
    #: claims the workspace by registering a real account.
    hashed_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Membership(TimestampMixin, Base):
    """Join table granting a user a role within a workspace."""

    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "workspace_id", name="uq_membership_user_workspace"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=MembershipRole.OWNER)

    user: Mapped[User] = relationship(back_populates="memberships")
    workspace: Mapped[Workspace] = relationship(back_populates="memberships")


class InventoryItem(TimestampMixin, Base):
    """The master physical inventory record. A single physical item may have
    many :class:`ChannelListing` rows (one per marketplace) but exactly one
    ``InventoryItem``."""

    __tablename__ = "inventory_items"
    __table_args__ = (UniqueConstraint("workspace_id", "sku", name="uq_inventory_item_workspace_sku"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sku: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False, default=ItemCategory.GENERAL)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    condition: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    cost_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ItemStatus.ACTIVE)
    #: Category-specific metadata, e.g. {"author": ..., "isbn": ...} for
    #: books or {"brand": ..., "size": ...} for clothing.
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONVariant, nullable=False, default=dict)

    workspace: Mapped[Workspace] = relationship(back_populates="inventory_items")
    listings: Mapped[list["ChannelListing"]] = relationship(back_populates="inventory_item")
    sales: Mapped[list["Sale"]] = relationship(back_populates="inventory_item")


class ChannelAccount(TimestampMixin, Base):
    """A connected marketplace/connector account within a workspace (e.g.
    "my eBay seller account", "my BIBLIO FTP account")."""

    __tablename__ = "channel_accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ChannelAccountStatus.DISCONNECTED)
    #: Non-secret connector configuration (e.g. eBay site id). Real secrets
    #: should not be stored here until encryption-at-rest is implemented
    #: (tracked for the Phase 2 connector work); for now connectors keep
    #: reading credentials from server-side environment variables.
    config: Mapped[dict[str, Any]] = mapped_column(JSONVariant, nullable=False, default=dict)
    credentials_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)

    workspace: Mapped[Workspace] = relationship(back_populates="channel_accounts")
    listings: Mapped[list["ChannelListing"]] = relationship(back_populates="channel_account")
    sync_runs: Mapped[list["ConnectorSyncRun"]] = relationship(back_populates="channel_account")
    profile_observations: Mapped[list["ProfileObservation"]] = relationship(back_populates="channel_account")


class ChannelListing(TimestampMixin, Base):
    """A listing for one :class:`InventoryItem` on one marketplace/channel."""

    __tablename__ = "channel_listings"
    __table_args__ = (
        UniqueConstraint("workspace_id", "channel", "external_id", name="uq_channel_listing_identity"),
        Index("ix_channel_listings_item", "inventory_item_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    inventory_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("inventory_items.id", ondelete="SET NULL"), nullable=True
    )
    channel_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("channel_accounts.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    external_sku: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    price_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ListingStatus.ACTIVE)
    url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utcnow)
    #: Fields that do not have a first-class column yet (raw payloads,
    #: isbn/author convenience copies, last-known views/favourites, etc).
    extra: Mapped[dict[str, Any]] = mapped_column(JSONVariant, nullable=False, default=dict)

    workspace: Mapped[Workspace] = relationship()
    inventory_item: Mapped[Optional[InventoryItem]] = relationship(back_populates="listings")
    channel_account: Mapped[Optional[ChannelAccount]] = relationship(back_populates="listings")
    snapshots: Mapped[list["ListingSnapshot"]] = relationship(
        back_populates="channel_listing", cascade="all, delete-orphan"
    )
    favorite_events: Mapped[list["FavoriteEvent"]] = relationship(back_populates="channel_listing")
    market_research: Mapped[list["MarketResearch"]] = relationship(back_populates="channel_listing")


class ListingSnapshot(Base):
    """A point-in-time observation of a listing (price/status/views/
    favourites), used to draw per-listing history graphs."""

    __tablename__ = "listing_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "channel_listing_id", "captured_at", name="uq_listing_snapshot_identity"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channel_listings.id", ondelete="CASCADE"), nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    price_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    views: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    favourites: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    raw: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONVariant, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow, nullable=False)

    channel_listing: Mapped[ChannelListing] = relationship(back_populates="snapshots")


class ConnectorSyncRun(Base):
    """A channel-neutral log entry for one connector sync/import/export run."""

    __tablename__ = "connector_sync_runs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "channel", "run_type", "started_at", name="uq_connector_sync_run_identity"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("channel_accounts.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    run_type: Mapped[str] = mapped_column(String(30), nullable=False, default="snapshot")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=SyncRunStatus.SUCCESS)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    item_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    active_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    delete_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONVariant, nullable=False, default=dict)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow, nullable=False)

    channel_account: Mapped[Optional[ChannelAccount]] = relationship(back_populates="sync_runs")


class Sale(TimestampMixin, Base):
    """A sale or purchase order observed on a channel."""

    __tablename__ = "sales"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "channel", "direction", "external_order_id", name="uq_sale_identity"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("channel_accounts.id", ondelete="SET NULL"), nullable=True
    )
    inventory_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("inventory_items.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    external_order_id: Mapped[str] = mapped_column(String(200), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    counterparty: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    total_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    lifecycle_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    occurred_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utcnow)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONVariant, nullable=False, default=dict)

    inventory_item: Mapped[Optional[InventoryItem]] = relationship(back_populates="sales")


class FavoriteEvent(Base):
    """A "someone favourited your item" notification event (Vinted-specific
    analytics input)."""

    __tablename__ = "favorite_events"

    #: The marketplace notification id is the stable natural key.
    notification_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_listing_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("channel_listings.id", ondelete="SET NULL"), nullable=True
    )
    item_external_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    item_title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    actor: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    occurred_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utcnow)

    channel_listing: Mapped[Optional[ChannelListing]] = relationship(back_populates="favorite_events")


class MarketResearch(Base):
    """Market-research pricing samples gathered for a listing."""

    __tablename__ = "market_research"
    __table_args__ = (Index("ix_market_research_listing", "listing_external_id", "requested_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_listing_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("channel_listings.id", ondelete="SET NULL"), nullable=True
    )
    listing_external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    query: Mapped[str] = mapped_column(String(500), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utcnow)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    completed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    sample_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    min_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    median_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    results: Mapped[Optional[list[Any]]] = mapped_column(JSONVariant, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    channel_listing: Mapped[Optional[ChannelListing]] = relationship(back_populates="market_research")


class ProfileObservation(Base):
    """Follower/following counts for a channel account over time (Vinted
    audience analytics)."""

    __tablename__ = "profile_observations"
    __table_args__ = (
        UniqueConstraint(
            "channel_account_id", "captured_at", name="uq_profile_observation_identity"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channel_accounts.id", ondelete="CASCADE"), nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    followers: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    following: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow, nullable=False)

    channel_account: Mapped[ChannelAccount] = relationship(back_populates="profile_observations")


class LegacyBackfillRun(Base):
    """Bookkeeping so ``app.legacy_migration`` can run idempotently/repeatedly
    without duplicating data from the same legacy file."""

    __tablename__ = "legacy_backfill_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_path: Mapped[str] = mapped_column(String(500), nullable=False)
    #: Cheap change-detection signature (size + mtime) for the legacy file.
    source_signature: Mapped[str] = mapped_column(String(200), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONVariant, nullable=False, default=dict)
