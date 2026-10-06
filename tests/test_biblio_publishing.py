from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models, publishing
from app.constants import Channel, ItemCategory, ListingStatus
from app.product_models import BackgroundJob
from app.workspace_ingest import classify_vinted_category, record_workspace_snapshot


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
                },
                "image_urls": [
                    "https://images1.vinted.net/t/01_one.jpg",
                    "https://images1.vinted.net/t/02_two.webp",
                ],
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
    assert candidate["field_sources"]["title"] == "isbn"
    assert candidate["fields"]["description"] == "English paperback in very good condition."
    assert candidate["field_sources"]["description"] == "vinted"
    assert candidate["fields"]["isbn"] == "9780099428640"
    assert candidate["field_sources"]["isbn"] == "vinted"
    assert candidate["fields"]["price_cents"] == 750
    assert candidate["field_sources"]["price_cents"] == "vinted"
    assert candidate["fields"]["author"] == "Franz Kafka"
    assert candidate["field_sources"]["author"] == "isbn"
    assert candidate["fields"]["book_id"] == "BK-0001"
    assert candidate["source"]["channel"] == "vinted"
    assert candidate["source"]["photo_count"] == 2
    assert candidate["source"]["image_urls"][0].startswith("https://images1.vinted.net/")


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
        assert biblio.extra["image_source"] == Channel.VINTED
        assert len(biblio.extra["image_urls"]) == 2


def test_biblio_override_repairs_only_missing_preflight_field():
    candidate = {
        "fields": {
            "sku": "BK-1",
            "book_id": "BK-1",
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
    monkeypatch.setattr(
        "app.publishing.stock_intake.lookup_isbn",
        lambda isbn: {
            "found": True,
            "isbn": isbn,
            "title": "Clean ISBN Title",
            "author": "Source Author",
            "publisher": "Publisher",
        },
    )
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
        assert listing.title == "Clean ISBN Title"
        job = session.execute(
            select(BackgroundJob).where(BackgroundJob.job_type == "biblio_sync")
        ).scalar_one()
        assert job.workspace_id == workspace_id
        assert job.status == "queued"



def test_biblio_preflight_tolerates_multiple_historical_biblio_rows():
    workspace_id = _workspace()
    item_id, listing_id = _source_book(workspace_id)

    with db.session_scope() as session:
        old = models.ChannelListing(
            workspace_id=workspace_id,
            inventory_item_id=item_id,
            channel=Channel.BIBLIO,
            external_id="OLD-BIBLIO-ID",
            title="Old BIBLIO row",
            price_cents=500,
            currency="EUR",
            status=ListingStatus.INACTIVE,
            quantity=0,
        )
        current = models.ChannelListing(
            workspace_id=workspace_id,
            inventory_item_id=item_id,
            channel=Channel.BIBLIO,
            external_id="CURRENT-BIBLIO-ID",
            title="Current BIBLIO row",
            price_cents=700,
            currency="EUR",
            status=ListingStatus.ACTIVE,
            quantity=1,
        )
        session.add_all([old, current])

    with db.session_scope() as session:
        candidate = publishing.build_biblio_candidate(
            session,
            workspace_id,
            item_id,
            source_listing_id=listing_id,
            enrich_isbn=False,
        )

    assert candidate["ready"] is True
    assert candidate["already_listed"] is True
    assert candidate["existing_biblio_listing_id"] is not None


def test_biblio_upsert_prefers_active_row_when_historical_duplicates_exist():
    workspace_id = _workspace()
    item_id, listing_id = _source_book(workspace_id)

    with db.session_scope() as session:
        workspace = session.get(models.Workspace, workspace_id)
        old = models.ChannelListing(
            workspace_id=workspace_id,
            inventory_item_id=item_id,
            channel=Channel.BIBLIO,
            external_id="OLD-BIBLIO-ID",
            title="Old BIBLIO row",
            price_cents=500,
            currency="EUR",
            status=ListingStatus.INACTIVE,
            quantity=0,
        )
        current = models.ChannelListing(
            workspace_id=workspace_id,
            inventory_item_id=item_id,
            channel=Channel.BIBLIO,
            external_id="CURRENT-BIBLIO-ID",
            title="Current BIBLIO row",
            price_cents=700,
            currency="EUR",
            status=ListingStatus.ACTIVE,
            quantity=1,
        )
        session.add_all([old, current])
        session.flush()

        candidate = publishing.build_biblio_candidate(
            session,
            workspace_id,
            item_id,
            source_listing_id=listing_id,
            enrich_isbn=False,
        )
        updated = publishing.upsert_biblio_listing(
            session,
            workspace,
            item_id,
            candidate,
        )
        updated_id = updated.id
        current_id = current.id

    assert updated_id == current_id



def test_general_vinted_book_metadata_is_publishable_in_inventory_and_listings(monkeypatch):
    client, _csrf = _registered_client(monkeypatch)
    with db.session_scope() as session:
        membership = session.execute(select(models.Membership)).scalar_one()
        workspace_id = membership.workspace_id
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="VINTED-GENERAL-BOOK",
            title="Destination India",
            category=ItemCategory.GENERAL,
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
            external_id="9826364597",
            title="Destination India - The Lonely Hearts Travel Club #2 - Katy Colins",
            price_cents=100,
            currency="EUR",
            status=ListingStatus.ACTIVE,
            quantity=1,
            url="https://www.vinted.pt/items/9826364597",
            extra={
                "metadata": {
                    "author": "Katy Colins",
                    "isbn": "9780263923698",
                    "description": "Very good English paperback.",
                }
            },
        )
        session.add(source)
        session.flush()
        item_id = str(item.id)

    inventory = client.get("/api/app/inventory").json()["items"]
    row = next(row for row in inventory if row["id"] == item_id)
    assert row["category"] == ItemCategory.GENERAL
    assert row["biblio_publishable"] is True
    assert row["biblio_source_listing_id"] is not None

    listings = client.get("/api/app/listings").json()["listings"]
    listing = next(row for row in listings if row["external_id"] == "9826364597")
    assert listing["inventory_category"] == ItemCategory.GENERAL
    assert listing["biblio_publishable"] is True

    preview = client.get(
        f"/api/app/inventory/{item_id}/publish/biblio",
        params={"source_listing_id": listing["id"]},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["ready"] is True
    assert preview.json()["fields"]["isbn"] == "9780263923698"
    assert preview.json()["fields"]["author"] == "Katy Colins"


def test_vinted_ingest_promotes_general_item_to_book_from_isbn_without_category():
    workspace_id = _workspace()
    record_workspace_snapshot(
        workspace_id,
        {
            "collected_at": 1_799_100_000,
            "current_user": {},
            "notifications": [],
            "orders": [],
            "listings": [
                {
                    "id": "BOOK-BY-ISBN",
                    "title": "Destination India",
                    "price_cents": 100,
                    "currency": "EUR",
                    "status": "active",
                    "vinted_url": "https://www.vinted.pt/items/BOOK-BY-ISBN",
                    "metadata": {
                        "isbn": "9780263923698",
                        "author": "Katy Colins",
                        "description": "Very good English paperback.",
                    },
                }
            ],
        },
        extension_version="2.8.0",
    )

    with db.session_scope() as session:
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.external_id == "BOOK-BY-ISBN"
            )
        ).scalar_one()
        item = session.get(models.InventoryItem, listing.inventory_item_id)
        assert item.category == ItemCategory.BOOK
        assert item.attributes["isbn"] == "9780263923698"
        assert item.attributes["author"] == "Katy Colins"



def test_biblio_conflicting_book_id_is_repairable_inline():
    workspace_id = _workspace()
    item_id, listing_id = _source_book(workspace_id)

    with db.session_scope() as session:
        other = models.InventoryItem(
            workspace_id=workspace_id,
            sku="OTHER",
            title="Other book",
            category=ItemCategory.BOOK,
            quantity=1,
            currency="EUR",
            attributes={},
        )
        session.add(other)
        session.flush()
        session.add(
            models.ChannelListing(
                workspace_id=workspace_id,
                inventory_item_id=other.id,
                channel=Channel.BIBLIO,
                external_id="BK-0001",
                external_sku="BK-0001",
                title="Other BIBLIO listing",
                price_cents=500,
                currency="EUR",
                status=ListingStatus.ACTIVE,
                quantity=1,
            )
        )

    with db.session_scope() as session:
        candidate = publishing.build_biblio_candidate(
            session,
            workspace_id,
            item_id,
            source_listing_id=listing_id,
            enrich_isbn=False,
        )
        assert candidate["fields"]["book_id"] is None
        assert "unique BIBLIO Book ID" in candidate["missing"]
        assert candidate["book_id_suggestion"].startswith("BK-0001-")

        candidate = publishing.apply_biblio_overrides(
            candidate,
            {"book_id": candidate["book_id_suggestion"]},
        )
        candidate = publishing.validate_biblio_candidate(
            session,
            workspace_id,
            item_id,
            candidate,
        )
        assert candidate["ready"] is True
        repaired = candidate["fields"]["book_id"]

        workspace = session.get(models.Workspace, workspace_id)
        listing = publishing.upsert_biblio_listing(
            session,
            workspace,
            item_id,
            candidate,
        )
        assert listing.external_id == repaired
        assert listing.external_sku == repaired


def test_listing_api_exposes_biblio_gate_instead_of_hiding_vinted_actions(monkeypatch):
    client, _csrf = _registered_client(monkeypatch)
    with db.session_scope() as session:
        membership = session.execute(select(models.Membership)).scalar_one()
        workspace_id = membership.workspace_id

        linked_general = models.InventoryItem(
            workspace_id=workspace_id,
            sku="GENERAL-VINTED",
            title="Sparse vintage book",
            category=ItemCategory.GENERAL,
            quantity=1,
            currency="EUR",
            attributes={},
        )
        session.add(linked_general)
        session.flush()
        session.add(
            models.ChannelListing(
                workspace_id=workspace_id,
                inventory_item_id=linked_general.id,
                channel=Channel.VINTED,
                external_id="SPARSE-1",
                title="Sparse vintage book",
                price_cents=400,
                currency="EUR",
                status=ListingStatus.ACTIVE,
                quantity=1,
                extra={"metadata": {}},
            )
        )

        unlinked = models.ChannelListing(
            workspace_id=workspace_id,
            inventory_item_id=None,
            channel=Channel.VINTED,
            external_id="UNLINKED-1",
            title="Unlinked book",
            price_cents=300,
            currency="EUR",
            status=ListingStatus.ACTIVE,
            quantity=1,
            extra={"metadata": {"author": "Unknown"}},
        )
        session.add(unlinked)

    rows = {
        row["external_id"]: row
        for row in client.get("/api/app/listings").json()["listings"]
    }
    assert rows["SPARSE-1"]["biblio_gate"] == "book_review"
    assert rows["UNLINKED-1"]["biblio_gate"] == "link_required"



def test_biblio_isbn_metadata_overrides_marketing_heavy_vinted_title(monkeypatch):
    workspace_id = _workspace()
    with db.session_scope() as session:
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="VINTED-ISBN-CLEAN",
            title="Hunter S. Thompson - The Great Shark Hunt - First Edition First Printing 1979",
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
            external_id="10253402699",
            title="Hunter S. Thompson - The Great Shark Hunt - First Edition First Printing 1979",
            price_cents=10900,
            currency="EUR",
            status=ListingStatus.ACTIVE,
            quantity=1,
            extra={
                "metadata": {
                    "category": "Books > Fiction",
                    "author": "Hunter S. Thompson",
                    "isbn": "9780000000002",
                    "description": "Vinted condition description should be kept exactly as the listing source.",
                },
                "image_urls": [
                    "https://images1.vinted.net/t/a.jpg",
                    "https://images1.vinted.net/t/b.jpg",
                ],
            },
        )
        session.add(source)
        session.flush()
        item_id = item.id
        source_id = source.id

    monkeypatch.setattr(
        "app.publishing.stock_intake.lookup_isbn",
        lambda isbn: {
            "found": True,
            "isbn": isbn,
            "title": "The Great Shark Hunt",
            "subtitle": "Strange Tales from a Strange Time",
            "author": "Hunter S. Thompson",
            "publisher": "Summit Books",
            "edition": "First edition",
            "publish_date": "1979",
        },
    )

    with db.session_scope() as session:
        candidate = publishing.build_biblio_candidate(
            session,
            workspace_id,
            item_id,
            source_listing_id=source_id,
            enrich_isbn=True,
        )

    assert candidate["fields"]["title"] == (
        "The Great Shark Hunt: Strange Tales from a Strange Time"
    )
    assert candidate["field_sources"]["title"] == "isbn"
    assert candidate["fields"]["author"] == "Hunter S. Thompson"
    assert candidate["field_sources"]["author"] == "isbn"
    assert candidate["fields"]["description"] == (
        "Vinted condition description should be kept exactly as the listing source."
    )
    assert candidate["field_sources"]["description"] == "vinted"
    assert candidate["source"]["photo_count"] == 2
    assert len(candidate["source"]["image_urls"]) == 2


def test_vinted_category_auto_classifies_books_clothing_and_electronics():
    workspace_id = _workspace()
    record_workspace_snapshot(
        workspace_id,
        {
            "collected_at": 1_799_100_000,
            "current_user": {},
            "notifications": [],
            "orders": [],
            "listings": [
                {
                    "id": "CAT-BOOK",
                    "title": "Book",
                    "price_cents": 500,
                    "currency": "EUR",
                    "status": "active",
                    "metadata": {"category": "Books > Fiction"},
                },
                {
                    "id": "CAT-CLOTHING",
                    "title": "Jacket",
                    "price_cents": 1500,
                    "currency": "EUR",
                    "status": "active",
                    "metadata": {"category": "Men > Clothing > Jackets"},
                },
                {
                    "id": "CAT-ELECTRONICS",
                    "title": "Phone",
                    "price_cents": 9000,
                    "currency": "EUR",
                    "status": "active",
                    "metadata": {"category": "Electronics > Phones"},
                },
            ],
        },
        extension_version="3.2.0",
    )

    with db.session_scope() as session:
        rows = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.external_id.in_(
                    ["CAT-BOOK", "CAT-CLOTHING", "CAT-ELECTRONICS"]
                ),
            )
        ).scalars().all()
        categories = {
            row.external_id: session.get(
                models.InventoryItem, row.inventory_item_id
            ).category
            for row in rows
        }

    assert categories["CAT-BOOK"] == ItemCategory.BOOK
    assert categories["CAT-CLOTHING"] == ItemCategory.CLOTHING
    assert categories["CAT-ELECTRONICS"] == ItemCategory.ELECTRONICS



def test_vinted_book_category_alone_avoids_manual_book_confirmation():
    workspace_id = _workspace()
    with db.session_scope() as session:
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="CATEGORY-ONLY-BOOK",
            title="Vintage novel listing title",
            category=ItemCategory.GENERAL,
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
            external_id="CATEGORY-ONLY-1",
            title="Vintage novel listing title",
            price_cents=500,
            currency="EUR",
            status=ListingStatus.ACTIVE,
            quantity=1,
            extra={
                "metadata": {
                    "category": "Fiction",
                    "description": "Vinted description",
                }
            },
        )
        session.add(source)
        session.flush()
        item_id = item.id
        source_id = source.id

    with db.session_scope() as session:
        candidate = publishing.build_biblio_candidate(
            session,
            workspace_id,
            item_id,
            source_listing_id=source_id,
            enrich_isbn=False,
        )

    assert candidate["category"] == ItemCategory.GENERAL
    assert "author" in candidate["missing"]
    assert candidate["fields"]["description"] == "Vinted description"



def test_vinted_taxonomy_prefers_specific_phrases_and_avoids_substring_false_positives():
    assert classify_vinted_category("Entertainment > Video games") == ItemCategory.MEDIA
    assert classify_vinted_category("Toys > Board games") == ItemCategory.TOYS_GAMES
    assert classify_vinted_category("Women > Party dresses") == ItemCategory.CLOTHING
    assert classify_vinted_category("Sports > Martial arts") == ItemCategory.SPORTS
    assert classify_vinted_category("Beauty > Accessories") == ItemCategory.BEAUTY
    assert classify_vinted_category("Electronics > Accessories") == ItemCategory.ELECTRONICS
    assert classify_vinted_category("Art") == ItemCategory.ART_CRAFTS


def test_biblio_publish_persists_isbn_enrichment_on_master_item(monkeypatch):
    workspace_id = _workspace()
    item_id, listing_id = _source_book(workspace_id)
    monkeypatch.setattr(
        "app.publishing.stock_intake.lookup_isbn",
        lambda isbn: {
            "found": True,
            "isbn": isbn,
            "title": "The Trial",
            "author": "Franz Kafka",
            "publisher": "Penguin Classics",
            "edition": "Revised edition",
            "publish_date": "2000",
        },
    )

    with db.session_scope() as session:
        workspace = session.get(models.Workspace, workspace_id)
        candidate = publishing.build_biblio_candidate(
            session,
            workspace_id,
            item_id,
            source_listing_id=listing_id,
            enrich_isbn=True,
        )
        assert candidate["bibliographic_enrichment"] == {
            "publisher": "Penguin Classics",
            "edition": "Revised edition",
            "publish_date": "2000",
        }
        publishing.upsert_biblio_listing(
            session,
            workspace,
            item_id,
            candidate,
        )

    with db.session_scope() as session:
        item = session.get(models.InventoryItem, item_id)
        assert item.attributes["publisher"] == "Penguin Classics"
        assert item.attributes["publisher_source"] == "isbn"
        assert item.attributes["edition"] == "Revised edition"
        assert item.attributes["edition_source"] == "isbn"
        assert item.attributes["publish_date"] == "2000"
        assert item.attributes["publish_date_source"] == "isbn"


def test_biblio_publish_uses_values_reviewed_in_preview_even_if_lookup_changes(monkeypatch):
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
            sku="REVIEW-STABLE-1",
            title="Marketing title",
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
            external_id="REVIEW-STABLE-V",
            title="Marketing title",
            price_cents=1200,
            currency="EUR",
            status=ListingStatus.ACTIVE,
            quantity=1,
            extra={
                "metadata": {
                    "author": "Vinted Author",
                    "description": "Vinted description",
                    "isbn": "9780140328721",
                }
            },
        )
        session.add(source)
        session.flush()
        item_id = item.id
        source_id = source.id

    calls = iter([
        {
            "found": True,
            "isbn": "9780140328721",
            "title": "Preview Title",
            "author": "Preview Author",
            "publisher": "Preview Publisher",
        },
        {
            "found": True,
            "isbn": "9780140328721",
            "title": "Changed Title",
            "author": "Changed Author",
            "publisher": "Changed Publisher",
        },
    ])
    monkeypatch.setattr(
        "app.publishing.stock_intake.lookup_isbn",
        lambda _isbn: next(calls),
    )

    preview = client.get(
        f"/api/app/inventory/{item_id}/publish/biblio",
        params={"source_listing_id": str(source_id)},
    )
    assert preview.status_code == 200, preview.text
    reviewed = preview.json()["fields"]
    assert reviewed["title"] == "Preview Title"
    assert reviewed["author"] == "Preview Author"

    published = client.post(
        f"/api/app/inventory/{item_id}/publish/biblio",
        headers={"X-CSRF-Token": csrf},
        json={
            "source_listing_id": str(source_id),
            "book_id": reviewed["book_id"],
            "title": reviewed["title"],
            "author": reviewed["author"],
            "description": reviewed["description"],
            "isbn": reviewed["isbn"],
            "publisher": preview.json()["bibliographic_enrichment"]["publisher"],
            "price_cents": reviewed["price_cents"],
        },
    )
    assert published.status_code == 200, published.text

    with db.session_scope() as session:
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.channel == Channel.BIBLIO,
            )
        ).scalar_one()
        assert listing.title == "Preview Title"
        assert listing.extra["author"] == "Preview Author"
        assert listing.extra["description"] == "Vinted description"
        item = session.get(models.InventoryItem, item_id)
        assert item.attributes["publisher"] == "Preview Publisher"



def test_biblio_review_can_clear_optional_isbn_and_enrichment():
    candidate = {
        "fields": {
            "sku": "BK-CLEAR",
            "book_id": "BK-CLEAR",
            "title": "Book",
            "author": "Author",
            "description": "Description",
            "isbn": "9780140328721",
            "price_cents": 500,
            "currency": "EUR",
            "quantity": 1,
        },
        "field_sources": {"isbn": "isbn"},
        "bibliographic_enrichment": {
            "publisher": "Publisher",
            "edition": "Edition",
            "publish_date": "2000",
        },
        "missing": [],
        "ready": True,
    }
    result = publishing.apply_biblio_overrides(
        candidate,
        {
            "isbn": "",
            "publisher": "",
            "edition": "",
            "publish_date": "",
        },
    )
    assert result["fields"]["isbn"] is None
    assert result["field_sources"]["isbn"] == "review"
    assert result["bibliographic_enrichment"] == {
        "publisher": None,
        "edition": None,
        "publish_date": None,
    }
    assert result["ready"] is True
