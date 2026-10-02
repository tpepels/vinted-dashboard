from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import select

from app import db, models
from app.workspace_ingest import record_workspace_snapshot


NOW = datetime.now(timezone.utc)


def _workspace(slug: str = "vinted-metadata") -> uuid.UUID:
    with db.session_scope() as session:
        workspace = models.Workspace(name="Metadata", slug=slug, settings={})
        session.add(workspace)
        session.flush()
        return workspace.id


def _snapshot(metadata: dict, *, title: str = "Metadata book") -> dict:
    return {
        "collected_at": NOW.timestamp(),
        "current_user": {"id": "user-1", "username": "seller"},
        "listings": [
            {
                "id": "V-1",
                "title": title,
                "status": "active",
                "price_cents": 1250,
                "currency": "EUR",
                "listed_at": NOW.isoformat(),
                "favourites": 3,
                "views": 20,
                "metadata": metadata,
            }
        ],
        "notifications": [],
        "orders": [],
    }


def test_vinted_metadata_populates_missing_master_fields_and_listing_extra():
    workspace_id = _workspace()
    metadata = {
        "condition": "Very good",
        "category": "Entertainment > Books > Fiction",
        "brand": "Penguin",
        "description": "A clean paperback copy.",
        "isbn": "9780141187761",
        "author": "George Orwell",
        "publisher": "Penguin",
        "language": "English",
    }

    result = record_workspace_snapshot(workspace_id, _snapshot(metadata))
    assert result["listings"] == 1

    with db.session_scope() as session:
        item = session.execute(
            select(models.InventoryItem).where(models.InventoryItem.workspace_id == workspace_id)
        ).scalars().one()
        listing = session.execute(
            select(models.ChannelListing).where(models.ChannelListing.workspace_id == workspace_id)
        ).scalars().one()

        assert item.condition == "Very good"
        assert item.category == "book"
        assert item.attributes["author"] == "George Orwell"
        assert item.attributes["isbn"] == "9780141187761"
        assert item.attributes["publisher"] == "Penguin"
        assert item.attributes["brand"] == "Penguin"
        assert item.attributes["description"] == "A clean paperback copy."
        assert item.attributes["vinted_category"] == "Entertainment > Books > Fiction"
        assert item.attributes["vinted_description"] == "A clean paperback copy."
        assert item.attributes["vinted_metadata_synced_at"]
        assert listing.extra["metadata"]["condition"] == "Very good"
        assert listing.extra["metadata"]["author"] == "George Orwell"


def test_vinted_metadata_does_not_overwrite_manual_master_values():
    workspace_id = _workspace("metadata-preserve")
    record_workspace_snapshot(
        workspace_id,
        _snapshot(
            {
                "condition": "Good",
                "category": "Entertainment > Books",
                "author": "Marketplace author",
                "publisher": "Marketplace publisher",
            }
        ),
    )

    with db.session_scope() as session:
        item = session.execute(
            select(models.InventoryItem).where(models.InventoryItem.workspace_id == workspace_id)
        ).scalars().one()
        item.condition = "Manual condition"
        item.attributes = {
            **dict(item.attributes or {}),
            "author": "Manual author",
            "publisher": "Manual publisher",
        }

    record_workspace_snapshot(
        workspace_id,
        _snapshot(
            {
                "condition": "Satisfactory",
                "category": "Entertainment > Books",
                "author": "Changed marketplace author",
                "publisher": "Changed marketplace publisher",
                "size": "Paperback",
            }
        ),
    )

    with db.session_scope() as session:
        item = session.execute(
            select(models.InventoryItem).where(models.InventoryItem.workspace_id == workspace_id)
        ).scalars().one()
        assert item.condition == "Manual condition"
        assert item.attributes["author"] == "Manual author"
        assert item.attributes["publisher"] == "Manual publisher"
        assert item.attributes["size"] == "Paperback"


def test_empty_later_metadata_does_not_erase_previous_listing_metadata():
    workspace_id = _workspace("metadata-sticky")
    record_workspace_snapshot(
        workspace_id,
        _snapshot({"condition": "Good", "brand": "Vintage"}),
    )
    record_workspace_snapshot(workspace_id, _snapshot({}))

    with db.session_scope() as session:
        listing = session.execute(
            select(models.ChannelListing).where(models.ChannelListing.workspace_id == workspace_id)
        ).scalars().one()
        assert listing.extra["metadata"]["condition"] == "Good"
        assert listing.extra["metadata"]["brand"] == "Vintage"
