from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
import zxingcpp

from app import db, entry, models, stock_intake


def _registered_client(monkeypatch, email: str = "scanner@example.test"):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "a-long-test-password",
            "workspace_name": "Scanner",
        },
    )
    assert registered.status_code == 200, registered.text
    return client, registered.json()["csrf_token"]


def test_barcode_classifier_recognizes_isbn_and_location_command():
    isbn = stock_intake.classify_barcode("978-0-14-032872-1")
    assert isbn == {
        "code": "9780140328721",
        "kind": "isbn",
        "location": None,
        "isbn": "9780140328721",
    }

    location = stock_intake.classify_barcode("RDLOC:BOX-17")
    assert location["kind"] == "location"
    assert location["location"] == "BOX-17"


def test_barcode_decoder_reads_real_ean13_image():
    barcode = zxingcpp.create_barcode(
        "9780140328721",
        zxingcpp.BarcodeFormat.EAN13,
    )
    image = Image.fromarray(barcode.to_image(scale=4))
    body = BytesIO()
    image.save(body, format="PNG")

    decoded = stock_intake.decode_barcode_image("image/png", body.getvalue())
    assert decoded
    assert decoded[0]["code"] == "9780140328721"


def test_open_library_lookup_extracts_edition_and_author(monkeypatch):
    def fake_json(path: str):
        if path == "/isbn/9780140328721.json":
            return {
                "title": "Fantastic Mr. Fox",
                "authors": [{"key": "/authors/OL34184A"}],
                "publishers": ["Puffin"],
                "publish_date": "1988",
                "physical_format": "Paperback",
                "covers": [12345],
                "number_of_pages": 96,
            }
        if path == "/authors/OL34184A.json":
            return {"name": "Roald Dahl"}
        raise AssertionError(path)

    monkeypatch.setattr(stock_intake, "_openlibrary_json", fake_json)
    result = stock_intake.lookup_isbn("9780140328721")

    assert result["title"] == "Fantastic Mr. Fox"
    assert result["author"] == "Roald Dahl"
    assert result["publisher"] == "Puffin"
    assert result["edition"] == "Paperback"
    assert result["publication_year"] == 1988
    assert result["cover_url"].endswith("/12345-M.jpg")


def test_batch_scan_creates_separate_physical_copies(monkeypatch):
    client, csrf = _registered_client(monkeypatch)
    payload = {
        "items": [
            {
                "barcode": "9780140328721",
                "barcode_format": "EAN13",
                "title": "Fantastic Mr. Fox",
                "category": "book",
                "condition": "Good",
                "cost_cents": 100,
                "price_cents": 500,
                "currency": "EUR",
                "location": "BOX-17",
                "author": "Roald Dahl",
                "isbn": "9780140328721",
                "publisher": "Puffin",
                "edition": "Paperback",
                "publication_year": 1988,
            },
            {
                "barcode": "9780140328721",
                "barcode_format": "EAN13",
                "title": "Fantastic Mr. Fox",
                "category": "book",
                "condition": "Very good",
                "cost_cents": 150,
                "price_cents": 600,
                "currency": "EUR",
                "location": "BOX-18",
                "author": "Roald Dahl",
                "isbn": "9780140328721",
                "publisher": "Puffin",
                "edition": "Paperback",
                "publication_year": 1988,
            },
        ]
    }
    response = client.post(
        "/api/app/stock-intake/items",
        headers={"X-CSRF-Token": csrf},
        json=payload,
    )
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 2
    assert response.json()["created"][0]["sku"] != response.json()["created"][1]["sku"]

    with db.session_scope() as session:
        rows = session.execute(
            select(models.InventoryItem).where(
                models.InventoryItem.category == "book",
            )
        ).scalars().all()
        assert len(rows) == 2
        assert {row.location for row in rows} == {"BOX-17", "BOX-18"}
        assert {row.quantity for row in rows} == {1}
        assert all(row.attributes["isbn"] == "9780140328721" for row in rows)
        assert all(row.attributes["stock_intake_source"] == "barcode_scan" for row in rows)
        assert {row.attributes["default_price_cents"] for row in rows} == {500, 600}


def test_barcode_lookup_reports_existing_copies(monkeypatch):
    client, csrf = _registered_client(monkeypatch, "copies@example.test")

    create = client.post(
        "/api/app/stock-intake/items",
        headers={"X-CSRF-Token": csrf},
        json={
            "items": [
                {
                    "barcode": "9780140328721",
                    "title": "Fantastic Mr. Fox",
                    "category": "book",
                    "isbn": "9780140328721",
                    "currency": "EUR",
                },
                {
                    "barcode": "9780140328721",
                    "title": "Fantastic Mr. Fox",
                    "category": "book",
                    "isbn": "9780140328721",
                    "currency": "EUR",
                },
            ]
        },
    )
    assert create.status_code == 200

    monkeypatch.setattr(
        stock_intake,
        "lookup_isbn",
        lambda isbn: {
            "found": True,
            "isbn": isbn,
            "title": "Fantastic Mr. Fox",
            "author": "Roald Dahl",
        },
    )
    lookup = client.post(
        "/api/app/stock-intake/barcode/lookup",
        headers={"X-CSRF-Token": csrf},
        json={"code": "9780140328721"},
    )
    assert lookup.status_code == 200, lookup.text
    data = lookup.json()
    assert data["kind"] == "isbn"
    assert data["existing_copy_count"] == 2
    assert len(data["existing_copies"]) == 2


def test_location_qr_lookup_does_not_create_inventory(monkeypatch):
    client, csrf = _registered_client(monkeypatch, "location@example.test")
    response = client.post(
        "/api/app/stock-intake/barcode/lookup",
        headers={"X-CSRF-Token": csrf},
        json={"code": "RDLOC:SHELF-B04"},
    )
    assert response.status_code == 200
    assert response.json()["kind"] == "location"
    assert response.json()["location"] == "SHELF-B04"

    with db.session_scope() as session:
        assert session.execute(select(models.InventoryItem)).scalars().all() == []



def test_stock_intake_keeps_all_isbn_lookup_metadata(monkeypatch):
    client, csrf = _registered_client(monkeypatch, "rich-scan@example.test")
    response = client.post(
        "/api/app/stock-intake/items",
        headers={"X-CSRF-Token": csrf},
        json={
            "items": [{
                "barcode": "9780140328721",
                "barcode_format": "EAN13",
                "title": "Fantastic Mr. Fox",
                "category": "book",
                "currency": "EUR",
                "author": "Roald Dahl",
                "isbn": "9780140328721",
                "subtitle": "A Story",
                "publisher": "Puffin",
                "edition": "Revised",
                "binding": "Paperback",
                "language": "English",
                "publish_date": "1988",
                "publication_year": 1988,
                "pages": 96,
                "cover_url": "https://covers.example/fox.jpg",
                "source_url": "https://openlibrary.org/isbn/9780140328721",
            }]
        },
    )
    assert response.status_code == 200, response.text

    with db.session_scope() as session:
        item = session.execute(select(models.InventoryItem)).scalar_one()
        attrs = item.attributes
        assert attrs["barcode"] == "9780140328721"
        assert attrs["isbn"] == "9780140328721"
        assert attrs["subtitle"] == "A Story"
        assert attrs["publisher"] == "Puffin"
        assert attrs["edition"] == "Revised"
        assert attrs["binding"] == "Paperback"
        assert attrs["language"] == "English"
        assert attrs["publish_date"] == "1988"
        assert attrs["publication_year"] == 1988
        assert attrs["pages"] == 96
        assert attrs["cover_url"] == "https://covers.example/fox.jpg"
