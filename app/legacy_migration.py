"""Backfill the workspace/inventory ORM schema from the legacy ``sqlite3``
database(s) used by :mod:`app.intelligence` and :mod:`app.channels`.

This script is purely additive: it reads the legacy tables (``channel_items``,
``channel_sync_runs``, ``biblio_ftp_runs``, ``sync_runs``,
``listing_observations``, ``profile_observations``, ``favorite_events``,
``orders_history``, ``market_research``) and copies their data into the
ORM-backed workspace schema (``DATABASE_URL``) under a bootstrap "personal"
workspace. Legacy files are treated as read-only migration input and are never
part of the runtime application.

Inventory matching is intentionally conservative: legacy ``channel_items``
rows are grouped into a single :class:`app.models.InventoryItem` only when
they share a normalized SKU or ISBN. Rows with neither (all Vinted listings
today) become standalone one-listing inventory items with a synthesized SKU,
rather than being guessed into a merge that might be wrong.

Every write here is an upsert keyed on the corresponding legacy table's
natural key (or, for pure log tables with no natural key, on
``(channel, run_type, started_at)`` / ``(listing, captured_at)``), so running
this repeatedly - e.g. once per container start - only ever applies
incremental changes and never duplicates rows.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db, models
from app.constants import (
    Channel,
    ItemCategory,
    ItemStatus,
    ListingStatus,
    SyncRunStatus,
)
from app.cross_channel import auto_link_unlinked_sales
from app.workspace_bootstrap import (
    BOOTSTRAP_OWNER_EMAIL,
    BOOTSTRAP_WORKSPACE_NAME,
    BOOTSTRAP_WORKSPACE_SLUG,
    clean_isbn,
    get_or_create_channel_account,
    get_or_create_owner,
    get_or_create_workspace,
    normalize_sku,
)

logger = logging.getLogger(__name__)

#: Falls back to the same env var the legacy code already reads, so a
#: deployment that never sets ``LEGACY_SQLITE_PATH`` explicitly still finds
#: its existing data.
DEFAULT_LEGACY_SQLITE_PATH = Path(
    os.getenv("LEGACY_SQLITE_PATH", os.getenv("VINTED_HISTORY_DB", "/app/data/vinted-history.sqlite3"))
)


@dataclass
class BackfillSummary:
    """Counts of newly-created rows, for logging/CLI output. Rows that were
    matched to something already imported (update-in-place) are not counted
    again, so re-running against unchanged legacy data reports all zeros."""

    channel_accounts: int = 0
    inventory_items: int = 0
    channel_listings: int = 0
    connector_sync_runs: int = 0
    listing_snapshots: int = 0
    profile_observations: int = 0
    favorite_events: int = 0
    sales: int = 0
    sales_auto_linked: int = 0
    market_research: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _epoch_to_dt(value: Any) -> Optional[datetime]:
    """Legacy epoch-seconds (``REAL``/float) -> aware UTC ``datetime``."""
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _parse_legacy_time(value: Any) -> float | None:
    """Parse the timestamp shapes written by pre-workspace releases."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return None


def _text_to_dt(value: Any) -> Optional[datetime]:
    """Legacy free-text timestamp (ISO string or numeric-as-string, as
    produced by ``app.vinted._timestamp``) -> aware UTC ``datetime``.

    """
    return _epoch_to_dt(_parse_legacy_time(value))


def _file_signature(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_size}:{int(stat.st_mtime)}"


def _legacy_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


# --------------------------------------------------------------------------
# channel_items -> InventoryItem + ChannelListing
# --------------------------------------------------------------------------


class _UnionFind:
    """Disjoint-set over ``(kind, value)`` identifier keys, used to group
    ``channel_items`` rows that share a SKU or an ISBN - even transitively,
    e.g. a BIBLIO row carrying both its own SKU and an ISBN links any other
    row matching *either* of those two values into the same item, without
    requiring every row to agree on which identifier it used."""

    def __init__(self) -> None:
        self._parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(self, key: tuple[str, str]) -> tuple[str, str]:
        self._parent.setdefault(key, key)
        root = key
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[key] != root:
            self._parent[key], key = root, self._parent[key]
        return root

    def union(self, a: tuple[str, str], b: tuple[str, str]) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self._parent[root_a] = root_b


def _row_keys(row: sqlite3.Row) -> list[tuple[str, str]]:
    keys = []
    sku = normalize_sku(row["sku"])
    if sku:
        keys.append(("sku", sku.upper()))
    isbn = clean_isbn(row["isbn"])
    if isbn:
        keys.append(("isbn", isbn))
    return keys


def _group_channel_items(
    rows: list[sqlite3.Row],
) -> tuple[list[list[sqlite3.Row]], list[sqlite3.Row]]:
    """Splits ``channel_items`` rows into merge groups (rows sharing a SKU
    and/or ISBN) and standalone rows (neither identifier present)."""

    union_find = _UnionFind()
    keyed_rows: list[tuple[sqlite3.Row, list[tuple[str, str]]]] = []
    standalone: list[sqlite3.Row] = []

    for row in rows:
        keys = _row_keys(row)
        if not keys:
            standalone.append(row)
            continue
        keyed_rows.append((row, keys))
        for other in keys[1:]:
            union_find.union(keys[0], other)

    groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row, keys in keyed_rows:
        root = union_find.find(keys[0])
        groups.setdefault(root, []).append(row)

    return list(groups.values()), standalone


def _summarize_group(rows: list[sqlite3.Row]) -> tuple[str, str, dict[str, Any]]:
    is_book = any(row["source"] == Channel.BIBLIO or row["author"] or row["isbn"] for row in rows)
    category = ItemCategory.BOOK if is_book else ItemCategory.GENERAL
    title = next((row["title"] for row in rows if row["title"]), "Untitled")
    attributes: dict[str, Any] = {}
    for row in rows:
        if row["author"] and "author" not in attributes:
            attributes["author"] = row["author"]
        isbn = clean_isbn(row["isbn"])
        if isbn and "isbn" not in attributes:
            attributes["isbn"] = isbn
    return category, title, attributes


def _group_quantity(rows: list[sqlite3.Row]) -> int:
    active = [int(row["quantity"] or 0) for row in rows if row["status"] == "active"]
    return max(active) if active else 0


def _group_status(rows: list[sqlite3.Row]) -> str:
    statuses = {row["status"] for row in rows}
    if "active" in statuses:
        return ItemStatus.ACTIVE
    if statuses and statuses <= {"sold"}:
        return ItemStatus.SOLD
    return ItemStatus.ARCHIVED


def _backfill_channel_items(
    session: Session,
    legacy_conn: sqlite3.Connection,
    workspace: models.Workspace,
    account_cache: dict[str, models.ChannelAccount],
    summary: BackfillSummary,
) -> dict[tuple[str, str], models.ChannelListing]:
    """Returns a ``(channel, external_id) -> ChannelListing`` index used by
    the Vinted-analytics backfill steps below to link observations/favorites/
    market research back to the listing they describe."""

    if not _table_exists(legacy_conn, "channel_items"):
        return {}

    rows = legacy_conn.execute(
        """
        SELECT source, source_id, sku, isbn, title, author, description, status,
               quantity, price_cents, currency, url, first_seen_at, last_seen_at
        FROM channel_items
        ORDER BY source, source_id
        """
    ).fetchall()

    groups, standalone = _group_channel_items(rows)

    listing_index: dict[tuple[str, str], models.ChannelListing] = {}
    existing_listings = {
        (listing.channel, listing.external_id): listing
        for listing in session.execute(
            select(models.ChannelListing).where(models.ChannelListing.workspace_id == workspace.id)
        ).scalars()
    }
    existing_items_by_sku = {
        item.sku.upper(): item
        for item in session.execute(
            select(models.InventoryItem).where(models.InventoryItem.workspace_id == workspace.id)
        ).scalars()
    }

    def _resolve_item(sku: str, rows_in_group: list[sqlite3.Row]) -> models.InventoryItem:
        category, title, attributes = _summarize_group(rows_in_group)
        quantity = _group_quantity(rows_in_group)
        status = _group_status(rows_in_group)
        existing = existing_items_by_sku.get(sku.upper())
        if existing is not None:
            existing.title = title
            existing.category = category
            existing.quantity = quantity
            existing.status = status
            existing.attributes = {**existing.attributes, **attributes}
            return existing
        item = models.InventoryItem(
            workspace_id=workspace.id,
            sku=sku,
            title=title,
            category=category,
            quantity=quantity,
            status=status,
            attributes=attributes,
        )
        session.add(item)
        session.flush()
        existing_items_by_sku[sku.upper()] = item
        summary.inventory_items += 1
        return item

    def _apply_listing(row: sqlite3.Row, item: models.InventoryItem) -> None:
        channel = row["source"]
        external_id = row["source_id"]
        account, created = get_or_create_channel_account(session, workspace, channel, account_cache)
        if created:
            summary.channel_accounts += 1
        first_seen = _epoch_to_dt(row["first_seen_at"]) or _utcnow()
        last_seen = _epoch_to_dt(row["last_seen_at"]) or _utcnow()
        extra: dict[str, Any] = {}
        if row["author"]:
            extra["author"] = row["author"]
        if row["description"]:
            extra["description"] = row["description"]
        isbn = clean_isbn(row["isbn"])
        if isbn:
            extra["isbn"] = isbn

        existing = existing_listings.get((channel, external_id))
        if existing is not None:
            existing.inventory_item_id = item.id
            existing.channel_account_id = account.id
            existing.external_sku = normalize_sku(row["sku"])
            existing.title = row["title"] or existing.title
            existing.price_cents = row["price_cents"]
            existing.currency = row["currency"]
            existing.status = row["status"] or existing.status
            existing.url = row["url"]
            existing.quantity = row["quantity"]
            existing.first_seen_at = min(existing.first_seen_at, first_seen)
            existing.last_seen_at = max(existing.last_seen_at, last_seen)
            existing.extra = {**existing.extra, **extra}
            listing_index[(channel, external_id)] = existing
            return

        listing = models.ChannelListing(
            workspace_id=workspace.id,
            inventory_item_id=item.id,
            channel_account_id=account.id,
            channel=channel,
            external_id=external_id,
            external_sku=normalize_sku(row["sku"]),
            title=row["title"] or "Untitled",
            price_cents=row["price_cents"],
            currency=row["currency"],
            status=row["status"] or ListingStatus.ACTIVE,
            url=row["url"],
            quantity=row["quantity"],
            first_seen_at=first_seen,
            last_seen_at=last_seen,
            extra=extra,
        )
        session.add(listing)
        session.flush()
        existing_listings[(channel, external_id)] = listing
        listing_index[(channel, external_id)] = listing
        summary.channel_listings += 1

    for group_rows in groups:
        # Prefer any row's real SKU (original casing) for the merged item's
        # canonical identifier; fall back to a synthesized ISBN-based SKU
        # when the group was only linked via ISBN (no row has a real SKU).
        sku = next(
            (normalize_sku(row["sku"]) for row in group_rows if normalize_sku(row["sku"])), None
        )
        if sku is None:
            isbn = next(
                (clean_isbn(row["isbn"]) for row in group_rows if clean_isbn(row["isbn"])), None
            )
            sku = f"ISBN-{isbn}" if isbn else f"{group_rows[0]['source'].upper()}-{group_rows[0]['source_id']}"
        item = _resolve_item(sku, group_rows)
        for row in group_rows:
            _apply_listing(row, item)

    for row in standalone:
        sku = f"{str(row['source']).upper()}-{row['source_id']}"
        item = _resolve_item(sku, [row])
        _apply_listing(row, item)

    return listing_index


# --------------------------------------------------------------------------
# channel_sync_runs / biblio_ftp_runs / sync_runs -> ConnectorSyncRun
# --------------------------------------------------------------------------


def _preload_sync_run_keys(session: Session, workspace: models.Workspace) -> set[tuple[str, str, datetime]]:
    return set(
        session.execute(
            select(
                models.ConnectorSyncRun.channel,
                models.ConnectorSyncRun.run_type,
                models.ConnectorSyncRun.started_at,
            ).where(models.ConnectorSyncRun.workspace_id == workspace.id)
        ).all()
    )


def _backfill_channel_sync_runs(
    session: Session,
    legacy_conn: sqlite3.Connection,
    workspace: models.Workspace,
    account_cache: dict[str, models.ChannelAccount],
    existing_keys: set[tuple[str, str, datetime]],
    summary: BackfillSummary,
) -> None:
    if not _table_exists(legacy_conn, "channel_sync_runs"):
        return
    rows = legacy_conn.execute(
        "SELECT source, synced_at, item_count, active_count, note FROM channel_sync_runs ORDER BY synced_at"
    ).fetchall()
    for row in rows:
        channel = row["source"]
        started_at = _epoch_to_dt(row["synced_at"])
        if started_at is None:
            continue
        key = (channel, "snapshot", started_at)
        if key in existing_keys:
            continue
        account, created = get_or_create_channel_account(session, workspace, channel, account_cache)
        if created:
            summary.channel_accounts += 1
        session.add(
            models.ConnectorSyncRun(
                workspace_id=workspace.id,
                channel_account_id=account.id,
                channel=channel,
                run_type="snapshot",
                status=SyncRunStatus.SUCCESS,
                started_at=started_at,
                completed_at=started_at,
                item_count=row["item_count"],
                active_count=row["active_count"],
                detail={"note": row["note"]} if row["note"] else {},
            )
        )
        existing_keys.add(key)
        summary.connector_sync_runs += 1


def _backfill_biblio_ftp_runs(
    session: Session,
    legacy_conn: sqlite3.Connection,
    workspace: models.Workspace,
    account_cache: dict[str, models.ChannelAccount],
    existing_keys: set[tuple[str, str, datetime]],
    summary: BackfillSummary,
) -> None:
    if not _table_exists(legacy_conn, "biblio_ftp_runs"):
        return
    rows = legacy_conn.execute(
        """
        SELECT attempted_at, action, status, inventory_filename, deletes_filename,
               active_count, delete_count, detail
        FROM biblio_ftp_runs
        ORDER BY attempted_at
        """
    ).fetchall()
    if not rows:
        return
    account, created = get_or_create_channel_account(session, workspace, Channel.BIBLIO, account_cache)
    if created:
        summary.channel_accounts += 1
    for row in rows:
        run_type = f"ftp_{row['action']}"
        started_at = _epoch_to_dt(row["attempted_at"])
        if started_at is None:
            continue
        key = (Channel.BIBLIO, run_type, started_at)
        if key in existing_keys:
            continue
        status = SyncRunStatus.SUCCESS if row["status"] == "success" else SyncRunStatus.ERROR
        session.add(
            models.ConnectorSyncRun(
                workspace_id=workspace.id,
                channel_account_id=account.id,
                channel=Channel.BIBLIO,
                run_type=run_type,
                status=status,
                started_at=started_at,
                completed_at=started_at,
                active_count=row["active_count"],
                delete_count=row["delete_count"],
                detail={
                    "inventory_filename": row["inventory_filename"],
                    "deletes_filename": row["deletes_filename"],
                },
                error=row["detail"] if status == SyncRunStatus.ERROR else None,
            )
        )
        existing_keys.add(key)
        summary.connector_sync_runs += 1


# --------------------------------------------------------------------------
# sync_runs + listing_observations + profile_observations
# -> ConnectorSyncRun(vinted) + ListingSnapshot + ProfileObservation
# --------------------------------------------------------------------------


def _backfill_vinted_observations(
    session: Session,
    legacy_conn: sqlite3.Connection,
    workspace: models.Workspace,
    account_cache: dict[str, models.ChannelAccount],
    listing_index: dict[tuple[str, str], models.ChannelListing],
    existing_run_keys: set[tuple[str, str, datetime]],
    summary: BackfillSummary,
) -> None:
    if not _table_exists(legacy_conn, "sync_runs"):
        return
    sync_rows = legacy_conn.execute("SELECT id, collected_at FROM sync_runs ORDER BY collected_at").fetchall()
    if not sync_rows:
        return
    account, created = get_or_create_channel_account(session, workspace, Channel.VINTED, account_cache)
    if created:
        summary.channel_accounts += 1

    existing_snapshots = set(
        session.execute(
            select(models.ListingSnapshot.channel_listing_id, models.ListingSnapshot.captured_at)
            .join(models.ChannelListing)
            .where(models.ChannelListing.workspace_id == workspace.id)
        ).all()
    )
    existing_profile_obs = set(
        session.execute(
            select(models.ProfileObservation.channel_account_id, models.ProfileObservation.captured_at).where(
                models.ProfileObservation.channel_account_id == account.id
            )
        ).all()
    )

    has_listing_observations = _table_exists(legacy_conn, "listing_observations")
    has_profile_observations = _table_exists(legacy_conn, "profile_observations")

    for sync_row in sync_rows:
        sync_id = sync_row["id"]
        captured_at = _epoch_to_dt(sync_row["collected_at"])
        if captured_at is None:
            continue

        listing_obs_rows: list[sqlite3.Row] = []
        if has_listing_observations:
            listing_obs_rows = legacy_conn.execute(
                """
                SELECT listing_id, price_cents, status, favourites, views
                FROM listing_observations
                WHERE sync_id = ?
                """,
                (sync_id,),
            ).fetchall()

        for obs in listing_obs_rows:
            listing = listing_index.get((Channel.VINTED, obs["listing_id"]))
            if listing is None:
                continue  # no matching channel_items row; shouldn't normally happen
            snapshot_key = (listing.id, captured_at)
            if snapshot_key in existing_snapshots:
                continue
            session.add(
                models.ListingSnapshot(
                    channel_listing_id=listing.id,
                    captured_at=captured_at,
                    price_cents=obs["price_cents"],
                    status=obs["status"],
                    views=obs["views"],
                    favourites=obs["favourites"],
                )
            )
            existing_snapshots.add(snapshot_key)
            summary.listing_snapshots += 1

        if has_profile_observations:
            profile_row = legacy_conn.execute(
                "SELECT followers, following FROM profile_observations WHERE sync_id = ?", (sync_id,)
            ).fetchone()
            profile_key = (account.id, captured_at)
            if profile_row is not None and profile_key not in existing_profile_obs:
                session.add(
                    models.ProfileObservation(
                        channel_account_id=account.id,
                        captured_at=captured_at,
                        followers=profile_row["followers"],
                        following=profile_row["following"],
                    )
                )
                existing_profile_obs.add(profile_key)
                summary.profile_observations += 1

        run_key = (Channel.VINTED, "snapshot", captured_at)
        if run_key not in existing_run_keys:
            session.add(
                models.ConnectorSyncRun(
                    workspace_id=workspace.id,
                    channel_account_id=account.id,
                    channel=Channel.VINTED,
                    run_type="snapshot",
                    status=SyncRunStatus.SUCCESS,
                    started_at=captured_at,
                    completed_at=captured_at,
                    item_count=len(listing_obs_rows),
                )
            )
            existing_run_keys.add(run_key)
            summary.connector_sync_runs += 1


# --------------------------------------------------------------------------
# favorite_events -> FavoriteEvent
# --------------------------------------------------------------------------


def _backfill_favorite_events(
    session: Session,
    legacy_conn: sqlite3.Connection,
    workspace: models.Workspace,
    listing_index: dict[tuple[str, str], models.ChannelListing],
    summary: BackfillSummary,
) -> None:
    if not _table_exists(legacy_conn, "favorite_events"):
        return
    rows = legacy_conn.execute(
        "SELECT notification_id, item_id, item_title, actor, occurred_at, first_seen_at FROM favorite_events"
    ).fetchall()
    if not rows:
        return
    existing_ids = set(
        session.execute(
            select(models.FavoriteEvent.notification_id).where(
                models.FavoriteEvent.workspace_id == workspace.id
            )
        ).scalars()
    )
    for row in rows:
        notification_id = row["notification_id"]
        if notification_id in existing_ids:
            # Legacy semantics are INSERT OR IGNORE: never overwrite an
            # already-recorded event.
            continue
        listing = listing_index.get((Channel.VINTED, row["item_id"])) if row["item_id"] else None
        session.add(
            models.FavoriteEvent(
                notification_id=notification_id,
                workspace_id=workspace.id,
                channel_listing_id=listing.id if listing else None,
                item_external_id=row["item_id"],
                item_title=row["item_title"],
                actor=row["actor"],
                occurred_at=_text_to_dt(row["occurred_at"]),
                first_seen_at=_epoch_to_dt(row["first_seen_at"]) or _utcnow(),
            )
        )
        existing_ids.add(notification_id)
        summary.favorite_events += 1


# --------------------------------------------------------------------------
# orders_history -> Sale
# --------------------------------------------------------------------------


def _backfill_orders_history(
    session: Session,
    legacy_conn: sqlite3.Connection,
    workspace: models.Workspace,
    account_cache: dict[str, models.ChannelAccount],
    summary: BackfillSummary,
) -> None:
    if not _table_exists(legacy_conn, "orders_history"):
        return
    rows = legacy_conn.execute(
        """
        SELECT order_key, direction, title, counterparty, total_cents, currency, status,
               lifecycle_status, is_closed, updated_at, vinted_url, first_seen_at, last_seen_at
        FROM orders_history
        """
    ).fetchall()
    if not rows:
        return
    account, created = get_or_create_channel_account(session, workspace, Channel.VINTED, account_cache)
    if created:
        summary.channel_accounts += 1
    existing = {
        (sale.channel, sale.direction, sale.external_order_id): sale
        for sale in session.execute(
            select(models.Sale).where(
                models.Sale.workspace_id == workspace.id, models.Sale.channel == Channel.VINTED
            )
        ).scalars()
    }
    for row in rows:
        direction = row["direction"]
        order_key = row["order_key"]
        # order_key = f"{direction}:{identity}"; strip the "direction:" prefix
        # back off to recover the bare external order id.
        external_order_id = order_key.split(":", 1)[1] if ":" in order_key else order_key
        first_seen = _epoch_to_dt(row["first_seen_at"]) or _utcnow()
        last_seen = _epoch_to_dt(row["last_seen_at"]) or _utcnow()
        occurred_at = _text_to_dt(row["updated_at"])
        extra = {"vinted_url": row["vinted_url"]} if row["vinted_url"] else {}

        key = (Channel.VINTED, direction, external_order_id)
        existing_sale = existing.get(key)
        if existing_sale is not None:
            existing_sale.title = row["title"]
            existing_sale.counterparty = row["counterparty"]
            existing_sale.total_cents = row["total_cents"]
            existing_sale.currency = row["currency"]
            existing_sale.status = row["status"]
            existing_sale.lifecycle_status = row["lifecycle_status"]
            existing_sale.is_closed = bool(row["is_closed"])
            existing_sale.occurred_at = occurred_at
            existing_sale.last_seen_at = max(existing_sale.last_seen_at, last_seen)
            existing_sale.extra = {**existing_sale.extra, **extra}
            continue

        sale = models.Sale(
            workspace_id=workspace.id,
            channel_account_id=account.id,
            channel=Channel.VINTED,
            external_order_id=external_order_id,
            direction=direction,
            title=row["title"],
            counterparty=row["counterparty"],
            total_cents=row["total_cents"],
            currency=row["currency"],
            status=row["status"],
            lifecycle_status=row["lifecycle_status"],
            is_closed=bool(row["is_closed"]),
            occurred_at=occurred_at,
            first_seen_at=first_seen,
            last_seen_at=last_seen,
            extra=extra,
        )
        session.add(sale)
        existing[key] = sale
        summary.sales += 1


# --------------------------------------------------------------------------
# market_research -> MarketResearch
# --------------------------------------------------------------------------


def _backfill_market_research(
    session: Session,
    legacy_conn: sqlite3.Connection,
    workspace: models.Workspace,
    listing_index: dict[tuple[str, str], models.ChannelListing],
    summary: BackfillSummary,
) -> None:
    if not _table_exists(legacy_conn, "market_research"):
        return
    rows = legacy_conn.execute(
        """
        SELECT listing_id, query, requested_at, status, completed_at, sample_count,
               min_cents, median_cents, max_cents, currency, results_json, error
        FROM market_research
        """
    ).fetchall()
    if not rows:
        return
    existing_keys = set(
        session.execute(
            select(models.MarketResearch.listing_external_id, models.MarketResearch.requested_at).where(
                models.MarketResearch.workspace_id == workspace.id
            )
        ).all()
    )
    for row in rows:
        requested_at = _epoch_to_dt(row["requested_at"])
        if requested_at is None:
            continue
        key = (row["listing_id"], requested_at)
        if key in existing_keys:
            continue
        listing = listing_index.get((Channel.VINTED, row["listing_id"]))
        results = None
        if row["results_json"]:
            try:
                results = json.loads(row["results_json"])
            except (TypeError, ValueError):
                results = None
        session.add(
            models.MarketResearch(
                workspace_id=workspace.id,
                channel_listing_id=listing.id if listing else None,
                listing_external_id=row["listing_id"],
                query=row["query"],
                requested_at=requested_at,
                status=row["status"],
                completed_at=_epoch_to_dt(row["completed_at"]),
                sample_count=row["sample_count"],
                min_cents=row["min_cents"],
                median_cents=row["median_cents"],
                max_cents=row["max_cents"],
                currency=row["currency"],
                results=results,
                error=row["error"],
            )
        )
        existing_keys.add(key)
        summary.market_research += 1


# --------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------


def run_legacy_backfill(
    session: Session,
    *,
    legacy_path: Optional[Path] = None,
    workspace_name: str = BOOTSTRAP_WORKSPACE_NAME,
    workspace_slug: str = BOOTSTRAP_WORKSPACE_SLUG,
    owner_email: str = BOOTSTRAP_OWNER_EMAIL,
) -> Optional[BackfillSummary]:
    """Backfill ``session``'s database from the legacy sqlite3 file at
    ``legacy_path`` (default: ``LEGACY_SQLITE_PATH``/``VINTED_HISTORY_DB``).

    Returns ``None`` (and does nothing) if no legacy database file exists
    yet - e.g. a brand new install with nothing to migrate. Does not commit;
    callers control the transaction boundary (use :func:`app.db.session_scope`
    for a one-shot call that commits on success).
    """

    path = legacy_path or DEFAULT_LEGACY_SQLITE_PATH
    if not path.exists():
        logger.info("legacy_migration: no legacy database found at %s, skipping", path)
        return None

    workspace = get_or_create_workspace(session, workspace_name, workspace_slug)
    get_or_create_owner(session, workspace, owner_email)
    session.flush()

    account_cache: dict[str, models.ChannelAccount] = {}
    summary = BackfillSummary()
    existing_run_keys = _preload_sync_run_keys(session, workspace)

    legacy_conn = _legacy_connect(path)
    try:
        listing_index = _backfill_channel_items(session, legacy_conn, workspace, account_cache, summary)
        session.flush()
        _backfill_channel_sync_runs(session, legacy_conn, workspace, account_cache, existing_run_keys, summary)
        _backfill_biblio_ftp_runs(session, legacy_conn, workspace, account_cache, existing_run_keys, summary)
        _backfill_vinted_observations(
            session, legacy_conn, workspace, account_cache, listing_index, existing_run_keys, summary
        )
        _backfill_favorite_events(session, legacy_conn, workspace, listing_index, summary)
        _backfill_orders_history(session, legacy_conn, workspace, account_cache, summary)
        session.flush()
        sale_links = auto_link_unlinked_sales(session, workspace.id)
        summary.sales_auto_linked += int(sale_links["linked"])
        _backfill_market_research(session, legacy_conn, workspace, listing_index, summary)
    finally:
        legacy_conn.close()

    session.add(
        models.LegacyBackfillRun(
            source_path=str(path),
            source_signature=_file_signature(path),
            workspace_id=workspace.id,
            summary=summary.as_dict(),
        )
    )
    session.flush()
    logger.info("legacy_migration: backfill complete: %s", summary.as_dict())
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--legacy-path",
        type=Path,
        default=None,
        help="Path to the legacy vinted-history.sqlite3 file (default: $LEGACY_SQLITE_PATH / $VINTED_HISTORY_DB)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    db.create_all()
    with db.session_scope() as session:
        summary = run_legacy_backfill(session, legacy_path=args.legacy_path)

    if summary is None:
        print("No legacy database found; nothing to backfill.")
        return
    for key, value in summary.as_dict().items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
