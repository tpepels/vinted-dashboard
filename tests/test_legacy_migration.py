"""Builds a synthetic legacy sqlite3 database covering all nine legacy
tables (``app.channels`` + ``app.intelligence`` schemas) and verifies
``app.legacy_migration`` maps every one of them into the new ORM schema
correctly, merges inventory conservatively, and is safe to re-run.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app import db, legacy_migration, models


def _build_legacy_db(path: Path) -> float:
    """Returns the ``now`` timestamp used to seed the fixture data, so tests
    can reason about relative ordering."""

    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE channel_items (
            source TEXT NOT NULL, source_id TEXT NOT NULL, sku TEXT, isbn TEXT, title TEXT NOT NULL,
            author TEXT, description TEXT, status TEXT NOT NULL, quantity INTEGER, price_cents INTEGER,
            currency TEXT, url TEXT, first_seen_at REAL NOT NULL, last_seen_at REAL NOT NULL, raw_json TEXT,
            PRIMARY KEY(source, source_id)
        );
        CREATE TABLE channel_sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, synced_at REAL NOT NULL,
            item_count INTEGER NOT NULL, active_count INTEGER NOT NULL, note TEXT
        );
        CREATE TABLE biblio_ftp_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, attempted_at REAL NOT NULL, action TEXT NOT NULL,
            status TEXT NOT NULL, inventory_filename TEXT, deletes_filename TEXT,
            active_count INTEGER NOT NULL DEFAULT 0, delete_count INTEGER NOT NULL DEFAULT 0, detail TEXT
        );
        CREATE TABLE sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, collected_at REAL NOT NULL UNIQUE, created_at REAL NOT NULL
        );
        CREATE TABLE listing_observations (
            sync_id INTEGER NOT NULL REFERENCES sync_runs(id) ON DELETE CASCADE, listing_id TEXT NOT NULL,
            title TEXT NOT NULL, status TEXT, price_cents INTEGER, currency TEXT, favourites INTEGER,
            views INTEGER, listed_at TEXT, vinted_url TEXT, PRIMARY KEY (sync_id, listing_id)
        );
        CREATE TABLE profile_observations (
            sync_id INTEGER PRIMARY KEY REFERENCES sync_runs(id) ON DELETE CASCADE,
            followers INTEGER, following INTEGER
        );
        CREATE TABLE favorite_events (
            notification_id TEXT PRIMARY KEY, item_id TEXT, item_title TEXT, actor TEXT,
            occurred_at TEXT, first_seen_at REAL NOT NULL
        );
        CREATE TABLE orders_history (
            order_key TEXT PRIMARY KEY, direction TEXT NOT NULL, title TEXT NOT NULL, counterparty TEXT,
            total_cents INTEGER, currency TEXT, status TEXT, lifecycle_status TEXT,
            is_closed INTEGER NOT NULL DEFAULT 0, updated_at TEXT, vinted_url TEXT,
            first_seen_at REAL NOT NULL, last_seen_at REAL NOT NULL
        );
        CREATE TABLE market_research (
            id INTEGER PRIMARY KEY AUTOINCREMENT, listing_id TEXT NOT NULL, query TEXT NOT NULL,
            requested_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'queued', completed_at REAL,
            sample_count INTEGER, min_cents INTEGER, median_cents INTEGER, max_cents INTEGER,
            currency TEXT, results_json TEXT, error TEXT
        );
        """
    )

    now = time.time()

    # Vinted: one active standalone listing, one inactive standalone listing.
    conn.execute(
        "INSERT INTO channel_items VALUES ('vinted','V1',NULL,NULL,'Vinted Jacket',NULL,NULL,'active',1,2500,"
        "'EUR','https://vinted.example/v1',?,?,'{}')",
        (now - 1000, now),
    )
    conn.execute(
        "INSERT INTO channel_items VALUES ('vinted','V2',NULL,NULL,'Old Vinted Shoes',NULL,NULL,'inactive',0,1500,"
        "'EUR','https://vinted.example/v2',?,?,'{}')",
        (now - 5000, now - 4000),
    )
    # BIBLIO (has sku + isbn) and eBay (isbn only, no sku) for the same
    # physical book: must merge into a single InventoryItem via the shared
    # ISBN, even though only the BIBLIO row carries a SKU.
    conn.execute(
        "INSERT INTO channel_items VALUES ('biblio','B1','BK-100','9780306406157','Shared Book',NULL,'desc',"
        "'active',3,999,'USD','https://biblio.example/b1',?,?,'{}')",
        (now - 2000, now),
    )
    conn.execute(
        "INSERT INTO channel_items VALUES ('ebay','E1',NULL,'9780306406157','Shared Book (ebay copy)',NULL,NULL,"
        "'active',1,1099,'USD','https://ebay.example/e1',?,?,'{}')",
        (now - 1500, now),
    )
    # A second, unrelated BIBLIO book (no shared identifier with anything else).
    conn.execute(
        "INSERT INTO channel_items VALUES ('biblio','B2','BK-200',NULL,'Another Book','Jane Author',NULL,'active',"
        "2,1299,'USD','https://biblio.example/b2',?,?,'{}')",
        (now - 1800, now),
    )

    conn.execute(
        "INSERT INTO channel_sync_runs(source, synced_at, item_count, active_count, note) "
        "VALUES ('biblio', ?, 2, 2, NULL)",
        (now,),
    )
    conn.execute(
        "INSERT INTO channel_sync_runs(source, synced_at, item_count, active_count, note) "
        "VALUES ('ebay', ?, 1, 1, 'manual run')",
        (now,),
    )
    conn.execute(
        "INSERT INTO biblio_ftp_runs(attempted_at, action, status, inventory_filename, deletes_filename, "
        "active_count, delete_count, detail) VALUES (?, 'sync', 'success', 'inv.csv', 'del.csv', 2, 0, NULL)",
        (now,),
    )
    conn.execute(
        "INSERT INTO biblio_ftp_runs(attempted_at, action, status, inventory_filename, deletes_filename, "
        "active_count, delete_count, detail) VALUES (?, 'test', 'error', NULL, NULL, 0, 0, 'connection refused')",
        (now - 100,),
    )

    conn.execute("INSERT INTO sync_runs(collected_at, created_at) VALUES (?, ?)", (now, now))
    sync_id = conn.execute("SELECT id FROM sync_runs WHERE collected_at=?", (now,)).fetchone()[0]
    conn.execute(
        "INSERT INTO listing_observations VALUES (?, 'V1', 'Vinted Jacket', 'active', 2500, 'EUR', 3, 40, "
        "'2024-01-01T00:00:00Z', 'https://vinted.example/v1')",
        (sync_id,),
    )
    conn.execute("INSERT INTO profile_observations VALUES (?, 120, 80)", (sync_id,))

    conn.execute(
        "INSERT INTO favorite_events VALUES ('N1', 'V1', 'Vinted Jacket', 'someone', '2024-01-02T00:00:00Z', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO favorite_events VALUES ('N2', NULL, 'Mystery item', 'someone-else', NULL, ?)",
        (now,),
    )

    conn.execute(
        "INSERT INTO orders_history VALUES ('sell:ORDER1', 'sell', 'Vinted Jacket', 'buyer1', 2500, 'EUR', 'paid', "
        "'completed', 1, '2024-01-03T00:00:00Z', 'https://vinted.example/o1', ?, ?)",
        (now - 500, now),
    )
    conn.execute(
        "INSERT INTO orders_history VALUES ('buy:ORDER2', 'buy', 'Something I bought', 'seller1', 1000, 'EUR', "
        "'shipped', 'in_progress', 0, '2024-01-04T00:00:00Z', 'https://vinted.example/o2', ?, ?)",
        (now - 300, now),
    )

    conn.execute(
        "INSERT INTO market_research(listing_id, query, requested_at, status, completed_at, sample_count, "
        "min_cents, median_cents, max_cents, currency, results_json, error) "
        "VALUES ('V1', 'Vinted Jacket', ?, 'completed', ?, 2, 2000, 2200, 2400, 'EUR', ?, NULL)",
        (now, now, '[{"id": "x", "title": "Vinted Jacket", "price_cents": 2200, "currency": "EUR"}]'),
    )

    conn.commit()
    conn.close()
    return now


@pytest.fixture()
def legacy_db(tmp_path) -> Path:
    path = tmp_path / "vinted-history.sqlite3"
    _build_legacy_db(path)
    return path


@pytest.fixture()
def app_session_factory(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path / 'app.sqlite3'}")
    db.create_all()
    return db.session_scope


def _counts(session) -> dict[str, int]:
    return {
        cls.__tablename__: session.execute(select(func.count()).select_from(cls)).scalar_one()
        for cls in [
            models.Workspace,
            models.ChannelAccount,
            models.InventoryItem,
            models.ChannelListing,
            models.ConnectorSyncRun,
            models.ListingSnapshot,
            models.ProfileObservation,
            models.FavoriteEvent,
            models.Sale,
            models.MarketResearch,
            models.LegacyBackfillRun,
        ]
    }


def test_backfill_maps_every_legacy_table(app_session_factory, legacy_db):
    with app_session_factory() as session:
        summary = legacy_migration.run_legacy_backfill(session, legacy_path=legacy_db)

    assert summary is not None
    assert summary.channel_accounts == 3  # vinted, biblio, ebay
    assert summary.inventory_items == 4  # V1, V2, (B1+E1 merged), B2
    assert summary.channel_listings == 5
    assert summary.connector_sync_runs == 5
    assert summary.listing_snapshots == 1
    assert summary.profile_observations == 1
    assert summary.favorite_events == 2
    assert summary.sales == 2
    assert summary.market_research == 1


def test_backfill_merges_items_sharing_isbn_even_without_shared_sku(app_session_factory, legacy_db):
    """Regression test for the union-find merge logic: a BIBLIO row with
    both a SKU and an ISBN must link an eBay row that only has the ISBN into
    the *same* inventory item, not leave them as two separate items."""

    with app_session_factory() as session:
        legacy_migration.run_legacy_backfill(session, legacy_path=legacy_db)

        biblio_listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.channel == "biblio", models.ChannelListing.external_id == "B1"
            )
        ).scalar_one()
        ebay_listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.channel == "ebay", models.ChannelListing.external_id == "E1"
            )
        ).scalar_one()

        assert biblio_listing.inventory_item_id == ebay_listing.inventory_item_id
        assert biblio_listing.inventory_item.sku == "BK-100"
        assert biblio_listing.inventory_item.attributes.get("isbn") == "9780306406157"


def test_backfill_is_idempotent(app_session_factory, legacy_db):
    with app_session_factory() as session:
        legacy_migration.run_legacy_backfill(session, legacy_path=legacy_db)
        before = _counts(session)

    with app_session_factory() as session:
        summary = legacy_migration.run_legacy_backfill(session, legacy_path=legacy_db)
        after = _counts(session)

    # Every table except the backfill-run audit log is unchanged by a second,
    # no-op run against the same unmodified legacy data.
    before["legacy_backfill_runs"] = after["legacy_backfill_runs"] = 0
    assert before == after
    assert summary is not None
    assert summary.as_dict() == {key: 0 for key in summary.as_dict()}


def test_backfill_applies_incremental_changes_without_duplicating(app_session_factory, legacy_db):
    with app_session_factory() as session:
        legacy_migration.run_legacy_backfill(session, legacy_path=legacy_db)

    conn = sqlite3.connect(legacy_db)
    now = time.time()
    conn.execute(
        "UPDATE channel_items SET price_cents=2600, last_seen_at=? WHERE source='vinted' AND source_id='V1'",
        (now + 100,),
    )
    conn.execute(
        "INSERT INTO channel_items VALUES ('vinted','V3',NULL,NULL,'New Vinted Hat',NULL,NULL,'active',1,800,"
        "'EUR','https://vinted.example/v3',?,?,'{}')",
        (now + 100, now + 100),
    )
    conn.commit()
    conn.close()

    with app_session_factory() as session:
        summary = legacy_migration.run_legacy_backfill(session, legacy_path=legacy_db)
        assert summary is not None
        assert summary.inventory_items == 1
        assert summary.channel_listings == 1

        v1 = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.channel == "vinted", models.ChannelListing.external_id == "V1"
            )
        ).scalar_one()
        assert v1.price_cents == 2600


def test_backfill_without_legacy_file_is_a_noop(app_session_factory, tmp_path):
    with app_session_factory() as session:
        summary = legacy_migration.run_legacy_backfill(session, legacy_path=tmp_path / "missing.sqlite3")
    assert summary is None


def test_backfill_tolerates_partial_legacy_schema(app_session_factory, tmp_path):
    """A legacy database that only ever ran the Vinted sync (never BIBLIO or
    eBay) only has ``app.channels``'s tables, not ``app.intelligence``'s -
    the backfill must not assume every legacy table exists."""

    path = tmp_path / "partial.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE channel_items (
            source TEXT NOT NULL, source_id TEXT NOT NULL, sku TEXT, isbn TEXT, title TEXT NOT NULL,
            author TEXT, description TEXT, status TEXT NOT NULL, quantity INTEGER, price_cents INTEGER,
            currency TEXT, url TEXT, first_seen_at REAL NOT NULL, last_seen_at REAL NOT NULL, raw_json TEXT,
            PRIMARY KEY(source, source_id)
        );
        """
    )
    now = time.time()
    conn.execute(
        "INSERT INTO channel_items VALUES ('vinted','V1',NULL,NULL,'Only Vinted Item',NULL,NULL,'active',1,1000,"
        "'EUR','https://vinted.example/v1',?,?,'{}')",
        (now, now),
    )
    conn.commit()
    conn.close()

    with app_session_factory() as session:
        summary = legacy_migration.run_legacy_backfill(session, legacy_path=path)

    assert summary is not None
    assert summary.channel_listings == 1
    assert summary.connector_sync_runs == 0
