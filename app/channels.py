from __future__ import annotations

import base64
import ftplib
import csv
import io
import json
import os
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from curl_cffi import requests


DB_PATH = Path(os.getenv("VINTED_HISTORY_DB", "/app/data/vinted-history.sqlite3"))


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS channel_items (
            source TEXT NOT NULL,
            source_id TEXT NOT NULL,
            sku TEXT,
            isbn TEXT,
            title TEXT NOT NULL,
            author TEXT,
            description TEXT,
            status TEXT NOT NULL,
            quantity INTEGER,
            price_cents INTEGER,
            currency TEXT,
            url TEXT,
            first_seen_at REAL NOT NULL,
            last_seen_at REAL NOT NULL,
            raw_json TEXT,
            PRIMARY KEY(source, source_id)
        );
        CREATE INDEX IF NOT EXISTS idx_channel_items_source_status
            ON channel_items(source, status);
        CREATE INDEX IF NOT EXISTS idx_channel_items_sku
            ON channel_items(sku);
        CREATE INDEX IF NOT EXISTS idx_channel_items_isbn
            ON channel_items(isbn);

        CREATE TABLE IF NOT EXISTS channel_sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            synced_at REAL NOT NULL,
            item_count INTEGER NOT NULL,
            active_count INTEGER NOT NULL,
            note TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_channel_sync_source
            ON channel_sync_runs(source, synced_at DESC);
        """
    )
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(channel_items)").fetchall()
    }
    if "description" not in columns:
        conn.execute("ALTER TABLE channel_items ADD COLUMN description TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS biblio_ftp_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            attempted_at REAL NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            inventory_filename TEXT,
            deletes_filename TEXT,
            active_count INTEGER NOT NULL DEFAULT 0,
            delete_count INTEGER NOT NULL DEFAULT 0,
            detail TEXT
        )
        """
    )
    return conn


def _int(value: Any, default: int | None = None) -> int | None:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _money(value: Any) -> int | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("\u00a0", " ")
    match = re.search(r"([0-9]+(?:[.,][0-9]{1,2})?)", text)
    if not match:
        return None
    try:
        return round(float(match.group(1).replace(",", ".")) * 100)
    except ValueError:
        return None


def _clean_isbn(value: Any) -> str | None:
    text = re.sub(r"[^0-9Xx]", "", str(value or ""))
    return text.upper() if len(text) in {10, 13} else None


def upsert_channel_snapshot(
    source: str,
    items: list[dict[str, Any]],
    *,
    synced_at: float | None = None,
    full_snapshot: bool = True,
    note: str | None = None,
) -> dict[str, Any]:
    source = source.strip().lower()
    synced_at = float(synced_at or time.time())
    seen: set[str] = set()

    with _connect() as conn:
        for item in items:
            source_id = str(item.get("source_id") or item.get("id") or "").strip()
            if not source_id:
                continue
            seen.add(source_id)
            status = str(item.get("status") or "active").lower()
            existing = conn.execute(
                "SELECT first_seen_at FROM channel_items WHERE source=? AND source_id=?",
                (source, source_id),
            ).fetchone()
            first_seen = float(existing["first_seen_at"]) if existing else synced_at
            conn.execute(
                """
                INSERT INTO channel_items(
                    source, source_id, sku, isbn, title, author, description, status, quantity,
                    price_cents, currency, url, first_seen_at, last_seen_at, raw_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, source_id) DO UPDATE SET
                    sku=excluded.sku,
                    isbn=excluded.isbn,
                    title=excluded.title,
                    author=excluded.author,
                    description=excluded.description,
                    status=excluded.status,
                    quantity=excluded.quantity,
                    price_cents=excluded.price_cents,
                    currency=excluded.currency,
                    url=excluded.url,
                    last_seen_at=excluded.last_seen_at,
                    raw_json=excluded.raw_json
                """,
                (
                    source,
                    source_id,
                    str(item.get("sku") or "").strip() or None,
                    _clean_isbn(item.get("isbn")),
                    str(item.get("title") or "Untitled").strip(),
                    str(item.get("author") or "").strip() or None,
                    str(item.get("description") or "").strip() or None,
                    status,
                    _int(item.get("quantity"), 1),
                    _int(item.get("price_cents")),
                    str(item.get("currency") or "").strip() or None,
                    str(item.get("url") or "").strip() or None,
                    first_seen,
                    synced_at,
                    json.dumps(item, ensure_ascii=False),
                ),
            )

        if full_snapshot:
            rows = conn.execute(
                "SELECT source_id FROM channel_items WHERE source=? AND status='active'",
                (source,),
            ).fetchall()
            missing = [str(row["source_id"]) for row in rows if str(row["source_id"]) not in seen]
            for source_id in missing:
                conn.execute(
                    """
                    UPDATE channel_items
                    SET status='inactive', quantity=0, last_seen_at=?
                    WHERE source=? AND source_id=?
                    """,
                    (synced_at, source, source_id),
                )

        active_count = sum(
            1 for item in items if str(item.get("status") or "active").lower() == "active"
        )
        conn.execute(
            """
            INSERT INTO channel_sync_runs(source, synced_at, item_count, active_count, note)
            VALUES(?, ?, ?, ?, ?)
            """,
            (source, synced_at, len(seen), active_count, note),
        )

    return {
        "source": source,
        "synced_at": synced_at,
        "items": len(seen),
        "active": active_count,
    }


def record_vinted_items(items: list[dict[str, Any]], synced_at: float) -> dict[str, Any]:
    normalized = []
    for row in items:
        source_id = str(row.get("id") or "")
        if not source_id:
            continue
        normalized.append(
            {
                "source_id": source_id,
                "title": row.get("title"),
                "status": row.get("status") or "active",
                "quantity": 0 if str(row.get("status") or "").lower() == "sold" else 1,
                "price_cents": row.get("price_cents"),
                "currency": row.get("currency") or "EUR",
                "url": row.get("vinted_url"),
            }
        )
    return upsert_channel_snapshot(
        "vinted",
        normalized,
        synced_at=synced_at,
        full_snapshot=True,
        note="Chrome sync",
    )


_HEADER_ALIASES = {
    "sku": {"sku", "bookid", "book id", "book_id", "record number", "recordnumber", "inventory number", "item number"},
    "title": {"title"},
    "author": {"author"},
    "isbn": {"isbn", "isbn10", "isbn13", "isbn-10", "isbn-13"},
    "price": {"price", "asking price"},
    "description": {"description", "desc", "book description"},
    "status": {"status"},
    "quantity": {"quantity", "qty"},
}


def _header_key(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _detect_delimiter(text: str) -> str:
    sample = text[:10000]
    try:
        return csv.Sniffer().sniff(sample, delimiters="\t|,;").delimiter
    except csv.Error:
        counts = {sep: sample.count(sep) for sep in ("\t", "|", ",", ";")}
        return max(counts, key=counts.get)


def parse_biblio_inventory(text: str, *, currency: str = "EUR") -> list[dict[str, Any]]:
    if not text.strip():
        return []
    delimiter = _detect_delimiter(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValueError("BIBLIO file has no header row")

    fields: dict[str, str] = {}
    for original in reader.fieldnames:
        normalized = _header_key(original)
        for canonical, aliases in _HEADER_ALIASES.items():
            if normalized in aliases:
                fields[canonical] = original
                break

    missing = [field for field in ("sku", "title") if field not in fields]
    if missing:
        raise ValueError(
            "BIBLIO file is missing required column(s): " + ", ".join(missing)
        )

    rows: list[dict[str, Any]] = []
    for raw in reader:
        sku = str(raw.get(fields["sku"]) or "").strip()
        title = str(raw.get(fields["title"]) or "").strip()
        if not sku or not title:
            continue
        status_raw = str(raw.get(fields.get("status", "")) or "active").strip().lower()
        status = "active"
        if any(word in status_raw for word in ("sold", "inactive", "deleted", "removed")):
            status = "sold" if "sold" in status_raw else "inactive"

        quantity = _int(raw.get(fields.get("quantity", "")), 1)
        if quantity is not None and quantity <= 0 and status == "active":
            status = "inactive"

        rows.append(
            {
                "source_id": sku,
                "sku": sku,
                "isbn": raw.get(fields.get("isbn", "")),
                "title": title,
                "author": raw.get(fields.get("author", "")),
                "description": raw.get(fields.get("description", "")),
                "status": status,
                "quantity": quantity,
                "price_cents": _money(raw.get(fields.get("price", ""))),
                "currency": currency,
            }
        )
    return rows


def import_biblio_inventory(text: str, filename: str | None = None) -> dict[str, Any]:
    currency = os.getenv("BIBLIO_CURRENCY", "EUR").strip().upper() or "EUR"
    rows = parse_biblio_inventory(text, currency=currency)
    if not rows:
        raise ValueError("No BIBLIO inventory rows could be read")
    result = upsert_channel_snapshot(
        "biblio",
        rows,
        full_snapshot=True,
        note=f"Imported {filename or 'inventory file'}",
    )
    auto = maybe_auto_sync_biblio()
    if auto:
        result["ftp_sync"] = auto
    return result


def biblio_ftp_status() -> dict[str, Any]:
    host = os.getenv("BIBLIO_FTP_HOST", "ftp.biblio.com").strip() or "ftp.biblio.com"
    username = os.getenv("BIBLIO_FTP_USERNAME", "").strip()
    password = os.getenv("BIBLIO_FTP_PASSWORD", "").strip()
    auto_sync = os.getenv("BIBLIO_FTP_AUTO_SYNC", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }
    with _connect() as conn:
        latest = conn.execute(
            """
            SELECT attempted_at, action, status, inventory_filename, deletes_filename,
                   active_count, delete_count, detail
            FROM biblio_ftp_runs
            ORDER BY attempted_at DESC LIMIT 1
            """
        ).fetchone()
    return {
        "configured": bool(username and password),
        "host": host,
        "username": username or None,
        "auto_sync": auto_sync,
        "last_run": dict(latest) if latest else None,
    }


def _biblio_ftp_connect() -> ftplib.FTP:
    status = biblio_ftp_status()
    username = status.get("username")
    password = os.getenv("BIBLIO_FTP_PASSWORD", "").strip()
    if not username or not password:
        raise RuntimeError(
            "BIBLIO FTP is not configured. Set BIBLIO_FTP_USERNAME and "
            "BIBLIO_FTP_PASSWORD in the server .env."
        )
    timeout = _int(os.getenv("BIBLIO_FTP_TIMEOUT_SECONDS", "20"), 20) or 20
    ftp = ftplib.FTP()
    ftp.connect(str(status["host"]), timeout=timeout)
    ftp.login(str(username), password)
    ftp.set_pasv(True)
    directory = os.getenv("BIBLIO_FTP_DIRECTORY", "").strip()
    if directory and directory not in {".", "./"}:
        ftp.cwd(directory)
    return ftp


def test_biblio_ftp() -> dict[str, Any]:
    started = time.time()
    try:
        ftp = _biblio_ftp_connect()
        try:
            pwd = ftp.pwd()
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()
        detail = f"Connected successfully; directory {pwd}"
        _record_biblio_ftp_run("test", "success", detail=detail)
        return {"ok": True, "detail": detail, "elapsed_ms": round((time.time() - started) * 1000)}
    except Exception as exc:
        detail = str(exc)
        _record_biblio_ftp_run("test", "error", detail=detail)
        raise RuntimeError(detail) from exc


def _record_biblio_ftp_run(
    action: str,
    status: str,
    *,
    inventory_filename: str | None = None,
    deletes_filename: str | None = None,
    active_count: int = 0,
    delete_count: int = 0,
    detail: str | None = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO biblio_ftp_runs(
                attempted_at, action, status, inventory_filename, deletes_filename,
                active_count, delete_count, detail
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(), action, status, inventory_filename, deletes_filename,
                int(active_count), int(delete_count), detail,
            ),
        )


def _biblio_export_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT source_id, sku, isbn, title, author, description, status,
                   quantity, price_cents, currency, last_seen_at
            FROM channel_items
            WHERE source='biblio'
            ORDER BY source_id
            """
        ).fetchall()
        last_success = conn.execute(
            """
            SELECT MAX(attempted_at) AS attempted_at
            FROM biblio_ftp_runs
            WHERE action='sync' AND status='success'
            """
        ).fetchone()
    cutoff = float(last_success["attempted_at"] or 0) if last_success else 0
    active = [
        dict(row)
        for row in rows
        if row["status"] == "active" and int(row["quantity"] or 0) > 0
    ]
    inactive = [
        dict(row)
        for row in rows
        if (row["status"] != "active" or int(row["quantity"] or 0) <= 0)
        and float(row["last_seen_at"] or 0) > cutoff
    ]
    return active, inactive


def _biblio_required_missing(row: dict[str, Any]) -> list[str]:
    missing = []
    if not str(row.get("sku") or row.get("source_id") or "").strip():
        missing.append("SKU")
    if not str(row.get("title") or "").strip():
        missing.append("title")
    if not str(row.get("author") or "").strip():
        missing.append("author")
    if not str(row.get("description") or "").strip():
        missing.append("description")
    if row.get("price_cents") in (None, ""):
        missing.append("price")
    return missing


def _biblio_tsv(rows: list[dict[str, Any]], *, sold: bool = False) -> bytes:
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
                str(row.get("sku") or row.get("source_id") or ""),
                str(row.get("author") or ""),
                str(row.get("title") or ""),
                str(row.get("description") or ""),
                price,
                "sold" if sold else "for sale",
                str(row.get("isbn") or ""),
                0 if sold else max(1, int(row.get("quantity") or 1)),
            ]
        )
    return output.getvalue().encode("utf-8")


def preview_biblio_ftp_sync() -> dict[str, Any]:
    active, inactive = _biblio_export_rows()
    incomplete = []
    for row in active:
        missing = _biblio_required_missing(row)
        if missing:
            incomplete.append(
                {
                    "sku": row.get("sku") or row.get("source_id"),
                    "title": row.get("title"),
                    "missing": missing,
                }
            )
    return {
        "configured": biblio_ftp_status()["configured"],
        "auto_sync": biblio_ftp_status()["auto_sync"],
        "active_count": len(active),
        "delete_count": len(inactive),
        "ready": not incomplete,
        "incomplete": incomplete[:50],
    }


def sync_biblio_ftp(*, include_inventory: bool = True, include_deletes: bool = True) -> dict[str, Any]:
    active, inactive = _biblio_export_rows()
    if include_inventory:
        incomplete = [
            (row, _biblio_required_missing(row))
            for row in active
            if _biblio_required_missing(row)
        ]
        if incomplete:
            examples = ", ".join(
                f"{row.get('sku') or row.get('source_id')} ({'/'.join(missing)})"
                for row, missing in incomplete[:5]
            )
            raise RuntimeError(
                "BIBLIO active upload blocked: some listings are missing required "
                f"fields. Examples: {examples}. Import a full BIBLIO download first."
            )

    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    prefix = re.sub(
        r"[^A-Za-z0-9_-]+",
        "-",
        os.getenv("BIBLIO_FTP_FILENAME_PREFIX", "vinted-dashboard").strip()
        or "vinted-dashboard",
    ).strip("-")
    inventory_filename = f"{prefix}-{stamp}.txt" if include_inventory and active else None
    deletes_filename = (
        f"{prefix}-{stamp}-deletes.txt" if include_deletes and inactive else None
    )
    if not inventory_filename and not deletes_filename:
        detail = "Nothing to upload"
        _record_biblio_ftp_run("sync", "success", detail=detail)
        return {"ok": True, "detail": detail, "active": 0, "deletes": 0}

    try:
        ftp = _biblio_ftp_connect()
        try:
            if inventory_filename:
                ftp.storbinary(
                    f"STOR {inventory_filename}",
                    io.BytesIO(_biblio_tsv(active, sold=False)),
                )
            if deletes_filename:
                ftp.storbinary(
                    f"STOR {deletes_filename}",
                    io.BytesIO(_biblio_tsv(inactive, sold=True)),
                )
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()
    except Exception as exc:
        _record_biblio_ftp_run(
            "sync",
            "error",
            inventory_filename=inventory_filename,
            deletes_filename=deletes_filename,
            active_count=len(active) if inventory_filename else 0,
            delete_count=len(inactive) if deletes_filename else 0,
            detail=str(exc),
        )
        raise RuntimeError(str(exc)) from exc

    detail = "Uploaded to BIBLIO FTP"
    _record_biblio_ftp_run(
        "sync",
        "success",
        inventory_filename=inventory_filename,
        deletes_filename=deletes_filename,
        active_count=len(active) if inventory_filename else 0,
        delete_count=len(inactive) if deletes_filename else 0,
        detail=detail,
    )
    return {
        "ok": True,
        "detail": detail,
        "inventory_filename": inventory_filename,
        "deletes_filename": deletes_filename,
        "active": len(active) if inventory_filename else 0,
        "deletes": len(inactive) if deletes_filename else 0,
    }


def maybe_auto_sync_biblio() -> dict[str, Any] | None:
    status = biblio_ftp_status()
    if not status["configured"] or not status["auto_sync"]:
        return None
    return sync_biblio_ftp()


def _ebay_access_token() -> str:
    direct = os.getenv("EBAY_OAUTH_TOKEN", "").strip()
    if direct:
        return direct

    client_id = os.getenv("EBAY_CLIENT_ID", "").strip()
    client_secret = os.getenv("EBAY_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("EBAY_REFRESH_TOKEN", "").strip()
    if not (client_id and client_secret and refresh_token):
        raise RuntimeError(
            "eBay is not configured. Set EBAY_OAUTH_TOKEN, or EBAY_CLIENT_ID + "
            "EBAY_CLIENT_SECRET + EBAY_REFRESH_TOKEN."
        )

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    response = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=20,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"eBay OAuth refresh failed ({response.status_code})")
    payload = response.json()
    token = str(payload.get("access_token") or "")
    if not token:
        raise RuntimeError("eBay OAuth refresh returned no access token")
    return token


def _xml_text(node: ET.Element, name: str) -> str | None:
    child = node.find(f"{{urn:ebay:apis:eBLBaseComponents}}{name}")
    return child.text if child is not None else None


def sync_ebay_inventory() -> dict[str, Any]:
    token = _ebay_access_token()
    site_id = os.getenv("EBAY_SITE_ID", "0").strip() or "0"
    compatibility = os.getenv("EBAY_COMPATIBILITY_LEVEL", "1477").strip() or "1477"
    page = 1
    items: list[dict[str, Any]] = []

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

        page_items = root.findall(".//e:ActiveList/e:ItemArray/e:Item", ns)
        for node in page_items:
            item_id = node.findtext("e:ItemID", namespaces=ns)
            if not item_id:
                continue
            title = node.findtext("e:Title", namespaces=ns) or "Untitled"
            sku = node.findtext("e:SKU", namespaces=ns)
            quantity = _int(node.findtext("e:QuantityAvailable", namespaces=ns))
            if quantity is None:
                quantity = _int(node.findtext("e:Quantity", namespaces=ns), 1)
            price_node = node.find("e:SellingStatus/e:CurrentPrice", ns)
            if price_node is None:
                price_node = node.find("e:StartPrice", ns)
            price_cents = _money(price_node.text if price_node is not None else None)
            currency = price_node.attrib.get("currencyID") if price_node is not None else None
            view_url = node.findtext("e:ListingDetails/e:ViewItemURL", namespaces=ns)
            items.append(
                {
                    "source_id": item_id,
                    "sku": sku,
                    "title": title,
                    "status": "active",
                    "quantity": quantity,
                    "price_cents": price_cents,
                    "currency": currency,
                    "url": view_url,
                }
            )

        total_pages = _int(
            root.findtext(".//e:ActiveList/e:PaginationResult/e:TotalNumberOfPages", namespaces=ns),
            1,
        ) or 1
        if page >= total_pages:
            break
        page += 1

    return upsert_channel_snapshot(
        "ebay",
        items,
        full_snapshot=True,
        note="eBay GetMyeBaySelling",
    )


def channel_inventory_payload() -> dict[str, Any]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT source, source_id, sku, isbn, title, author, description, status, quantity,
                   price_cents, currency, url, first_seen_at, last_seen_at
            FROM channel_items
            ORDER BY source, status='active' DESC, title COLLATE NOCASE
            """
        ).fetchall()
        syncs = conn.execute(
            """
            SELECT csr.*
            FROM channel_sync_runs csr
            JOIN (
                SELECT source, MAX(synced_at) AS synced_at
                FROM channel_sync_runs GROUP BY source
            ) latest
              ON latest.source=csr.source AND latest.synced_at=csr.synced_at
            ORDER BY csr.source
            """
        ).fetchall()

    items = [dict(row) for row in rows]
    sources: dict[str, dict[str, Any]] = {}
    for item in items:
        source = str(item["source"])
        summary = sources.setdefault(
            source,
            {
                "source": source,
                "items": 0,
                "active": 0,
                "quantity": 0,
                "value_cents": 0,
                "currency": None,
            },
        )
        summary["items"] += 1
        if item["status"] == "active":
            summary["active"] += 1
            quantity = int(item["quantity"] or 0)
            summary["quantity"] += quantity
            if item["price_cents"] is not None:
                summary["value_cents"] += int(item["price_cents"]) * max(1, quantity)
                summary["currency"] = item["currency"] or summary["currency"]

    latest_sync = {str(row["source"]): dict(row) for row in syncs}
    for source, summary in sources.items():
        summary["last_sync"] = latest_sync.get(source)

    for source in ("vinted", "biblio", "ebay"):
        sources.setdefault(
            source,
            {
                "source": source,
                "items": 0,
                "active": 0,
                "quantity": 0,
                "value_cents": 0,
                "currency": None,
                "last_sync": latest_sync.get(source),
            },
        )

    return {
        "sources": [sources[key] for key in ("vinted", "biblio", "ebay")],
        "items": items,
        "configured": {
            "biblio": biblio_ftp_status()["configured"],
            "ebay": bool(
                os.getenv("EBAY_OAUTH_TOKEN", "").strip()
                or (
                    os.getenv("EBAY_CLIENT_ID", "").strip()
                    and os.getenv("EBAY_CLIENT_SECRET", "").strip()
                    and os.getenv("EBAY_REFRESH_TOKEN", "").strip()
                )
            ),
        },
        "biblio_ftp": biblio_ftp_status(),
    }
