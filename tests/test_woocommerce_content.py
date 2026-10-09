"""WooCommerce selective listing-content updates preserve non-content state."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.connectors import hosted, woocommerce_content


def product(*, title="Old title", description="Old description",
            sku="COPY-10", modified="2026-10-09T09:00:00",
            extra=None):
    return {
        "id": 10, "type": "simple", "sku": sku,
        "name": title, "description": description,
        "date_modified_gmt": modified,
        "regular_price": "9.00", "stock_quantity": 2,
        "status": "publish", "images": [{"id": 4}],
        "attributes": [
            {"name": "Author", "options": ["A writer"]},
            {"name": "Condition", "options": ["Good"]},
        ],
        "global_unique_id": "9781111111111",
        **(extra or {}),
    }


def variant(*, description="Old description", sku="COPY-10"):
    return {
        "id": 7, "sku": sku, "type": "variation",
        "description": description, "date_modified_gmt": "2026-10-09T09:00:00",
    }


def mocks(monkeypatch, *records, variation=False):
    calls = []
    queue = iter(records)
    monkeypatch.setattr(hosted, "_credentials", lambda *args: {
        "store_url": "https://store.example", "consumer_key": "test",
        "consumer_secret": "secret",
    })
    path = "products/10/variations/7" if variation else "products/10"
    monkeypatch.setattr(hosted, "_woocommerce_stock_record",lambda *args:
        (path, next(queue), "publish", variation))
    monkeypatch.setattr(hosted, "_woocommerce_base", lambda *args: "https://store.example")
    monkeypatch.setattr(hosted, "_woocommerce_headers", lambda *args: {})
    def put(url, *, headers, json, timeout):
        calls.append((url, json))
        return SimpleNamespace(status_code=200)
    monkeypatch.setattr(woocommerce_content.requests, "put", put)
    return calls


def test_live_read_provides_readonly_book_details(monkeypatch):
    calls = mocks(monkeypatch, product())
    data = woocommerce_content.read_woocommerce_workspace_content(
        uuid4(), external_id="10", expected_sku="COPY-10",
    )
    assert data["fields"]["title"] == "Old title"
    assert data["fields"]["isbn"] == "9781111111111"
    assert data["fields"]["condition"] == "Good"
    assert data["fields"]["author"] == "A writer"
    assert data["writable_fields"] == ["title", "description"]
    assert not calls


def test_selective_put_omits_prices_stock_images_identifiers(monkeypatch):
    before = product()
    calls = mocks(monkeypatch, before, before, product(title="Updated"))
    snap = woocommerce_content.read_woocommerce_workspace_content(
        uuid4(), external_id="10", expected_sku="COPY-10",
    )
    result = woocommerce_content.update_woocommerce_workspace_content(
        uuid4(), external_id="10", expected_sku="COPY-10",
        expected_fingerprint=snap["fingerprint"], changes={"title": "Updated"},
    )
    assert result["remote_verified"] and not result["already_complete"]
    assert calls == [
        ("https://store.example/wp-json/wc/v3/products/10", {"name": "Updated"})
    ]


def test_variation_allows_only_description(monkeypatch):
    calls = mocks(monkeypatch, variant(), variant(), variant(description="Changed"),
                  variation=True)
    snap = woocommerce_content.read_woocommerce_workspace_content(
        uuid4(), external_id="10:7", expected_sku="COPY-10",
    )
    assert snap["writable_fields"] == ["description"]
    with pytest.raises(ValueError, match="does not support"):
        woocommerce_content.update_woocommerce_workspace_content(
            uuid4(), external_id="10:7", expected_sku="COPY-10",
            expected_fingerprint=snap["fingerprint"], changes={"title": "New"},
        )
    # The preflight consumed a remote read, but never wrote.
    assert not calls


def test_changed_remote_description_blocks_stale_write(monkeypatch):
    calls = mocks(monkeypatch, product(), product(description="Changed remotely"))
    snap = woocommerce_content.read_woocommerce_workspace_content(
        uuid4(), external_id="10", expected_sku="COPY-10",
    )
    with pytest.raises(ValueError, match="changed since the last check"):
        woocommerce_content.update_woocommerce_workspace_content(
            uuid4(), external_id="10", expected_sku="COPY-10",
            expected_fingerprint=snap["fingerprint"], changes={"title": "New"},
        )
    assert not calls


def test_uncertain_readback_is_never_verified(monkeypatch):
    calls = mocks(monkeypatch, product(), product(), product())
    snap = woocommerce_content.read_woocommerce_workspace_content(
        uuid4(), external_id="10", expected_sku="COPY-10",
    )
    with pytest.raises(RuntimeError, match="differs after update"):
        woocommerce_content.update_woocommerce_workspace_content(
            uuid4(), external_id="10", expected_sku="COPY-10",
            expected_fingerprint=snap["fingerprint"],
            changes={"description": "Updated"},
        )
    assert len(calls) == 1


@pytest.mark.parametrize("changes", [
    {}, {"isbn": "9780000000000"}, {"price": "0.00"},
    {"description": ""}, {"title": "X" * 501},
])
def test_forbidden_or_invalid_fields_do_not_write(monkeypatch, changes):
    calls = mocks(monkeypatch, product())
    with pytest.raises(ValueError):
        woocommerce_content.update_woocommerce_workspace_content(
            uuid4(), external_id="10", expected_sku="COPY-10",
            expected_fingerprint="known", changes=changes,
        )
    assert not calls
