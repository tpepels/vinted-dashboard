"""BIBLIO inventory file parsing.

Pure format logic lives here so product/API code does not depend on connector
transport or database persistence.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

_HEADER_ALIASES = {
    "sku": {
        "sku",
        "bookid",
        "book id",
        "book_id",
        "record number",
        "recordnumber",
        "inventory number",
        "item number",
    },
    "title": {"title"},
    "author": {"author"},
    "isbn": {"isbn", "isbn10", "isbn13", "isbn-10", "isbn-13"},
    "price": {"price", "asking price"},
    "description": {"description", "desc", "book description"},
    "status": {"status"},
    "quantity": {"quantity", "qty"},
    "publisher": {"publisher", "publishing house", "publishing_house"},
    "edition": {"edition", "edition statement", "edition_statement"},
    "condition": {"condition", "book condition", "book_condition"},
    "binding": {"binding", "format", "book format", "book_format"},
    "language": {"language", "lang"},
    "pages": {"pages", "page count", "page_count", "number of pages"},
    "publish_date": {
        "publication date", "publication_date", "publish date", "publish_date",
        "date published", "published",
    },
    "publication_year": {"publication year", "publication_year", "year published"},
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


def parse_biblio_inventory(
    text: str,
    *,
    currency: str = "EUR",
) -> list[dict[str, Any]]:
    """Parse a BIBLIO export into normalized channel rows."""

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

        status_raw = str(
            raw.get(fields.get("status", "")) or "active"
        ).strip().lower()
        status = "active"
        if any(
            word in status_raw
            for word in ("sold", "inactive", "deleted", "removed")
        ):
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
                "publisher": raw.get(fields.get("publisher", "")),
                "edition": raw.get(fields.get("edition", "")),
                "condition": raw.get(fields.get("condition", "")),
                "binding": raw.get(fields.get("binding", "")),
                "language": raw.get(fields.get("language", "")),
                "pages": _int(raw.get(fields.get("pages", ""))),
                "publish_date": raw.get(fields.get("publish_date", "")),
                "publication_year": _int(raw.get(fields.get("publication_year", ""))),
                "status": status,
                "quantity": quantity,
                "price_cents": _money(raw.get(fields.get("price", ""))),
                "currency": currency,
            }
        )
    return rows
