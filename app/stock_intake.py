"""Stock-intake helpers for barcode scanning and book enrichment.

The browser may use its native BarcodeDetector where available, but camera
frames can always be decoded server-side so mobile Safari and other browsers
do not need BarcodeDetector support.
"""

from __future__ import annotations

from io import BytesIO
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from PIL import Image, UnidentifiedImageError
import zxingcpp


MAX_BARCODE_IMAGE_BYTES = 8 * 1024 * 1024
SUPPORTED_BARCODE_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}
OPEN_LIBRARY_BASE = "https://openlibrary.org"
OPEN_LIBRARY_USER_AGENT = "ResellerDashboard/1.0 (https://github.com/tpepels/vinted-dashboard)"


def normalize_barcode(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if re.fullmatch(r"[0-9Xx\s-]+", raw):
        return re.sub(r"[\s-]+", "", raw).upper()
    return raw


def _isbn10_valid(value: str) -> bool:
    if not re.fullmatch(r"\d{9}[\dX]", value):
        return False
    total = 0
    for index, char in enumerate(value):
        number = 10 if char == "X" else int(char)
        total += (10 - index) * number
    return total % 11 == 0


def _isbn13_valid(value: str) -> bool:
    if not re.fullmatch(r"\d{13}", value):
        return False
    total = sum(
        int(char) * (1 if index % 2 == 0 else 3)
        for index, char in enumerate(value[:12])
    )
    return (10 - total % 10) % 10 == int(value[-1])


def classify_barcode(value: Any) -> dict[str, Any]:
    code = normalize_barcode(value)
    upper = code.upper()
    if upper.startswith("RDLOC:"):
        location = code.split(":", 1)[1].strip()
        return {
            "code": code,
            "kind": "location",
            "location": location,
            "isbn": None,
        }
    if _isbn10_valid(upper):
        return {"code": upper, "kind": "isbn", "location": None, "isbn": upper}
    if _isbn13_valid(upper) and upper.startswith(("978", "979")):
        return {"code": upper, "kind": "isbn", "location": None, "isbn": upper}
    if re.fullmatch(r"\d{12}", upper):
        return {"code": upper, "kind": "upc", "location": None, "isbn": None}
    if re.fullmatch(r"\d{8}|\d{13}", upper):
        return {"code": upper, "kind": "ean", "location": None, "isbn": None}
    return {"code": code, "kind": "barcode", "location": None, "isbn": None}


def decode_barcode_image(content_type: str, body: bytes) -> list[dict[str, str]]:
    if content_type not in SUPPORTED_BARCODE_IMAGE_TYPES:
        raise ValueError("Barcode images must be JPEG, PNG or WebP")
    if not body:
        raise ValueError("Barcode image is empty")
    if len(body) > MAX_BARCODE_IMAGE_BYTES:
        raise ValueError("Barcode image must be 8 MB or smaller")
    try:
        with Image.open(BytesIO(body)) as image:
            image.load()
            decoded = zxingcpp.read_barcodes(image.convert("RGB"))
    except (UnidentifiedImageError, OSError, ValueError, TypeError) as exc:
        raise ValueError("Could not read the barcode image") from exc

    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for barcode in decoded:
        text = normalize_barcode(getattr(barcode, "text", ""))
        if not text or text in seen:
            continue
        seen.add(text)
        raw_format = str(getattr(barcode, "format", "") or "")
        result.append(
            {
                "code": text,
                "format": raw_format.rsplit(".", 1)[-1] or "unknown",
            }
        )
    return result


def _openlibrary_json(path: str) -> dict[str, Any] | None:
    request = urllib.request.Request(
        OPEN_LIBRARY_BASE + path,
        headers={
            "Accept": "application/json",
            "User-Agent": OPEN_LIBRARY_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(f"Open Library returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("Open Library lookup failed") from exc
    return payload if isinstance(payload, dict) else None


def _publication_year(value: Any) -> int | None:
    match = re.search(r"\b(1[5-9]\d{2}|20\d{2}|21\d{2})\b", str(value or ""))
    return int(match.group(1)) if match else None


def lookup_isbn(isbn: str) -> dict[str, Any] | None:
    normalized = normalize_barcode(isbn)
    if not (_isbn10_valid(normalized) or _isbn13_valid(normalized)):
        raise ValueError("ISBN checksum is invalid")
    edition = _openlibrary_json(
        "/isbn/" + urllib.parse.quote(normalized, safe="") + ".json"
    )
    if not edition:
        return None

    author_names: list[str] = []
    for row in edition.get("authors") or []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip()
        if not key.startswith("/authors/"):
            continue
        author = _openlibrary_json(key + ".json")
        name = str((author or {}).get("name") or "").strip()
        if name and name not in author_names:
            author_names.append(name)
        if len(author_names) >= 4:
            break

    publishers = [
        str(value).strip()
        for value in (edition.get("publishers") or [])
        if str(value or "").strip()
    ]
    cover_ids = [
        value for value in (edition.get("covers") or [])
        if str(value or "").strip()
    ]
    physical_format = str(edition.get("physical_format") or "").strip() or None
    edition_name = str(edition.get("edition_name") or "").strip() or None
    publish_date = str(edition.get("publish_date") or "").strip() or None
    return {
        "found": True,
        "source": "open_library",
        "isbn": normalized,
        "title": str(edition.get("title") or "").strip(),
        "subtitle": str(edition.get("subtitle") or "").strip() or None,
        "author": ", ".join(author_names) or None,
        "publisher": publishers[0] if publishers else None,
        "edition": edition_name or physical_format,
        "physical_format": physical_format,
        "publish_date": publish_date,
        "publication_year": _publication_year(publish_date),
        "number_of_pages": edition.get("number_of_pages"),
        "cover_url": (
            f"https://covers.openlibrary.org/b/id/{cover_ids[0]}-M.jpg"
            if cover_ids
            else None
        ),
        "source_url": f"https://openlibrary.org/isbn/{urllib.parse.quote(normalized, safe='')}",
    }
