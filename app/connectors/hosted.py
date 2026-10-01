"""Workspace-native marketplace connector adapters.

The legacy personal installation still has env-backed helpers in app.channels.
This module is the hosted/multi-tenant equivalent: credentials are loaded from
the encrypted ConnectorCredential row for one workspace and all resulting
inventory is written directly to the workspace/master-inventory schema.
"""

from __future__ import annotations

import base64
import csv
import ftplib
import io
import os
import re
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

from curl_cffi import requests
from sqlalchemy import select

from app import db, models
from app.constants import Channel, ListingStatus, SyncRunStatus
from app.crypto import decrypt_json
from app.product_models import ConnectorCredential
from app.workspace_bootstrap import get_or_create_channel_account
from app.connectors.workspace_sync import record_workspace_channel_snapshot


def _credentials(workspace_id: uuid.UUID, channel: str) -> dict[str, str]:
    with db.session_scope() as session:
        row = session.execute(
            select(ConnectorCredential).where(
                ConnectorCredential.workspace_id == workspace_id,
                ConnectorCredential.channel == channel,
            )
        ).scalar_one_or_none()
        if row is None:
            raise RuntimeError(f"{channel} credentials are not configured")
        values = decrypt_json(row.encrypted_payload)
    return {str(k): str(v) for k, v in values.items() if str(v).strip()}


def has_credentials(workspace_id: uuid.UUID, channel: str) -> bool:
    with db.session_scope() as session:
        return session.execute(
            select(ConnectorCredential.id).where(
                ConnectorCredential.workspace_id == workspace_id,
                ConnectorCredential.channel == channel,
            )
        ).scalar_one_or_none() is not None


def _money(value: Any) -> int | None:
    if value in (None, ""):
        return None
    match = re.search(r"([0-9]+(?:[.,][0-9]{1,2})?)", str(value))
    if not match:
        return None
    try:
        return round(float(match.group(1).replace(",", ".")) * 100)
    except ValueError:
        return None


def _int(value: Any, default: int | None = None) -> int | None:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _ebay_access_token(values: dict[str, str]) -> str:
    direct = values.get("oauth_token", "").strip()
    if direct:
        return direct
    client_id = values.get("client_id", "").strip()
    client_secret = values.get("client_secret", "").strip()
    refresh_token = values.get("refresh_token", "").strip()
    if not (client_id and client_secret and refresh_token):
        raise RuntimeError(
            "eBay needs oauth_token, or client_id + client_secret + refresh_token"
        )
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    response = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=20,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"eBay OAuth refresh failed ({response.status_code})")
    payload = response.json()
    token = str(payload.get("access_token") or "")
    if not token:
        raise RuntimeError("eBay OAuth refresh returned no access token")
    return token


def _fetch_ebay_active(values: dict[str, str]) -> list[dict[str, Any]]:
    token = _ebay_access_token(values)
    site_id = values.get("site_id", "0").strip() or "0"
    compatibility = values.get("compatibility_level", "1477").strip() or "1477"
    items: list[dict[str, Any]] = []
    page = 1
    while page <= 125:
        body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetMyeBaySellingRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <ErrorLanguage>en_US</ErrorLanguage>
  <WarningLevel>High</WarningLevel>
  <ActiveList>
    <Include>true</Include>
    <Pagination>
      <EntriesPerPage>200</EntriesPerPage>
      <PageNumber>{page}</PageNumber>
    </Pagination>
  </ActiveList>
</GetMyeBaySellingRequest>"""
        response = requests.post(
            "https://api.ebay.com/ws/api.dll",
            headers={
                "Content-Type": "text/xml",
                "X-EBAY-API-CALL-NAME": "GetMyeBaySelling",
                "X-EBAY-API-COMPATIBILITY-LEVEL": compatibility,
                "X-EBAY-API-SITEID": site_id,
                "X-EBAY-API-IAF-TOKEN": token,
            },
            data=body.encode("utf-8"),
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"eBay inventory request failed ({response.status_code})")
        root = ET.fromstring(response.content)
        ns = {"e": "urn:ebay:apis:eBLBaseComponents"}
        ack = root.findtext("e:Ack", namespaces=ns)
        if ack not in {"Success", "Warning"}:
            message = root.findtext(".//e:LongMessage", namespaces=ns) or "Unknown eBay API error"
            raise RuntimeError(message)
        for node in root.findall(".//e:ActiveList/e:ItemArray/e:Item", ns):
            item_id = node.findtext("e:ItemID", namespaces=ns)
            if not item_id:
                continue
            price_node = node.find("e:SellingStatus/e:CurrentPrice", ns)
            if price_node is None:
                price_node = node.find("e:StartPrice", ns)
            quantity = _int(node.findtext("e:QuantityAvailable", namespaces=ns))
            if quantity is None:
                quantity = _int(node.findtext("e:Quantity", namespaces=ns), 1)
            items.append(
                {
                    "source_id": item_id,
                    "sku": node.findtext("e:SKU", namespaces=ns),
                    "title": node.findtext("e:Title", namespaces=ns) or "Untitled",
                    "status": ListingStatus.ACTIVE,
                    "quantity": quantity,
                    "price_cents": _money(price_node.text if price_node is not None else None),
                    "currency": price_node.attrib.get("currencyID") if price_node is not None else None,
                    "url": node.findtext("e:ListingDetails/e:ViewItemURL", namespaces=ns),
                }
            )
        total_pages = _int(
            root.findtext(".//e:ActiveList/e:PaginationResult/e:TotalNumberOfPages", namespaces=ns),
            1,
        ) or 1
        if page >= total_pages:
            break
        page += 1
    return items


def sync_ebay_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id, Channel.EBAY)
    items = _fetch_ebay_active(values)
    synced_at = datetime.now(timezone.utc)
    record_workspace_channel_snapshot(
        workspace_id,
        Channel.EBAY,
        items,
        synced_at=synced_at,
        full_snapshot=True,
        note="eBay GetMyeBaySelling",
    )
    return {"source": Channel.EBAY, "items": len(items), "active": len(items)}


def import_biblio_workspace(
    workspace_id: uuid.UUID,
    rows: list[dict[str, Any]],
    *,
    filename: str = "BIBLIO inventory",
) -> dict[str, Any]:
    if not rows:
        raise ValueError("No BIBLIO inventory rows could be read")
    active = sum(1 for row in rows if str(row.get("status") or "active").lower() == "active")
    record_workspace_channel_snapshot(
        workspace_id,
        Channel.BIBLIO,
        rows,
        synced_at=datetime.now(timezone.utc),
        full_snapshot=True,
        note=f"Imported {filename}",
    )
    return {"source": Channel.BIBLIO, "items": len(rows), "active": active}


def _biblio_rows(workspace_id: uuid.UUID) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with db.session_scope() as session:
        listings = session.execute(
            select(models.ChannelListing, models.InventoryItem)
            .join(models.InventoryItem, models.InventoryItem.id == models.ChannelListing.inventory_item_id)
            .where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.channel == Channel.BIBLIO,
            )
            .order_by(models.ChannelListing.external_id)
        ).all()
        latest = session.execute(
            select(models.ConnectorSyncRun)
            .where(
                models.ConnectorSyncRun.workspace_id == workspace_id,
                models.ConnectorSyncRun.channel == Channel.BIBLIO,
                models.ConnectorSyncRun.run_type == "ftp_sync",
                models.ConnectorSyncRun.status == SyncRunStatus.SUCCESS,
            )
            .order_by(models.ConnectorSyncRun.completed_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        cutoff = latest.completed_at if latest and latest.completed_at else None

        active: list[dict[str, Any]] = []
        deletes: list[dict[str, Any]] = []
        for listing, item in listings:
            attrs = dict(item.attributes or {})
            extra = dict(listing.extra or {})
            row = {
                "source_id": listing.external_id,
                "sku": listing.external_sku or item.sku,
                "title": listing.title or item.title,
                "author": extra.get("author") or attrs.get("author"),
                "description": extra.get("description") or attrs.get("description") or item.notes,
                "isbn": extra.get("isbn") or attrs.get("isbn"),
                "price_cents": listing.price_cents,
                "currency": listing.currency or item.currency or "EUR",
                "quantity": listing.quantity if listing.quantity is not None else item.quantity,
                "status": listing.status,
            }
            if listing.status == ListingStatus.ACTIVE and int(row["quantity"] or 0) > 0:
                active.append(row)
            elif cutoff is None or listing.updated_at > cutoff or listing.last_seen_at > cutoff:
                deletes.append(row)
    return active, deletes


def _missing_biblio(row: dict[str, Any]) -> list[str]:
    missing = []
    for key, label in (
        ("sku", "SKU"),
        ("author", "author"),
        ("title", "title"),
        ("description", "description"),
        ("price_cents", "price"),
    ):
        if row.get(key) in (None, ""):
            missing.append(label)
    return missing


def _biblio_tsv(rows: list[dict[str, Any]], *, sold: bool) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    writer.writerow(
        ["Book ID", "Author", "Title", "Description", "Price", "Status", "ISBN", "Quantity"]
    )
    for row in rows:
        price = (
            f"{int(row['price_cents']) / 100:.2f}"
            if row.get("price_cents") not in (None, "")
            else "0.00"
        )
        writer.writerow(
            [
                row.get("sku") or row.get("source_id") or "",
                row.get("author") or "",
                row.get("title") or "",
                row.get("description") or "",
                price,
                "sold" if sold else "for sale",
                row.get("isbn") or "",
                0 if sold else max(1, int(row.get("quantity") or 1)),
            ]
        )
    return output.getvalue().encode("utf-8")


def _record_biblio_run(
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
        account, _ = get_or_create_channel_account(session, workspace, Channel.BIBLIO, {})
        account.last_synced_at = started_at
        session.add(
            models.ConnectorSyncRun(
                workspace_id=workspace_id,
                channel_account_id=account.id,
                channel=Channel.BIBLIO,
                run_type="ftp_sync",
                status=status,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                active_count=active_count,
                delete_count=delete_count,
                detail=detail,
                error=error,
            )
        )


def test_biblio_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id, Channel.BIBLIO)
    host = values.get("host", "ftp.biblio.com").strip() or "ftp.biblio.com"
    username = values.get("username", "").strip()
    password = values.get("password", "").strip()
    if not username or not password:
        raise RuntimeError("BIBLIO needs username and password")
    ftp = ftplib.FTP()
    try:
        ftp.connect(host, timeout=_int(values.get("timeout_seconds"), 20) or 20)
        ftp.login(username, password)
        ftp.set_pasv(True)
        directory = values.get("directory", "").strip()
        if directory and directory not in {".", "./"}:
            ftp.cwd(directory)
        pwd = ftp.pwd()
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()
    return {"ok": True, "detail": f"Connected successfully; directory {pwd}"}


def sync_biblio_workspace(workspace_id: uuid.UUID) -> dict[str, Any]:
    values = _credentials(workspace_id, Channel.BIBLIO)
    active, deletes = _biblio_rows(workspace_id)
    incomplete = [(row, _missing_biblio(row)) for row in active if _missing_biblio(row)]
    if incomplete:
        examples = ", ".join(
            f"{row.get('sku') or row.get('source_id')} ({'/'.join(missing)})"
            for row, missing in incomplete[:5]
        )
        raise RuntimeError(f"BIBLIO upload blocked: required fields are missing. Examples: {examples}")

    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    prefix = re.sub(
        r"[^A-Za-z0-9_-]+",
        "-",
        values.get("filename_prefix", "reseller-dashboard").strip() or "reseller-dashboard",
    ).strip("-")
    inventory_filename = f"{prefix}-{stamp}.txt" if active else None
    deletes_filename = f"{prefix}-{stamp}-deletes.txt" if deletes else None
    started = datetime.now(timezone.utc)
    if not inventory_filename and not deletes_filename:
        _record_biblio_run(
            workspace_id,
            status=SyncRunStatus.SUCCESS,
            started_at=started,
            active_count=0,
            delete_count=0,
            detail={"message": "Nothing to upload"},
        )
        return {"ok": True, "active": 0, "deletes": 0, "detail": "Nothing to upload"}

    host = values.get("host", "ftp.biblio.com").strip() or "ftp.biblio.com"
    username = values.get("username", "").strip()
    password = values.get("password", "").strip()
    if not username or not password:
        raise RuntimeError("BIBLIO needs username and password")

    try:
        ftp = ftplib.FTP()
        ftp.connect(host, timeout=_int(values.get("timeout_seconds"), 20) or 20)
        ftp.login(username, password)
        ftp.set_pasv(True)
        directory = values.get("directory", "").strip()
        if directory and directory not in {".", "./"}:
            ftp.cwd(directory)
        if inventory_filename:
            ftp.storbinary(f"STOR {inventory_filename}", io.BytesIO(_biblio_tsv(active, sold=False)))
        if deletes_filename:
            ftp.storbinary(f"STOR {deletes_filename}", io.BytesIO(_biblio_tsv(deletes, sold=True)))
        try:
            ftp.quit()
        except Exception:
            ftp.close()
    except Exception as exc:
        _record_biblio_run(
            workspace_id,
            status=SyncRunStatus.ERROR,
            started_at=started,
            active_count=len(active),
            delete_count=len(deletes),
            detail={
                "inventory_filename": inventory_filename,
                "deletes_filename": deletes_filename,
            },
            error=str(exc),
        )
        raise RuntimeError("BIBLIO FTP sync failed") from exc

    _record_biblio_run(
        workspace_id,
        status=SyncRunStatus.SUCCESS,
        started_at=started,
        active_count=len(active),
        delete_count=len(deletes),
        detail={
            "inventory_filename": inventory_filename,
            "deletes_filename": deletes_filename,
        },
    )
    return {
        "ok": True,
        "active": len(active),
        "deletes": len(deletes),
        "inventory_filename": inventory_filename,
        "deletes_filename": deletes_filename,
    }
