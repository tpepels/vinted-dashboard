"""Fast listing creation from photos, with optional server-side vision analysis.

Photos are accepted only for the duration of one analysis request. The
application does not persist them. When enabled, the OpenAI Responses API is
used with store=false and Structured Outputs so browser code remains a thin
UI layer and the server owns the listing workflow.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import os
import secrets
import urllib.error
import urllib.request
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.constants import ItemCategory, ItemStatus


SUPPORTED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}
MAX_PHOTOS = 6
MAX_PHOTO_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 30 * 1024 * 1024


def enabled() -> bool:
    return os.getenv("LISTING_ASSISTANT_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def provider_status() -> dict[str, Any]:
    configured = enabled() and bool(os.getenv("OPENAI_API_KEY", "").strip())
    return {
        "enabled": enabled(),
        "configured": configured,
        "provider": "openai" if enabled() else None,
        "model": (
            os.getenv("OPENAI_VISION_MODEL", "gpt-6-luna").strip()
            if enabled()
            else None
        ),
        "max_photos": MAX_PHOTOS,
        "max_photo_bytes": MAX_PHOTO_BYTES,
        "accepted_types": sorted(SUPPORTED_IMAGE_TYPES),
        "photo_retention": "not_stored",
    }


def validate_photos(
    photos: list[tuple[str, bytes]],
) -> None:
    if not photos:
        raise ValueError("Choose at least one photo")
    if len(photos) > MAX_PHOTOS:
        raise ValueError(f"Use at most {MAX_PHOTOS} photos")
    total = 0
    for content_type, body in photos:
        if content_type not in SUPPORTED_IMAGE_TYPES:
            raise ValueError("Photos must be JPEG, PNG or WebP")
        if not body:
            raise ValueError("One of the selected photos is empty")
        if len(body) > MAX_PHOTO_BYTES:
            raise ValueError("Each photo must be 8 MB or smaller")
        total += len(body)
    if total > MAX_TOTAL_BYTES:
        raise ValueError("Selected photos exceed the 30 MB request limit")


def _schema() -> dict[str, Any]:
    string = {"type": "string"}
    string_array = {"type": "array", "items": {"type": "string"}}
    properties = {
        "category": {
            "type": "string",
            "enum": [
                ItemCategory.CLOTHING,
                ItemCategory.BOOK,
                ItemCategory.GENERAL,
            ],
        },
        "item_type": string,
        "brand": string,
        "size": string,
        "colour": string,
        "material": string,
        "condition": string,
        "author": string,
        "isbn": string,
        "publisher": string,
        "edition": string,
        "suggested_title": string,
        "suggested_description": string,
        "visible_text": string_array,
        "confidence_notes": string_array,
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _prompt(hints: dict[str, Any]) -> str:
    category_hint = str(hints.get("category") or "").strip()
    return (
        "Analyze these reseller listing photos. Return only evidence supported "
        "by the images. Never invent a brand, size, material, edition, ISBN, "
        "condition detail or defect. Use an empty string when a field cannot "
        "be established. category must be clothing, book, or general. "
        "item_type should be a short literal noun such as jeans, jacket, book, "
        "shoes, mug. For condition, describe only visible condition in plain "
        "language; do not map it to a marketplace condition grade. "
        "suggested_title should be concise and factual. suggested_description "
        "should be ready to edit/paste, factual, and must not mention AI. "
        "Do not include a price. visible_text should contain useful text you "
        "can actually read from labels/covers. confidence_notes should briefly "
        "flag uncertainty or which fields need user confirmation."
        + (
            f" The user supplied category hint is {category_hint!r}; treat it "
            "as a hint, not as evidence."
            if category_hint
            else ""
        )
    )


def _extract_output_text(response: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if (
                isinstance(content, dict)
                and content.get("type") == "output_text"
                and isinstance(content.get("text"), str)
            ):
                parts.append(content["text"])
    if not parts:
        raise RuntimeError("Photo analysis returned no structured output")
    return "".join(parts)


def analyze_photos(
    photos: list[tuple[str, bytes]],
    *,
    hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_photos(photos)
    if not enabled():
        raise RuntimeError("Photo analysis is not enabled")
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Photo analysis is enabled but OPENAI_API_KEY is not configured")

    content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": _prompt(dict(hints or {})),
        }
    ]
    for content_type, body in photos:
        encoded = base64.b64encode(body).decode("ascii")
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:{content_type};base64,{encoded}",
                "detail": "low",
            }
        )

    payload = {
        "model": os.getenv("OPENAI_VISION_MODEL", "gpt-6-luna").strip()
        or "gpt-6-luna",
        "store": False,
        "input": [
            {
                "role": "user",
                "content": content,
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "reseller_listing_photo_analysis",
                "strict": True,
                "schema": _schema(),
            }
        },
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Photo analysis provider returned HTTP {exc.code}: {detail[:500]}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("Photo analysis provider request failed") from exc

    try:
        result = json.loads(_extract_output_text(raw))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Photo analysis returned invalid structured output") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Photo analysis returned an invalid result")
    return normalize_analysis(result)


def normalize_analysis(result: dict[str, Any]) -> dict[str, Any]:
    category = str(result.get("category") or ItemCategory.GENERAL).strip().lower()
    if category not in {
        ItemCategory.CLOTHING,
        ItemCategory.BOOK,
        ItemCategory.GENERAL,
    }:
        category = ItemCategory.GENERAL
    normalized = {
        "category": category,
        "item_type": str(result.get("item_type") or "").strip(),
        "brand": str(result.get("brand") or "").strip(),
        "size": str(result.get("size") or "").strip(),
        "colour": str(result.get("colour") or "").strip(),
        "material": str(result.get("material") or "").strip(),
        "condition": str(result.get("condition") or "").strip(),
        "author": str(result.get("author") or "").strip(),
        "isbn": str(result.get("isbn") or "").strip(),
        "publisher": str(result.get("publisher") or "").strip(),
        "edition": str(result.get("edition") or "").strip(),
        "suggested_title": str(result.get("suggested_title") or "").strip(),
        "suggested_description": str(result.get("suggested_description") or "").strip(),
        "visible_text": [
            str(value).strip()
            for value in (result.get("visible_text") or [])
            if str(value).strip()
        ][:20],
        "confidence_notes": [
            str(value).strip()
            for value in (result.get("confidence_notes") or [])
            if str(value).strip()
        ][:20],
    }
    normalized["missing_fields"] = missing_fields(normalized)
    return normalized


def missing_fields(values: dict[str, Any]) -> list[str]:
    category = str(values.get("category") or ItemCategory.GENERAL)
    item_type = str(values.get("item_type") or "").strip().lower()
    missing: list[str] = []

    if category == ItemCategory.CLOTHING:
        if not str(values.get("size") or "").strip() and item_type in {
            "shoes",
            "boots",
            "trainers",
            "sneakers",
        }:
            missing.append("size")
        if item_type in {"jeans", "trousers", "pants", "shorts"}:
            missing.extend(["waist_cm", "inside_leg_cm"])
        elif item_type in {
            "shirt",
            "blouse",
            "top",
            "t-shirt",
            "tee",
            "sweater",
            "jumper",
            "jacket",
            "coat",
            "dress",
        }:
            missing.extend(["pit_to_pit_cm", "length_cm"])
        elif item_type not in {"shoes", "boots", "trainers", "sneakers"}:
            missing.append("measurements")
    elif category == ItemCategory.BOOK:
        if not str(values.get("isbn") or "").strip():
            missing.append("isbn")

    missing.append("price")
    return list(dict.fromkeys(missing))


def auto_sku(
    session: Session,
    workspace_id: uuid.UUID,
    category: str,
    *,
    now: datetime | None = None,
) -> str:
    current = now or datetime.now(timezone.utc)
    prefix = {
        ItemCategory.CLOTHING: "CL",
        ItemCategory.BOOK: "BK",
        ItemCategory.GENERAL: "GN",
    }.get(category, "GN")
    stamp = current.strftime("%y%m%d")
    for _ in range(20):
        candidate = f"{prefix}-{stamp}-{secrets.token_hex(2).upper()}"
        exists = session.execute(
            select(models.InventoryItem.id).where(
                models.InventoryItem.workspace_id == workspace_id,
                models.InventoryItem.sku == candidate,
            )
        ).scalar_one_or_none()
        if not exists:
            return candidate
    raise RuntimeError("Could not allocate a unique inventory SKU")


def create_master_item(
    session: Session,
    workspace_id: uuid.UUID,
    values: dict[str, Any],
) -> models.InventoryItem:
    title = str(values.get("title") or "").strip()
    description = str(values.get("description") or "").strip()
    category = str(values.get("category") or ItemCategory.GENERAL).strip().lower()
    if category not in {
        ItemCategory.CLOTHING,
        ItemCategory.BOOK,
        ItemCategory.GENERAL,
    }:
        raise ValueError("Unknown item category")
    if not title:
        raise ValueError("Title is required")
    price_cents = int(values.get("price_cents") or 0)
    if price_cents <= 0:
        raise ValueError("Asking price must be greater than zero")
    cost_cents = values.get("cost_cents")
    if cost_cents is not None and int(cost_cents) < 0:
        raise ValueError("Cost price cannot be negative")
    currency = str(values.get("currency") or "EUR").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("Currency must be a 3-letter code")

    sku = str(values.get("sku") or "").strip() or auto_sku(
        session,
        workspace_id,
        category,
    )
    exists = session.execute(
        select(models.InventoryItem.id).where(
            models.InventoryItem.workspace_id == workspace_id,
            models.InventoryItem.sku == sku,
        )
    ).scalar_one_or_none()
    if exists:
        raise ValueError("SKU already exists")

    attribute_keys = ["item_type"]
    if category == ItemCategory.CLOTHING:
        attribute_keys.extend(
            [
                "brand",
                "size",
                "colour",
                "material",
                "measurements",
                "waist_cm",
                "inside_leg_cm",
                "pit_to_pit_cm",
                "length_cm",
            ]
        )
    elif category == ItemCategory.BOOK:
        attribute_keys.extend(
            [
                "author",
                "isbn",
                "publisher",
                "edition",
            ]
        )
    attributes = {
        key: values[key]
        for key in attribute_keys
        if values.get(key) not in (None, "")
    }
    attributes["default_price_cents"] = price_cents
    attributes["listing_title"] = title
    attributes["listing_description"] = description
    attributes["listing_creation_source"] = str(
        values.get("listing_creation_source") or "quick_listing"
    )
    attributes["photo_count"] = max(0, int(values.get("photo_count") or 0))

    item = models.InventoryItem(
        workspace_id=workspace_id,
        sku=sku,
        title=title,
        category=category,
        quantity=1,
        condition=str(values.get("condition") or "").strip() or None,
        cost_cents=int(cost_cents) if cost_cents is not None else None,
        currency=currency,
        location=str(values.get("location") or "").strip() or None,
        notes=str(values.get("notes") or "").strip() or None,
        status=ItemStatus.ACTIVE,
        attributes=attributes,
    )
    session.add(item)
    session.flush()
    return item


def listing_package(item: models.InventoryItem) -> dict[str, Any]:
    attributes = dict(item.attributes or {})
    return {
        "inventory_item_id": str(item.id),
        "sku": item.sku,
        "title": str(attributes.get("listing_title") or item.title),
        "description": str(attributes.get("listing_description") or ""),
        "price_cents": attributes.get("default_price_cents"),
        "currency": item.currency,
        "photo_count": int(attributes.get("photo_count") or 0),
        "publishing": {
            "mode": "manual_handoff",
            "automated": False,
            "instruction": (
                "Copy the prepared title/description and upload the selected "
                "photos in Vinted yourself. The dashboard does not publish, "
                "relist, message or like on your behalf."
            ),
        },
    }
