from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import db, entry, listing_assistant, models
from app.runtime_config import validate_configuration


def _register(client: TestClient, email: str, monkeypatch) -> str:
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "a-long-test-password",
            "workspace_name": "Quick listings",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


class _FakeResponse:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._body


def _structured_response(payload: dict) -> dict:
    return {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(payload),
                    }
                ],
            }
        ]
    }


def test_listing_assistant_status_is_manual_first_by_default(monkeypatch):
    monkeypatch.delenv("LISTING_ASSISTANT_ENABLED", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    status = listing_assistant.provider_status()
    assert status["enabled"] is False
    assert status["configured"] is False
    assert status["photo_retention"] == "not_stored"
    assert status["max_photos"] == 6


def test_photo_analysis_uses_structured_responses_without_storage(monkeypatch):
    monkeypatch.setenv("LISTING_ASSISTANT_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_VISION_MODEL", "gpt-6-luna")
    captured = {}

    result_payload = {
        "category": "clothing",
        "item_type": "jeans",
        "brand": "Example",
        "size": "32",
        "colour": "blue",
        "material": "denim",
        "condition": "light visible wear",
        "author": "",
        "isbn": "",
        "publisher": "",
        "edition": "",
        "suggested_title": "Example blue jeans size 32",
        "suggested_description": "Blue denim jeans with light visible wear.",
        "visible_text": ["EXAMPLE", "32"],
        "confidence_notes": ["Confirm measurements manually."],
    }

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(_structured_response(result_payload))

    monkeypatch.setattr(listing_assistant.urllib.request, "urlopen", fake_urlopen)

    result = listing_assistant.analyze_photos(
        [
            ("image/jpeg", b"first-photo"),
            ("image/png", b"second-photo"),
        ],
        hints={"category": "clothing"},
    )

    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["timeout"] == 60
    assert captured["payload"]["store"] is False
    assert captured["payload"]["model"] == "gpt-6-luna"
    assert captured["payload"]["text"]["format"]["type"] == "json_schema"
    image_parts = [
        part
        for part in captured["payload"]["input"][0]["content"]
        if part["type"] == "input_image"
    ]
    assert len(image_parts) == 2
    assert all(part["detail"] == "low" for part in image_parts)
    assert image_parts[0]["image_url"].startswith("data:image/jpeg;base64,")
    assert result["category"] == "clothing"
    assert result["item_type"] == "jeans"
    assert result["suggested_title"] == "Example blue jeans size 32"
    assert result["missing_fields"] == ["waist_cm", "inside_leg_cm", "price"]


def test_photo_validation_rejects_unsupported_or_excess_photos():
    try:
        listing_assistant.validate_photos([("image/gif", b"gif")])
    except ValueError as exc:
        assert "JPEG, PNG or WebP" in str(exc)
    else:
        raise AssertionError("GIF photo should have been rejected")

    too_many = [("image/jpeg", b"x")] * (listing_assistant.MAX_PHOTOS + 1)
    try:
        listing_assistant.validate_photos(too_many)
    except ValueError as exc:
        assert "at most" in str(exc)
    else:
        raise AssertionError("Too many photos should have been rejected")


def test_missing_fields_are_category_and_item_specific():
    assert listing_assistant.missing_fields(
        {"category": "clothing", "item_type": "jeans", "size": "32"}
    ) == ["waist_cm", "inside_leg_cm", "price"]
    assert listing_assistant.missing_fields(
        {"category": "clothing", "item_type": "jacket", "size": "M"}
    ) == ["pit_to_pit_cm", "length_cm", "price"]
    assert listing_assistant.missing_fields(
        {"category": "book", "item_type": "book", "isbn": ""}
    ) == ["isbn", "price"]
    assert listing_assistant.missing_fields(
        {"category": "book", "item_type": "book", "isbn": "9780000000000"}
    ) == ["price"]


def test_quick_listing_creates_master_item_but_no_marketplace_listing(monkeypatch):
    client = TestClient(entry.app)
    csrf = _register(client, "quick-listing@example.test", monkeypatch)

    response = client.post(
        "/api/app/listing-assistant/create",
        headers=_headers(csrf),
        json={
            "title": "Example blue jeans size 32",
            "description": "Blue denim jeans. Waist 42 cm flat. Inside leg 78 cm.",
            "category": "clothing",
            "item_type": "jeans",
            "brand": "Example",
            "size": "32",
            "colour": "blue",
            "material": "denim",
            "isbn": "stale-wrong-category-value",
            "condition": "good visible condition",
            "waist_cm": "42",
            "inside_leg_cm": "78",
            "price_cents": 2500,
            "cost_cents": 500,
            "currency": "eur",
            "location": "Rack A",
            "photo_count": 4,
            "analysis_used": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    item = body["item"]
    package = body["listing_package"]

    assert item["sku"].startswith("CL-")
    assert item["category"] == "clothing"
    assert item["cost_cents"] == 500
    assert item["default_price_cents"] == 2500
    assert package["title"] == "Example blue jeans size 32"
    assert package["price_cents"] == 2500
    assert package["photo_count"] == 4
    assert package["publishing"]["mode"] == "manual_handoff"
    assert package["publishing"]["automated"] is False

    with db.session_scope() as session:
        inventory = session.execute(select(models.InventoryItem)).scalars().all()
        assert len(inventory) == 1
        stored = inventory[0]
        assert stored.attributes["listing_creation_source"] == "photo_ai"
        assert stored.attributes["listing_description"].startswith("Blue denim jeans")
        assert stored.attributes["waist_cm"] == "42"
        assert "isbn" not in stored.attributes
        listing_count = session.execute(
            select(func.count(models.ChannelListing.id))
        ).scalar_one()
        assert listing_count == 0


def test_manual_quick_listing_works_when_photo_analysis_is_disabled(monkeypatch):
    monkeypatch.setenv("LISTING_ASSISTANT_ENABLED", "false")
    client = TestClient(entry.app)
    csrf = _register(client, "manual-quick@example.test", monkeypatch)

    status = client.get("/api/app/listing-assistant/status")
    assert status.status_code == 200
    assert status.json()["configured"] is False
    assert status.json()["manual_workflow_available"] is True

    response = client.post(
        "/api/app/listing-assistant/create",
        headers=_headers(csrf),
        json={
            "title": "Manual item",
            "description": "Prepared manually.",
            "category": "general",
            "price_cents": 1200,
            "currency": "EUR",
            "analysis_used": False,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["item"]["sku"].startswith("GN-")


def test_analysis_endpoint_is_explicit_and_rejects_disabled_provider(monkeypatch):
    monkeypatch.setenv("LISTING_ASSISTANT_ENABLED", "false")
    client = TestClient(entry.app)
    csrf = _register(client, "disabled-ai@example.test", monkeypatch)
    response = client.post(
        "/api/app/listing-assistant/analyze",
        headers=_headers(csrf),
        files={"photos": ("item.jpg", b"photo", "image/jpeg")},
        data={"hints_json": json.dumps({"category": "clothing"})},
    )
    assert response.status_code == 503
    assert "not enabled" in response.json()["detail"]


def test_production_requires_key_only_when_listing_assistant_is_enabled():
    base = {
        "APP_ENV": "production",
        "PUBLIC_APP_URL": "https://reseller.example",
        "COOKIE_SECURE": "true",
        "APP_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
        "LEGACY_UI_ENABLED": "false",
        "LEGACY_API_ENABLED": "false",
        "LEGACY_COMPAT_SYNC": "false",
        "EXTENSION_MARKET_RESEARCH_ENABLED": "false",
        "PASSWORD_HASH_ITERATIONS": "310000",
        "AUTH_SESSION_DAYS": "30",
        "BILLING_ENABLED": "false",
        "LISTING_ASSISTANT_ENABLED": "false",
    }
    disabled_errors = validate_configuration(
        base,
        database_url="postgresql://user:pass@db/app",
    )
    assert not any("OPENAI" in error for error in disabled_errors)

    enabled = dict(base)
    enabled["LISTING_ASSISTANT_ENABLED"] = "true"
    enabled_errors = validate_configuration(
        enabled,
        database_url="postgresql://user:pass@db/app",
    )
    assert any("OPENAI_API_KEY" in error for error in enabled_errors)

    enabled["OPENAI_API_KEY"] = "secret"
    enabled["OPENAI_VISION_MODEL"] = "gpt-6-luna"
    configured_errors = validate_configuration(
        enabled,
        database_url="postgresql://user:pass@db/app",
    )
    assert not any("OPENAI" in error for error in configured_errors)



def test_listing_assistant_accepts_expanded_general_categories():
    result = listing_assistant.normalize_analysis(
        {
            "category": "electronics",
            "item_type": "camera",
            "brand": "Nikon",
            "size": "",
            "colour": "black",
            "material": "metal",
            "condition": "used",
            "author": "",
            "isbn": "",
            "publisher": "",
            "edition": "",
            "suggested_title": "Nikon camera",
            "suggested_description": "Black Nikon camera.",
            "visible_text": [],
            "confidence_notes": [],
        }
    )
    assert result["category"] == "electronics"
    assert result["missing_fields"] == ["price"]
