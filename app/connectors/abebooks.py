"""AbeBooks inventory connector using the official Inventory FTP interface.

The connector is deliberately explicit:
- users enroll individual master book items locally before they are eligible;
- the exact tab-delimited inventory can be previewed/downloaded without upload;
- full sync requires both AbeBooks format approval and an explicit user
  confirmation that the dashboard may manage the enrolled listing IDs;
- remote removal uses AbeBooks' documented delnum.tab mechanism.

No order processing or AMoP behavior is implemented here.
"""

from __future__ import annotations

import csv
import ftplib
import io
import re
import ssl
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app import db, models
from app.constants import (
    Channel,
    ChannelAccountStatus,
    ItemCategory,
    ItemStatus,
    ListingStatus,
    SyncRunStatus,
)
from app.crypto import decrypt_json
from app.product_models import ConnectorCredential
from app.workspace_bootstrap import clean_isbn, get_or_create_channel_account


ABEBOOKS_HOST = "ftp.abebooks.com"
ABEBOOKS_PORT = 21
DEFAULT_CURRENCY = "EUR"
INVENTORY_HEADERS = [
    "listingid",
    "title",
    "author",
    "price",
    "quantity",
    "producttype",
    "description",
    "bindingtext",
    "bookcondition",
    "publishername",
    "yearpublished",
    "isbn",
    "editiontext",
]


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _credentials(workspace_id: uuid.UUID) -> dict[str, str]:
    with db.session_scope() as session:
        row = session.execute(
            select(ConnectorCredential).where(
                ConnectorCredential.workspace_id == workspace_id,
                ConnectorCredential.channel == Channel.ABEBOOKS,
            )
        ).scalar_one_or_none()
        if row is None:
            raise RuntimeError("AbeBooks credentials are not configured")
        values = decrypt_json(row.encrypted_payload)
    return {str(key): str(value) for key, value in values.items() if str(value).strip()}


def credential_status(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id)
    return {
        "configured": bool(values.get("username") and values.get("api_key")),
        "format_confirmed": _truthy(values.get("format_confirmed")),
        "sync_confirmed": _truthy(values.get("sync_confirmed")),
        "currency": str(values.get("currency") or DEFAULT_CURRENCY).upper(),
    }


def _normalized_username(values: dict[str, str]) -> str:
    username = re.sub(r"\s+", "", str(values.get("username") or "")).upper()
    if not username:
        raise RuntimeError("AbeBooks needs a seller User ID")
    return username


def _connect(values: dict[str, str]) -> ftplib.FTP_TLS:
    api_key = str(values.get("api_key") or "").strip()
    if not api_key:
        raise RuntimeError("AbeBooks needs an API Key")

    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    ftp = ftplib.FTP_TLS(context=context)
    ftp.connect(
        ABEBOOKS_HOST,
        ABEBOOKS_PORT,
        timeout=max(5, min(120, int(values.get("timeout_seconds") or 20))),
    )
    ftp.auth()
    ftp.login(_normalized_username(values), api_key)
    ftp.prot_p()
    ftp.set_pasv(True)
    return ftp


def _close_ftp(ftp: ftplib.FTP_TLS) -> None:
    try:
        ftp.quit()
    except Exception:
        try:
            ftp.close()
        except Exception:
            pass


def test_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id)
    ftp = _connect(values)
    try:
        directory = ftp.pwd()
    finally:
        _close_ftp(ftp)
    status = credential_status(workspace_id)
    return {
        "ok": True,
        "detail": f"Connected to AbeBooks FTPS; directory {directory}",
        **status,
    }


def _clean_cell(value: Any) -> str:
    return re.sub(r"[\t\r\n]+", " ", str(value or "")).strip()


def _binding(value: Any) -> str | None:
    normalized = " ".join(_clean_cell(value).casefold().replace("-", " ").split())
    mapping = {
        "hardcover": "Hardcover",
        "hard cover": "Hardcover",
        "hardback": "Hardback",
        "hard back": "Hardback",
        "hc": "Hardcover",
        "softcover": "Softcover",
        "soft cover": "Softcover",
        "paperback": "Paperback",
        "paper back": "Paperback",
        "pb": "Paperback",
        "no binding": "No binding",
    }
    return mapping.get(normalized)


def _condition(value: Any) -> str | None:
    normalized = " ".join(
        _clean_cell(value).casefold().replace("_", " ").replace("-", " ").split()
    )
    mapping = {
        "new": "New",
        "as new": "As New",
        "like new": "As New",
        "fine": "Fine",
        "near fine": "Near Fine",
        "very good": "Very Good",
        "good": "Good",
        "fair": "Fair",
        "poor": "Poor",
    }
    return mapping.get(normalized)


def _book_row(
    listing: models.ChannelListing,
    item: models.InventoryItem,
    *,
    connector_currency: str,
) -> dict[str, Any]:
    attrs = dict(item.attributes or {})
    description = (
        attrs.get("listing_description")
        or attrs.get("description")
        or item.notes
        or ""
    )
    price_cents = attrs.get("default_price_cents")
    quantity = max(0, int(item.quantity or 0))
    publication_year = str(
        attrs.get("publication_year")
        or attrs.get("yearpublished")
        or ""
    ).strip()
    if publication_year and not re.fullmatch(r"\d{4}", publication_year):
        publication_year = ""

    return {
        "listing_id": listing.id,
        "listingid": _clean_cell(listing.external_id or item.sku),
        "title": _clean_cell(item.title),
        "author": _clean_cell(attrs.get("author")),
        "price_cents": int(price_cents) if price_cents not in (None, "") else None,
        "quantity": quantity,
        "producttype": "Book",
        "description": _clean_cell(description),
        "bindingtext": _binding(attrs.get("binding")),
        "bookcondition": _condition(item.condition),
        "publishername": _clean_cell(attrs.get("publisher")),
        "yearpublished": publication_year,
        "isbn": clean_isbn(attrs.get("isbn")) or "",
        "editiontext": _clean_cell(attrs.get("edition")),
        "currency": str(item.currency or DEFAULT_CURRENCY).upper(),
        "connector_currency": connector_currency,
        "title_length": len(_clean_cell(item.title)),
        "description_length": len(_clean_cell(description)),
        "author_length": len(_clean_cell(attrs.get("author"))),
        "publisher_length": len(_clean_cell(attrs.get("publisher"))),
        "edition_length": len(_clean_cell(attrs.get("edition"))),
        "synced": _truthy((listing.extra or {}).get("abebooks_synced")),
    }


def _missing(row: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    required = (
        ("listingid", "listing ID"),
        ("title", "title"),
        ("price_cents", "asking price"),
        ("description", "description"),
        ("bindingtext", "binding"),
        ("bookcondition", "book condition"),
    )
    for key, label in required:
        if row.get(key) in (None, ""):
            missing.append(label)

    listing_id = str(row.get("listingid") or "")
    if len(listing_id) > 40:
        missing.append("listing ID longer than 40 characters")
    if row.get("title_length", 0) > 750:
        missing.append("title longer than 750 characters")
    if row.get("description_length", 0) > 4000:
        missing.append("description longer than 4000 characters")
    if row.get("author_length", 0) > 750:
        missing.append("author longer than 750 characters")
    if row.get("publisher_length", 0) > 750:
        missing.append("publisher longer than 750 characters")
    if row.get("edition_length", 0) > 50:
        missing.append("edition longer than 50 characters")
    if row.get("price_cents") is not None and int(row["price_cents"]) < 100:
        missing.append("asking price below 1.00")
    if int(row.get("quantity") or 0) > 999:
        missing.append("quantity above 999")
    if str(row.get("currency") or "").upper() != str(
        row.get("connector_currency") or ""
    ).upper():
        missing.append(
            f"currency must be {row.get('connector_currency')}"
        )
    return missing


def _prepare(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id)
    connector_currency = str(values.get("currency") or DEFAULT_CURRENCY).upper()
    with db.session_scope() as session:
        enrolled = session.execute(
            select(models.ChannelListing, models.InventoryItem)
            .join(
                models.InventoryItem,
                models.InventoryItem.id == models.ChannelListing.inventory_item_id,
            )
            .where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.channel == Channel.ABEBOOKS,
            )
            .order_by(models.ChannelListing.external_id)
        ).all()

        enrolled_item_ids = {
            listing.inventory_item_id
            for listing, _item in enrolled
            if listing.inventory_item_id is not None
        }
        candidate_query = select(models.InventoryItem).where(
            models.InventoryItem.workspace_id == workspace_id,
            models.InventoryItem.category == ItemCategory.BOOK,
            models.InventoryItem.status == ItemStatus.ACTIVE,
            models.InventoryItem.quantity > 0,
        )
        if enrolled_item_ids:
            candidate_query = candidate_query.where(
                models.InventoryItem.id.not_in(enrolled_item_ids)
            )
        candidates = session.execute(
            candidate_query.order_by(models.InventoryItem.title).limit(250)
        ).scalars().all()
        candidate_total = session.execute(
            select(models.InventoryItem.id).where(
                models.InventoryItem.workspace_id == workspace_id,
                models.InventoryItem.category == ItemCategory.BOOK,
                models.InventoryItem.status == ItemStatus.ACTIVE,
                models.InventoryItem.quantity > 0,
            )
        ).scalars().all()

    active: list[dict[str, Any]] = []
    deletes: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []
    for listing, item in enrolled:
        desired_active = (
            item.category == ItemCategory.BOOK
            and item.status == ItemStatus.ACTIVE
            and int(item.quantity or 0) > 0
            and listing.status in {ListingStatus.DRAFT, ListingStatus.ACTIVE}
        )
        row = _book_row(
            listing,
            item,
            connector_currency=connector_currency,
        )
        if desired_active:
            missing = _missing(row)
            if missing:
                incomplete.append(
                    {
                        "listing_id": str(listing.id),
                        "item_id": str(item.id),
                        "sku": item.sku,
                        "title": item.title,
                        "missing": missing,
                    }
                )
            active.append(row)
        elif row["synced"]:
            deletes.append(
                {
                    "listing_id": listing.id,
                    "listingid": row["listingid"],
                    "item_id": item.id,
                }
            )

    candidate_rows = []
    for item in candidates:
        attrs = dict(item.attributes or {})
        candidate_rows.append(
            {
                "item_id": str(item.id),
                "sku": item.sku,
                "title": item.title,
                "price_cents": attrs.get("default_price_cents"),
                "currency": item.currency or DEFAULT_CURRENCY,
                "condition": item.condition,
                "binding": attrs.get("binding"),
            }
        )

    status = credential_status(workspace_id)
    return {
        "active": active,
        "deletes": deletes,
        "incomplete": incomplete,
        "candidates": candidate_rows,
        "candidate_total": max(0, len(candidate_total) - len(enrolled_item_ids)),
        **status,
    }


def preview_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    prepared = _prepare(workspace_id)
    return {
        "configured": prepared["configured"],
        "format_confirmed": prepared["format_confirmed"],
        "sync_confirmed": prepared["sync_confirmed"],
        "currency": prepared["currency"],
        "enrolled_count": len(prepared["active"]) + len(prepared["deletes"]),
        "active_count": len(prepared["active"]),
        "delete_count": len(prepared["deletes"]),
        "incomplete_count": len(prepared["incomplete"]),
        "incomplete": prepared["incomplete"][:50],
        "candidates": prepared["candidates"],
        "candidate_total": prepared["candidate_total"],
        "ready": (
            prepared["format_confirmed"]
            and prepared["sync_confirmed"]
            and not prepared["incomplete"]
        ),
    }


def enroll_items(
    workspace_id: uuid.UUID,
    item_ids: list[uuid.UUID],
) -> dict[str, Any]:
    unique_ids = list(dict.fromkeys(item_ids))
    if not unique_ids:
        raise ValueError("Select at least one book")
    if len(unique_ids) > 250:
        raise ValueError("Enroll at most 250 books at once")

    now = datetime.now(timezone.utc)
    created = 0
    existing = 0
    with db.session_scope() as session:
        workspace = session.get(models.Workspace, workspace_id)
        if workspace is None:
            raise ValueError("Workspace does not exist")
        items = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.workspace_id == workspace_id,
                models.InventoryItem.id.in_(unique_ids),
            )
        ).scalars().all()
        if len(items) != len(unique_ids):
            raise ValueError("One or more selected inventory items do not exist")
        invalid = [
            item for item in items
            if (
                item.category != ItemCategory.BOOK
                or item.status != ItemStatus.ACTIVE
                or int(item.quantity or 0) <= 0
            )
        ]
        if invalid:
            raise ValueError("Only active in-stock book items can be enrolled")

        account, _ = get_or_create_channel_account(
            session,
            workspace,
            Channel.ABEBOOKS,
            {},
        )
        for item in items:
            listing = session.execute(
                select(models.ChannelListing).where(
                    models.ChannelListing.workspace_id == workspace_id,
                    models.ChannelListing.channel == Channel.ABEBOOKS,
                    models.ChannelListing.external_id == item.sku,
                )
            ).scalar_one_or_none()
            if listing is not None:
                if listing.inventory_item_id != item.id:
                    raise ValueError(
                        f"AbeBooks listing ID {item.sku!r} is already linked to another item"
                    )
                existing += 1
                continue
            attrs = dict(item.attributes or {})
            session.add(
                models.ChannelListing(
                    workspace_id=workspace_id,
                    inventory_item_id=item.id,
                    channel_account_id=account.id,
                    channel=Channel.ABEBOOKS,
                    external_id=item.sku,
                    external_sku=item.sku,
                    title=item.title,
                    price_cents=attrs.get("default_price_cents"),
                    currency=item.currency or DEFAULT_CURRENCY,
                    status=ListingStatus.DRAFT,
                    quantity=item.quantity,
                    first_seen_at=now,
                    last_seen_at=now,
                    extra={"abebooks_synced": False},
                )
            )
            created += 1
    return {"created": created, "existing": existing}


def _inventory_bytes(rows: list[dict[str, Any]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(
        output,
        delimiter="\t",
        lineterminator="\n",
        quoting=csv.QUOTE_MINIMAL,
    )
    writer.writerow(INVENTORY_HEADERS)
    for row in rows:
        writer.writerow(
            [
                row["listingid"],
                row["title"],
                row["author"],
                f"{int(row['price_cents']) / 100:.2f}",
                min(999, max(1, int(row["quantity"]))),
                "Book",
                row["description"],
                row["bindingtext"],
                row["bookcondition"],
                row["publishername"],
                row["yearpublished"],
                row["isbn"],
                row["editiontext"],
            ]
        )
    return output.getvalue().encode("utf-8")


def export_workspace(workspace_id: uuid.UUID) -> tuple[bytes, dict[str, Any]]:
    prepared = _prepare(workspace_id)
    if prepared["incomplete"]:
        examples = ", ".join(
            f"{row['sku']} ({'/'.join(row['missing'])})"
            for row in prepared["incomplete"][:5]
        )
        raise RuntimeError(
            "AbeBooks export blocked: enrolled books have missing/invalid fields. "
            f"Examples: {examples}"
        )
    return _inventory_bytes(prepared["active"]), preview_workspace(workspace_id)


def _delnum_bytes(listing_ids: list[str]) -> bytes:
    return (
        "".join(f"{_clean_cell(value)}\n" for value in listing_ids)
    ).encode("utf-8")


def _record_run(
    workspace_id: uuid.UUID,
    *,
    status: str,
    started_at: datetime,
    active_count: int,
    delete_count: int,
    detail: dict[str, Any],
    error: str | None = None,
) -> None:
    with db.session_scope() as session:
        workspace = session.get(models.Workspace, workspace_id)
        if workspace is None:
            raise RuntimeError("Workspace does not exist")
        account, _ = get_or_create_channel_account(
            session,
            workspace,
            Channel.ABEBOOKS,
            {},
        )
        account.last_synced_at = started_at
        account.status = (
            ChannelAccountStatus.CONNECTED
            if status == SyncRunStatus.SUCCESS
            else ChannelAccountStatus.ERROR
        )
        session.add(
            models.ConnectorSyncRun(
                workspace_id=workspace_id,
                channel_account_id=account.id,
                channel=Channel.ABEBOOKS,
                run_type="ftps_sync",
                status=status,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                active_count=active_count,
                delete_count=delete_count,
                detail=detail,
                error=error,
            )
        )


def _finalize_listing_state(
    workspace_id: uuid.UUID,
    active: list[dict[str, Any]],
    deletes: list[dict[str, Any]],
    *,
    synced_at: datetime,
) -> None:
    with db.session_scope() as session:
        for row in active:
            listing = session.get(models.ChannelListing, row["listing_id"])
            if listing is None or listing.workspace_id != workspace_id:
                continue
            listing.status = ListingStatus.ACTIVE
            listing.quantity = max(1, int(row["quantity"]))
            listing.price_cents = int(row["price_cents"])
            listing.currency = row["connector_currency"]
            listing.last_seen_at = synced_at
            listing.extra = {
                **dict(listing.extra or {}),
                "abebooks_synced": True,
                "last_uploaded_at": synced_at.isoformat(),
            }
        for row in deletes:
            listing = session.get(models.ChannelListing, row["listing_id"])
            if listing is None or listing.workspace_id != workspace_id:
                continue
            listing.status = ListingStatus.INACTIVE
            listing.quantity = 0
            listing.last_seen_at = synced_at
            listing.extra = {
                **dict(listing.extra or {}),
                "abebooks_synced": True,
                "remote_removed_at": synced_at.isoformat(),
            }


def sync_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id)
    prepared = _prepare(workspace_id)
    if not prepared["format_confirmed"]:
        raise RuntimeError(
            "AbeBooks sync is blocked until you confirm that AbeBooks Support "
            "has approved this inventory file format"
        )
    if not prepared["sync_confirmed"]:
        raise RuntimeError(
            "AbeBooks sync is blocked until you confirm that the dashboard may "
            "manage the enrolled AbeBooks listing IDs"
        )
    if prepared["incomplete"]:
        examples = ", ".join(
            f"{row['sku']} ({'/'.join(row['missing'])})"
            for row in prepared["incomplete"][:5]
        )
        raise RuntimeError(
            "AbeBooks sync blocked: enrolled books have missing/invalid fields. "
            f"Examples: {examples}"
        )

    active = prepared["active"]
    deletes = prepared["deletes"]
    started = datetime.now(timezone.utc)
    if not active and not deletes:
        _record_run(
            workspace_id,
            status=SyncRunStatus.SUCCESS,
            started_at=started,
            active_count=0,
            delete_count=0,
            detail={"message": "Nothing to upload"},
        )
        return {
            "ok": True,
            "active": 0,
            "deletes": 0,
            "detail": "Nothing to upload",
        }

    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    prefix = re.sub(
        r"[^A-Za-z0-9_-]+",
        "-",
        str(values.get("filename_prefix") or "reseller-dashboard").strip(),
    ).strip("-") or "reseller-dashboard"
    inventory_filename = (
        f"{prefix}-{stamp}.tab" if active else None
    )
    delete_filename = "delnum.tab" if deletes else None

    try:
        ftp = _connect(values)
        try:
            if inventory_filename:
                ftp.storbinary(
                    f"STOR {inventory_filename}",
                    io.BytesIO(_inventory_bytes(active)),
                )
            if delete_filename:
                ftp.storbinary(
                    f"STOR {delete_filename}",
                    io.BytesIO(
                        _delnum_bytes(
                            [str(row["listingid"]) for row in deletes]
                        )
                    ),
                )
        finally:
            _close_ftp(ftp)
    except Exception as exc:
        _record_run(
            workspace_id,
            status=SyncRunStatus.ERROR,
            started_at=started,
            active_count=len(active),
            delete_count=len(deletes),
            detail={
                "inventory_filename": inventory_filename,
                "deletes_filename": delete_filename,
            },
            error=str(exc),
        )
        raise RuntimeError("AbeBooks FTPS sync failed") from exc

    completed = datetime.now(timezone.utc)
    _finalize_listing_state(
        workspace_id,
        active,
        deletes,
        synced_at=completed,
    )
    _record_run(
        workspace_id,
        status=SyncRunStatus.SUCCESS,
        started_at=started,
        active_count=len(active),
        delete_count=len(deletes),
        detail={
            "inventory_filename": inventory_filename,
            "deletes_filename": delete_filename,
        },
    )
    return {
        "ok": True,
        "active": len(active),
        "deletes": len(deletes),
        "inventory_filename": inventory_filename,
        "deletes_filename": delete_filename,
    }


def close_workspace_listing(
    workspace_id: uuid.UUID,
    listing_id: uuid.UUID,
) -> dict[str, Any]:
    with db.session_scope() as session:
        listing = session.get(models.ChannelListing, listing_id)
        if (
            listing is None
            or listing.workspace_id != workspace_id
            or listing.channel != Channel.ABEBOOKS
        ):
            raise RuntimeError("AbeBooks listing no longer exists")
        external_id = str(listing.external_id)
        was_synced = _truthy((listing.extra or {}).get("abebooks_synced"))

    if not was_synced:
        return {
            "remote": "not_uploaded",
            "external_id": external_id,
        }

    values = _credentials(workspace_id)
    started = datetime.now(timezone.utc)
    try:
        ftp = _connect(values)
        try:
            ftp.storbinary(
                "STOR delnum.tab",
                io.BytesIO(_delnum_bytes([external_id])),
            )
        finally:
            _close_ftp(ftp)
    except Exception as exc:
        _record_run(
            workspace_id,
            status=SyncRunStatus.ERROR,
            started_at=started,
            active_count=0,
            delete_count=1,
            detail={"deletes_filename": "delnum.tab", "cross_channel": True},
            error=str(exc),
        )
        raise RuntimeError("AbeBooks delete upload failed") from exc

    _record_run(
        workspace_id,
        status=SyncRunStatus.SUCCESS,
        started_at=started,
        active_count=0,
        delete_count=1,
        detail={"deletes_filename": "delnum.tab", "cross_channel": True},
    )
    return {
        "remote": "delete_uploaded",
        "external_id": external_id,
        "deletes_filename": "delnum.tab",
    }
