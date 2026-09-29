from __future__ import annotations

import base64
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
                    source, source_id, sku, isbn, title, author, status, quantity,
                    price_cents, currency, url, first_seen_at, last_seen_at, raw_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, source_id) DO UPDATE SET
                    sku=excluded.sku,
                    isbn=excluded.isbn,
                    title=excluded.title,
                    author=excluded.author,
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
    return upsert_channel_snapshot(
        "biblio",
        rows,
        full_snapshot=True,
        note=f"Imported {filename or 'inventory file'}",
    )


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
            SELECT source, source_id, sku, isbn, title, author, status, quantity,
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
            "biblio": True,
            "ebay": bool(
                os.getenv("EBAY_OAUTH_TOKEN", "").strip()
                or (
                    os.getenv("EBAY_CLIENT_ID", "").strip()
                    and os.getenv("EBAY_CLIENT_SECRET", "").strip()
                    and os.getenv("EBAY_REFRESH_TOKEN", "").strip()
                )
            ),
        },
    }
