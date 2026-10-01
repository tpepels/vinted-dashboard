from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app import db, models
from app.constants import ChannelAccountStatus, ItemCategory, ListingStatus, MembershipRole


@pytest.fixture()
def session_factory():
    """Fresh in-memory database per test, isolated from any other test's
    state (the module-level engine/session factory in ``app.db`` is process
    global, so each test must re-init it)."""

    db.init_engine("sqlite://")
    db.create_all()
    return db.session_scope


def test_workspace_user_membership_roundtrip(session_factory):
    with session_factory() as session:
        workspace = models.Workspace(name="Acme", slug="acme")
        user = models.User(email="owner@example.com", display_name="Owner")
        session.add_all([workspace, user])
        session.flush()
        session.add(
            models.Membership(user_id=user.id, workspace_id=workspace.id, role=MembershipRole.OWNER)
        )

    with session_factory() as session:
        workspace = session.query(models.Workspace).filter_by(slug="acme").one()
        assert workspace.name == "Acme"
        assert len(workspace.memberships) == 1
        assert workspace.memberships[0].role == MembershipRole.OWNER


def test_inventory_item_sku_unique_per_workspace(session_factory):
    with session_factory() as session:
        workspace = models.Workspace(name="Acme", slug="acme")
        session.add(workspace)
        session.flush()
        session.add(
            models.InventoryItem(workspace_id=workspace.id, sku="SKU-1", title="Item", category=ItemCategory.GENERAL)
        )

    with pytest.raises(IntegrityError):
        with session_factory() as session:
            workspace = session.query(models.Workspace).filter_by(slug="acme").one()
            session.add(
                models.InventoryItem(
                    workspace_id=workspace.id, sku="SKU-1", title="Duplicate", category=ItemCategory.GENERAL
                )
            )


def test_datetime_columns_round_trip_as_aware_utc(session_factory):
    """Regression test: SQLite silently drops tzinfo on a plain
    ``DateTime(timezone=True)`` column, which breaks any later comparison
    against a freshly-constructed aware datetime. ``UTCDateTime`` must
    normalize this so every value read back is aware UTC, on every backend.
    """

    with session_factory() as session:
        workspace = models.Workspace(name="Acme", slug="acme")
        session.add(workspace)
        session.flush()
        account = models.ChannelAccount(
            workspace_id=workspace.id, channel="vinted", status=ChannelAccountStatus.CONNECTED
        )
        session.add(account)
        session.flush()
        item = models.InventoryItem(workspace_id=workspace.id, sku="SKU-1", title="Item")
        session.add(item)
        session.flush()
        listing = models.ChannelListing(
            workspace_id=workspace.id,
            inventory_item_id=item.id,
            channel_account_id=account.id,
            channel="vinted",
            external_id="abc",
            title="Item",
            status=ListingStatus.ACTIVE,
            first_seen_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
        )
        session.add(listing)
        session.flush()
        listing_id = listing.id

    with session_factory() as session:
        listing = session.get(models.ChannelListing, listing_id)
        assert listing.first_seen_at.tzinfo is not None
        # Would raise TypeError ("can't compare offset-naive and
        # offset-aware datetimes") if tzinfo were lost on read.
        assert datetime.now(timezone.utc) >= listing.first_seen_at
