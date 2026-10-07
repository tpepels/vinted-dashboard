from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import select

from app import db, models
from app.constants import Channel, ListingStatus, SyncRunStatus
from app.connectors import hosted
from app.connectors.biblio_format import parse_biblio_inventory


def test_parse_biblio_inventory_tab_delimited():
    text = (
        "Book ID\tAuthor\tTitle\tDescription\tPrice\tISBN\tStatus\tQuantity\n"
        "ABC-1\tWilla Cather\tA Lost Lady\tPaperback, good condition\t9.50\t9780000000001\tFor sale\t1\n"
        "ABC-2\tJohn Williams\tStoner\tPaperback\t12,00\t9780000000002\tSold\t0\n"
    )

    rows = parse_biblio_inventory(text, currency="EUR")

    assert len(rows) == 2
    assert rows[0]["source_id"] == "ABC-1"
    assert rows[0]["price_cents"] == 950
    assert rows[0]["description"] == "Paperback, good condition"
    assert "title" in rows[0]["_source_fields"]
    assert "language" not in rows[0]["_source_fields"]
    assert rows[0]["status"] == "active"
    assert rows[1]["price_cents"] == 1200
    assert rows[1]["status"] == "sold"


def test_parse_biblio_inventory_requires_identity_columns():
    try:
        parse_biblio_inventory("Author\tPrice\nSomeone\t5.00\n")
        assert False, "expected required-column validation"
    except ValueError as exc:
        assert "sku" in str(exc)
        assert "title" in str(exc)



def test_imported_biblio_snapshot_is_baselined_not_reuploaded():
    with db.session_scope() as session:
        workspace = models.Workspace(name="Biblio", slug="biblio-import")
        session.add(workspace)
        session.flush()
        workspace_id = workspace.id

    rows = parse_biblio_inventory(
        "Book ID\tAuthor\tTitle\tDescription\tPrice\tISBN\tStatus\tQuantity\n"
        "REMOTE-1\tAuthor\tBook\tDescription\t9.50\t9780000000001\tFor sale\t1\n",
        currency="EUR",
    )
    hosted.import_biblio_workspace(workspace_id, rows, filename="remote.txt")

    active, deletes = hosted._biblio_rows(workspace_id)
    assert len(active) == 1
    assert deletes == []
    assert active[0]["sku"] == "REMOTE-1"
    assert active[0]["inventory_dirty"] is False
    assert active[0]["inventory_sync_signature"] == active[0]["inventory_signature"]


def test_targeted_success_run_is_not_used_as_legacy_catalogue_baseline():
    now = datetime.now(timezone.utc)
    with db.session_scope() as session:
        workspace = models.Workspace(name="Biblio", slug="biblio-targeted-baseline")
        session.add(workspace)
        session.flush()
        item = models.InventoryItem(
            workspace_id=workspace.id,
            sku="LOCAL-1",
            title="Book",
            category="book",
            quantity=1,
            currency="EUR",
            attributes={"author": "Author", "description": "Description"},
        )
        session.add(item)
        session.flush()
        listing = models.ChannelListing(
            workspace_id=workspace.id,
            inventory_item_id=item.id,
            channel=Channel.BIBLIO,
            external_id="REMOTE-1",
            external_sku="LOCAL-1",
            title="Book",
            price_cents=950,
            currency="EUR",
            status=ListingStatus.ACTIVE,
            quantity=1,
            first_seen_at=now - timedelta(days=2),
            last_seen_at=now - timedelta(days=2),
            extra={"author": "Author", "description": "Description"},
        )
        session.add(listing)
        session.add(
            models.ConnectorSyncRun(
                workspace_id=workspace.id,
                channel=Channel.BIBLIO,
                run_type="ftp_sync",
                status=SyncRunStatus.SUCCESS,
                started_at=now - timedelta(hours=1),
                completed_at=now - timedelta(minutes=59),
                detail={"mode": "listing", "listing_id": str(uuid.uuid4())},
            )
        )
        workspace_id = workspace.id

    active, _deletes = hosted._biblio_rows(workspace_id)
    assert len(active) == 1
    assert active[0]["sku"] == "REMOTE-1"
    assert active[0]["inventory_dirty"] is True



def test_parse_biblio_inventory_preserves_optional_bibliographic_fields():
    rows = parse_biblio_inventory(
        "Book ID\tAuthor\tTitle\tSubtitle\tDescription\tPrice\tISBN\tPublisher\tEdition\t"
        "Binding\tLanguage\tPublication Date\tPublication Year\tPages\tCondition\tPublication Place\t"
        "First Edition\tSigned\tDJ Present\tDJ Condition\tDJ Description\tIllustrator\tKeywords\t"
        "Catalog 1\tCatalog 8\tStatus\tQuantity\n"
        "ABC-9\tAuthor\tBook\tA subtitle\tDescription\t9.50\t9780140328721\tPuffin\tRevised\t"
        "Paperback\tEnglish\t1988-01-01\t1988\t176\tVery good\tLondon\t"
        "Yes\tNo\tY\tGood\tLight wear\tArtist\tchildren,classic\tKids\tFeatured\tFor sale\t1\n",
        currency="EUR",
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["isbn"] == "9780140328721"
    assert row["subtitle"] == "A subtitle"
    assert "subtitle" in row["_source_fields"]
    assert row["publisher"] == "Puffin"
    assert row["edition"] == "Revised"
    assert row["binding"] == "Paperback"
    assert row["language"] == "English"
    assert row["publish_date"] == "1988-01-01"
    assert row["publication_year"] == 1988
    assert row["pages"] == 176
    assert row["condition"] == "Very good"
    assert row["publication_place"] == "London"
    assert row["first_edition"] is True
    assert row["signed"] is False
    assert row["dust_jacket_present"] is True
    assert row["dust_jacket_condition"] == "Good"
    assert row["dust_jacket_description"] == "Light wear"
    assert row["illustrator"] == "Artist"
    assert row["keywords"] == "children,classic"
    assert row["catalog_1"] == "Kids"
    assert row["catalog_8"] == "Featured"



def test_biblio_verification_is_read_only_and_records_mismatch():
    with db.session_scope() as session:
        workspace = models.Workspace(name="Verify", slug="biblio-verify")
        session.add(workspace)
        session.flush()
        item = models.InventoryItem(
            workspace_id=workspace.id,
            sku="VERIFY-1",
            title="Local title",
            category="book",
            quantity=1,
            status="active",
            currency="EUR",
            attributes={"author": "Author", "description": "Local description"},
        )
        session.add(item)
        session.flush()
        listing = models.ChannelListing(
            workspace_id=workspace.id,
            inventory_item_id=item.id,
            channel=Channel.BIBLIO,
            external_id="VERIFY-1",
            external_sku="VERIFY-1",
            title="Local title",
            price_cents=1000,
            currency="EUR",
            status=ListingStatus.ACTIVE,
            quantity=1,
            extra={"author": "Author", "description": "Local description"},
        )
        session.add(listing)
        session.flush()
        workspace_id = workspace.id
        listing_id = listing.id

    rows = parse_biblio_inventory(
        "Book ID\tAuthor\tTitle\tDescription\tPrice\tStatus\tQuantity\n"
        "VERIFY-1\tAuthor\tRemote title\tLocal description\t10.00\tFor sale\t1\n"
    )
    result = hosted.verify_biblio_workspace(workspace_id, rows, filename="download.txt")

    assert result["matched"] == 1
    assert result["mismatched"] == 1
    assert result["mismatch_samples"][0]["fields"] == ["title"]
    with db.session_scope() as session:
        listing = session.get(models.ChannelListing, listing_id)
        item = session.get(models.InventoryItem, listing.inventory_item_id)
        assert listing.title == "Local title"
        assert item.title == "Local title"
        assert listing.extra["remote_verified"] is True
        assert listing.extra["remote_matches_local"] is False
        assert listing.extra["remote_mismatch_fields"] == ["title"]

    with db.session_scope() as session:
        listing = session.get(models.ChannelListing, listing_id)
        listing.title = "Changed again locally"

    active, _deletes = hosted._biblio_rows(workspace_id)
    hosted._mark_biblio_inventory_sync(workspace_id, active)
    with db.session_scope() as session:
        listing = session.get(models.ChannelListing, listing_id)
        assert listing.extra["remote_verified"] is False
        assert listing.extra["remote_verification_stale"] is True
        assert listing.extra["remote_stale_since"]


def test_biblio_merge_does_not_deactivate_omitted_local_listing():
    with db.session_scope() as session:
        workspace = models.Workspace(name="Merge", slug="biblio-merge")
        session.add(workspace)
        session.flush()
        workspace_id = workspace.id
        for sku in ("KEEP-1", "OMITTED-1"):
            item = models.InventoryItem(
                workspace_id=workspace.id,
                sku=sku,
                title=sku,
                category="book",
                quantity=1,
                status="active",
                currency="EUR",
                attributes={"author": "Author", "description": "Description"},
            )
            session.add(item)
            session.flush()
            session.add(models.ChannelListing(
                workspace_id=workspace.id,
                inventory_item_id=item.id,
                channel=Channel.BIBLIO,
                external_id=sku,
                external_sku=sku,
                title=sku,
                price_cents=1000,
                currency="EUR",
                status=ListingStatus.ACTIVE,
                quantity=1,
                extra={"author": "Author", "description": "Description"},
            ))

    rows = parse_biblio_inventory(
        "Book ID\tAuthor\tTitle\tDescription\tPrice\tStatus\tQuantity\n"
        "KEEP-1\tAuthor\tKEEP-1\tDescription\t10.00\tFor sale\t1\n"
    )
    hosted.import_biblio_workspace(workspace_id, rows, filename="partial.txt", authoritative=False)

    with db.session_scope() as session:
        omitted = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.external_id == "OMITTED-1",
            )
        ).scalar_one()
        assert omitted.status == ListingStatus.ACTIVE


def test_biblio_authoritative_import_deactivates_missing_local_listing():
    with db.session_scope() as session:
        workspace = models.Workspace(name="Authoritative", slug="biblio-authoritative")
        session.add(workspace)
        session.flush()
        workspace_id = workspace.id
        for sku in ("KEEP-2", "MISSING-2"):
            item = models.InventoryItem(
                workspace_id=workspace.id,
                sku=sku,
                title=sku,
                category="book",
                quantity=1,
                status="active",
                currency="EUR",
                attributes={"author": "Author", "description": "Description"},
            )
            session.add(item)
            session.flush()
            session.add(models.ChannelListing(
                workspace_id=workspace.id,
                inventory_item_id=item.id,
                channel=Channel.BIBLIO,
                external_id=sku,
                external_sku=sku,
                title=sku,
                price_cents=1000,
                currency="EUR",
                status=ListingStatus.ACTIVE,
                quantity=1,
                extra={"author": "Author", "description": "Description"},
            ))

    rows = parse_biblio_inventory(
        "Book ID\tAuthor\tTitle\tDescription\tPrice\tStatus\tQuantity\n"
        "KEEP-2\tAuthor\tKEEP-2\tDescription\t10.00\tFor sale\t1\n"
    )
    result = hosted.import_biblio_workspace(
        workspace_id,
        rows,
        filename="complete.txt",
        authoritative=True,
    )
    assert result["missing_local"] == 1

    with db.session_scope() as session:
        missing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.external_id == "MISSING-2",
            )
        ).scalar_one()
        assert missing.status == ListingStatus.INACTIVE
        assert missing.extra["remote_verified"] is False
        assert missing.extra["remote_missing_at"]



def test_biblio_pending_changes_reports_exact_incremental_work():
    with db.session_scope() as session:
        workspace = models.Workspace(name="Pending", slug="biblio-pending")
        session.add(workspace)
        session.flush()
        workspace_id = workspace.id

        active_item = models.InventoryItem(
            workspace_id=workspace.id,
            sku="ACTIVE-PENDING",
            title="Active pending",
            category="book",
            quantity=1,
            status="active",
            currency="EUR",
            attributes={"author": "Author", "description": "Description"},
        )
        sold_item = models.InventoryItem(
            workspace_id=workspace.id,
            sku="SOLD-PENDING",
            title="Sold pending",
            category="book",
            quantity=0,
            status="sold",
            currency="EUR",
            attributes={"author": "Author", "description": "Description"},
        )
        session.add_all([active_item, sold_item])
        session.flush()

        session.add(models.ChannelListing(
            workspace_id=workspace.id,
            inventory_item_id=active_item.id,
            channel=Channel.BIBLIO,
            external_id="ACTIVE-PENDING",
            external_sku="ACTIVE-PENDING",
            title="Active pending",
            price_cents=1000,
            currency="EUR",
            status=ListingStatus.ACTIVE,
            quantity=1,
            extra={"author": "Author", "description": "Description"},
        ))
        session.add(models.ChannelListing(
            workspace_id=workspace.id,
            inventory_item_id=sold_item.id,
            channel=Channel.BIBLIO,
            external_id="SOLD-PENDING",
            external_sku="SOLD-PENDING",
            title="Sold pending",
            price_cents=1000,
            currency="EUR",
            status=ListingStatus.SOLD,
            quantity=0,
            extra={"author": "Author", "description": "Description"},
        ))

    pending = hosted.biblio_pending_changes(workspace_id)
    assert pending == {"inventory": 1, "deletes": 1}

    active, deletes = hosted._biblio_rows(workspace_id)
    hosted._mark_biblio_inventory_sync(workspace_id, [*active, *deletes])

    assert hosted.biblio_pending_changes(workspace_id) == {
        "inventory": 0,
        "deletes": 0,
    }
