from datetime import datetime, timedelta, timezone
import uuid

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
