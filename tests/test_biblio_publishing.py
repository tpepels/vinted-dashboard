from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models, publishing
from app.constants import Channel, ItemCategory, ListingStatus
from app.product_models import BackgroundJob
from app.workspace_ingest import record_workspace_snapshot


def _workspace():
    with db.session_scope() as session:
        workspace = models.Workspace(name="Books", slug="books", is_personal=True)
        session.add(workspace)
        session.flush()
        return workspace.id


def _source_book(workspace_id, *, author: str | None = "Franz Kafka"):
    with db.session_scope() as session:
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="BK-0001",
            title="Master title",
            category=ItemCategory.BOOK,
            quantity=1,
            currency="EUR",
            attributes={"default_price_cents": 999},
        )
        session.add(item)
        session.flush()
        listing = models.ChannelListing(
            workspace_id=workspace_id,
            inventory_item_id=item.id,
            channel=Channel.VINTED,
            external_id="123456",
            title="The Trial",
            price_cents=750,
            currency="EUR",
            status=ListingStatus.ACTIVE,
            quantity=1,
            url="https://www.vinted.pt/items/123456",
            extra={
                "metadata": {
                    "description": "English paperback in very good condition.",
                    "isbn": "9780099428640",
                    **({"author": author} if author else {}),
                }
            },
        )
        session.add(listing)
        session.flush()
        return item.id, listing.id


def test_biblio_candidate_prefers_vinted_source_and_uses_isbn_for_missing_author(monkeypatch):
    workspace_id = _workspace()
    item_id, listing_id = _source_book(workspace_id, author=None)
    monkeypatch.setattr(
        "app.publishing.stock_intake.lookup_isbn",
        lambda isbn: {
            "found": True,
            "isbn": isbn,
            "title": "The Trial",
            "author": "Franz Kafka",
            "publisher": "Penguin",
        },
    )

    with db.session_scope() as session:
        candidate = publishing.build_biblio_candidate(
            session,
            workspace_id,
            item_id,
            source_listing_id=listing_id,
        )

    assert candidate["ready"] is True
    assert candidate["fields"]["title"] == "The Trial"
    assert candidate["field_sources"]["title"] == "vinted"
    assert candidate["fields"]["description"] == "English paperback in very good condition."
    assert candidate["field_sources"]["description"] == "vinted"
    assert candidate["fields"]["isbn"] == "9780099428640"
    assert candidate["field_sources"]["isbn"] == "vinted"
    assert candidate["fields"]["price_cents"] == 750
    assert candidate["field_sources"]["price_cents"] == "vinted"
    assert candidate["fields"]["author"] == "Franz Kafka"
    assert candidate["field_sources"]["author"] == "isbn"
    assert candidate["source"]["channel"] == "vinted"


def test_biblio_upsert_links_listing_to_same_physical_item():
    workspace_id = _workspace()
    item_id, listing_id = _source_book(workspace_id)

    with db.session_scope() as session:
        workspace = session.get(models.Workspace, workspace_id)
        candidate = publishing.build_biblio_candidate(
            session,
            workspace_id,
            item_id,
            source_listing_id=listing_id,
            enrich_isbn=False,
        )
        biblio = publishing.upsert_biblio_listing(
            session,
            workspace,
            item_id,
            candidate,
        )
        biblio_id = biblio.id

    with db.session_scope() as session:
        biblio = session.get(models.ChannelListing, biblio_id)
        assert biblio.inventory_item_id == item_id
        assert biblio.channel == Channel.BIBLIO
        assert biblio.external_id == "BK-0001"
        assert biblio.external_sku == "BK-0001"
        assert biblio.title == "The Trial"
        assert biblio.price_cents == 750
        assert biblio.extra["author"] == "Franz Kafka"
        assert biblio.extra["description"] == "English paperback in very good condition."
        assert biblio.extra["isbn"] == "9780099428640"
        assert biblio.extra["source_channel"] == Channel.VINTED
        assert biblio.extra["source_listing_id"] == str(listing_id)


def test_biblio_override_repairs_only_missing_preflight_field():
    candidate = {
        "fields": {
            "sku": "BK-1",
            "title": "Book",
            "author": None,
            "description": "Description",
            "isbn": None,
            "price_cents": 500,
            "currency": "EUR",
            "quantity": 1,
        },
        "field_sources": {},
        "missing": ["author"],
        "ready": False,
    }
    result = publishing.apply_biblio_overrides(candidate, {"author": "Author Name"})
    assert result["ready"] is True
    assert result["missing"] == []
    assert result["fields"]["author"] == "Author Name"
    assert result["field_sources"]["author"] == "review"


def test_vinted_ingest_preserves_source_metadata_and_images():
    workspace_id = _workspace()
    result = record_workspace_snapshot(
        workspace_id,
        {
            "collected_at": 1_799_000_000,
            "current_user": {},
            "notifications": [],
            "orders": [],
            "listings": [
                {
                    "id": "991",
                    "title": "A Vinted Book",
                    "price_cents": 650,
                    "currency": "EUR",
                    "status": "active",
                    "vinted_url": "https://www.vinted.pt/items/991",
                    "image_url": "https://images.example/one.jpg",
                    "image_urls": [
                        "https://images.example/one.jpg",
                        "https://images.example/two.jpg",
                    ],
                    "metadata": {
                        "category": "Books",
                        "description": "Source description",
                        "isbn": "9780140328721",
                        "author": "Roald Dahl",
                    },
                }
            ],
        },
        extension_version="2.5.0",
    )
    assert result["listings"] == 1

    with db.session_scope() as session:
        listing = session.execute(
            select(models.ChannelListing).where(models.ChannelListing.external_id == "991")
        ).scalar_one()
        item = session.get(models.InventoryItem, listing.inventory_item_id)
        assert listing.extra["metadata"]["description"] == "Source description"
        assert listing.extra["image_urls"] == [
            "https://images.example/one.jpg",
            "https://images.example/two.jpg",
        ]
        assert item.category == ItemCategory.BOOK
        assert item.attributes["isbn"] == "9780140328721"
        assert item.attributes["author"] == "Roald Dahl"
        assert item.attributes["image_source"] == "vinted"
        assert item.attributes["image_urls"][1].endswith("two.jpg")


def _registered_client(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "publisher@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Publisher",
        },
    )
    assert registered.status_code == 200, registered.text
    return client, registered.json()["csrf_token"]


def test_publish_endpoint_creates_linked_biblio_listing_and_queues_ftp(monkeypatch):
    client, csrf = _registered_client(monkeypatch)
    credentials = client.put(
        "/api/app/connectors/biblio/credentials",
        headers={"X-CSRF-Token": csrf},
        json={"values": {"username": "seller", "password": "secret"}},
    )
    assert credentials.status_code == 200, credentials.text

    with db.session_scope() as session:
        membership = session.execute(select(models.Membership)).scalar_one()
        workspace_id = membership.workspace_id
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="BK-API-1",
            title="Master title",
            category=ItemCategory.BOOK,
            quantity=1,
            currency="EUR",
            attributes={},
        )
        session.add(item)
        session.flush()
        source = models.ChannelListing(
            workspace_id=workspace_id,
            inventory_item_id=item.id,
            channel=Channel.VINTED,
            external_id="v-api-1",
            title="Vinted title",
            price_cents=800,
            currency="EUR",
            status=ListingStatus.ACTIVE,
            quantity=1,
            extra={
                "metadata": {
                    "author": "Source Author",
                    "description": "Source description",
                    "isbn": "9780140328721",
                }
            },
        )
        session.add(source)
        session.flush()
        item_id = item.id
        source_id = source.id

    preview = client.get(
        f"/api/app/inventory/{item_id}/publish/biblio",
        params={"source_listing_id": str(source_id)},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["publish_ready"] is True
    assert preview.json()["source"]["channel"] == "vinted"

    published = client.post(
        f"/api/app/inventory/{item_id}/publish/biblio",
        headers={"X-CSRF-Token": csrf},
        json={"source_listing_id": str(source_id)},
    )
    assert published.status_code == 200, published.text
    assert published.json()["queued"] is True

    with db.session_scope() as session:
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.channel == Channel.BIBLIO,
            )
        ).scalar_one()
        assert listing.inventory_item_id == item_id
        assert listing.title == "Vinted title"
        job = session.execute(
            select(BackgroundJob).where(BackgroundJob.job_type == "biblio_sync")
        ).scalar_one()
        assert job.workspace_id == workspace_id
        assert job.status == "queued"
