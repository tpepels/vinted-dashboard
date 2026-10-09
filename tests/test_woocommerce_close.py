"""WooCommerce sold-out unpublish never changes stock or deletes products."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.connectors import hosted, woocommerce_close


def _product(status="publish", sku="PH-1", modified="2026-10-09T08:00:00"):
    return {
        "id": 12, "type": "simple", "sku": sku, "status": status,
        "name": "One book", "date_modified_gmt": modified,
        "regular_price": "12.00", "stock_quantity": 1,
    }


def _stub(monkeypatch, *records, variation=False):
    snapshots = iter(records)
    puts = []
    monkeypatch.setattr(hosted, "_credentials", lambda *_: {"store_url": "https://store.test"})
    monkeypatch.setattr(hosted, "_woocommerce_stock_record", lambda *args: (
        "products/12/variations/3" if variation else "products/12",
        next(snapshots), "publish", variation,
    ))
    monkeypatch.setattr(hosted, "_woocommerce_base", lambda *_: "https://store.test")
    monkeypatch.setattr(hosted, "_woocommerce_headers", lambda *_: {})
    def put(url, *, headers, json, timeout):
        puts.append((url, json))
        return SimpleNamespace(status_code=200)
    monkeypatch.setattr(woocommerce_close.requests, "put", put)
    return puts


def test_publication_check_has_no_write(monkeypatch):
    puts = _stub(monkeypatch, _product())
    result = woocommerce_close.read_woocommerce_workspace_publication(
        uuid4(), external_id="12", expected_sku="PH-1",
    )
    assert result["status"] == "publish" and result["can_unpublish"]
    assert result["fingerprint"] and not puts


def test_status_only_put_and_verified_draft(monkeypatch):
    puts = _stub(monkeypatch, _product(), _product(), _product(status="draft"))
    checked = woocommerce_close.read_woocommerce_workspace_publication(
        uuid4(), external_id="12", expected_sku="PH-1",
    )
    result = woocommerce_close.unpublish_woocommerce_workspace_product(
        uuid4(), external_id="12", expected_sku="PH-1",
        expected_fingerprint=checked["fingerprint"],
    )
    assert result["remote_verified"] and result["status"] == "draft"
    assert puts == [("https://store.test/wp-json/wc/v3/products/12", {"status": "draft"})]


def test_changed_remote_fingerprint_blocks_write(monkeypatch):
    puts = _stub(monkeypatch, _product(), _product(modified="changed"))
    checked = woocommerce_close.read_woocommerce_workspace_publication(
        uuid4(), external_id="12", expected_sku="PH-1",
    )
    with pytest.raises(ValueError, match="changed since inspection"):
        woocommerce_close.unpublish_woocommerce_workspace_product(
            uuid4(), external_id="12", expected_sku="PH-1",
            expected_fingerprint=checked["fingerprint"],
        )
    assert puts == []


def test_ambiguous_readback_does_not_claim_success(monkeypatch):
    puts = _stub(monkeypatch, _product(), _product(), _product(status="publish"))
    checked = woocommerce_close.read_woocommerce_workspace_publication(
        uuid4(), external_id="12", expected_sku="PH-1",
    )
    with pytest.raises(RuntimeError, match="not draft"):
        woocommerce_close.unpublish_woocommerce_workspace_product(
            uuid4(), external_id="12", expected_sku="PH-1",
            expected_fingerprint=checked["fingerprint"],
        )
    assert len(puts) == 1


def test_draft_cannot_be_unpublished_again(monkeypatch):
    puts = _stub(monkeypatch, _product(status="draft"))
    checked = woocommerce_close.read_woocommerce_workspace_publication(
        uuid4(), external_id="12", expected_sku="PH-1",
    )
    with pytest.raises(ValueError, match="Only published"):
        woocommerce_close.unpublish_woocommerce_workspace_product(
            uuid4(), external_id="12", expected_sku="PH-1",
            expected_fingerprint=checked["fingerprint"],
        )
    assert not puts


def test_variations_and_missing_skus_are_rejected(monkeypatch):
    puts = _stub(monkeypatch, _product(), variation=True)
    with pytest.raises(ValueError, match="variations"):
        woocommerce_close.read_woocommerce_workspace_publication(
            uuid4(), external_id="12:3", expected_sku="PH-1",
        )
    with pytest.raises(ValueError, match="SKU"):
        woocommerce_close.read_woocommerce_workspace_publication(
            uuid4(), external_id="12", expected_sku="",
        )
    assert not puts
