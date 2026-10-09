"""Shopify sold-out closure must not affect a product with other variants."""
from copy import deepcopy
from uuid import uuid4

import pytest

from app.connectors import hosted, shopify_close


VARIANT = "gid://shopify/ProductVariant/42"
OTHER_VARIANT = "gid://shopify/ProductVariant/43"
PRODUCT = "gid://shopify/Product/12"


def record(status="ACTIVE", sku="BOOK-42", updated="2026-10-09T10:00:00Z",
           other_variant=False, missing_count=False):
    nodes = [{"id": VARIANT, "sku": sku}]
    if other_variant:
        nodes.append({"id": OTHER_VARIANT, "sku": "OTHER-43"})
    return {"productVariant": {
        "id": VARIANT, "sku": sku,
        "product": {
            "id": PRODUCT, "status": status, "title": "One book",
            "updatedAt": updated,
            "variants": {"nodes": nodes,
                         "pageInfo": {} if missing_count else {"hasNextPage": False}},
        },
    }}


def stub(monkeypatch, *responses):
    calls = []
    responses = iter(responses)
    monkeypatch.setattr(hosted, "_credentials", lambda *args: {"access_token": "test"})
    def graphql(values, query, *, variables=None):
        calls.append((query, variables))
        return deepcopy(next(responses))
    monkeypatch.setattr(hosted, "_shopify_graphql", graphql)
    return calls


def test_read_only_check_does_not_mutate(monkeypatch):
    calls = stub(monkeypatch, record())
    result = shopify_close.read_shopify_workspace_publication(
        uuid4(), external_id=VARIANT, expected_sku="BOOK-42",
    )
    assert result["status"] == "active"
    assert result["can_unpublish"]
    assert result["product_id"] == PRODUCT
    assert result["fingerprint"]
    assert len(calls) == 1 and "query Reseller" in calls[0][0]


def test_exact_single_variant_status_only_mutation_and_readback(monkeypatch):
    calls = stub(monkeypatch, record(), record(),
                 {"productUpdate": {"product": {"id": PRODUCT, "status": "DRAFT"},
                                    "userErrors": []}},
                 record(status="DRAFT", updated="2026-10-09T10:01:00Z"))
    checked = shopify_close.read_shopify_workspace_publication(
        uuid4(), external_id=VARIANT, expected_sku="BOOK-42",
    )
    result = shopify_close.unpublish_shopify_workspace_product(
        uuid4(), external_id=VARIANT, expected_sku="BOOK-42",
        expected_fingerprint=checked["fingerprint"],
    )
    assert result["remote_verified"] and result["status"] == "draft"
    assert len(calls) == 4
    assert calls[2][1] == {"product": {"id": PRODUCT, "status": "DRAFT"}}


@pytest.mark.parametrize("response,match", [
    (record(sku="OTHER"), "SKU differs"),
    (record(other_variant=True), "multiple or unverified variants"),
    (record(missing_count=True), "multiple or unverified variants"),
    (record(status="MYSTERY"), "unsupported product status"),
])
def test_invalid_identity_and_multi_variant_refused_without_write(monkeypatch, response, match):
    calls = stub(monkeypatch, response)
    with pytest.raises(ValueError, match=match):
        shopify_close.read_shopify_workspace_publication(
            uuid4(), external_id=VARIANT, expected_sku="BOOK-42",
        )
    assert len(calls) == 1 and "query " in calls[0][0]


def test_invalid_variant_id_and_missing_sku_refused_before_remote_call(monkeypatch):
    calls = stub(monkeypatch)
    with pytest.raises(ValueError, match="ProductVariant"):
        shopify_close.read_shopify_workspace_publication(
            uuid4(), external_id="12", expected_sku="BOOK-42",
        )
    with pytest.raises(ValueError, match="SKU"):
        shopify_close.read_shopify_workspace_publication(
            uuid4(), external_id=VARIANT, expected_sku="",
        )
    assert not calls


def test_changed_product_fingerprint_prevents_write(monkeypatch):
    calls = stub(monkeypatch, record(), record(updated="changed"))
    checked = shopify_close.read_shopify_workspace_publication(
        uuid4(), external_id=VARIANT, expected_sku="BOOK-42",
    )
    with pytest.raises(ValueError, match="changed since inspection"):
        shopify_close.unpublish_shopify_workspace_product(
            uuid4(), external_id=VARIANT, expected_sku="BOOK-42",
            expected_fingerprint=checked["fingerprint"],
        )
    assert len(calls) == 2


@pytest.mark.parametrize("response,match", [
    ({"productUpdate": {"product": None, "userErrors": [{"message": "rejected"}]}},
     "did not accept"),
    ({"productUpdate": {"product": {"id": PRODUCT, "status": "ACTIVE"},
                        "userErrors": []}}, "did not confirm"),
])
def test_rejected_or_ambiguous_mutation_is_not_success(monkeypatch, response, match):
    calls = stub(monkeypatch, record(), response)
    fingerprint = shopify_close.read_shopify_workspace_publication(
        uuid4(), external_id=VARIANT, expected_sku="BOOK-42",
    )["fingerprint"]
    # A complete attempt must check remote again before sending.
    calls = stub(monkeypatch, record(), response)
    with pytest.raises(RuntimeError, match=match):
        shopify_close.unpublish_shopify_workspace_product(
            uuid4(), external_id=VARIANT, expected_sku="BOOK-42",
            expected_fingerprint=fingerprint,
        )
    assert len(calls) == 2


def test_readback_failed_stays_unverified(monkeypatch):
    calls = stub(monkeypatch, record(), record(),
                 {"productUpdate": {"product": {"id": PRODUCT, "status": "DRAFT"},
                                    "userErrors": []}},
                 record(status="ACTIVE"))
    checked = shopify_close.read_shopify_workspace_publication(
        uuid4(), external_id=VARIANT, expected_sku="BOOK-42",
    )
    with pytest.raises(RuntimeError, match="not a draft"):
        shopify_close.unpublish_shopify_workspace_product(
            uuid4(), external_id=VARIANT, expected_sku="BOOK-42",
            expected_fingerprint=checked["fingerprint"],
        )
    assert len(calls) == 4


def test_already_drafted_product_is_not_mutated(monkeypatch):
    calls = stub(monkeypatch, record(status="DRAFT"), record(status="DRAFT"))
    checked = shopify_close.read_shopify_workspace_publication(
        uuid4(), external_id=VARIANT, expected_sku="BOOK-42",
    )
    with pytest.raises(ValueError, match="Only active"):
        shopify_close.unpublish_shopify_workspace_product(
            uuid4(), external_id=VARIANT, expected_sku="BOOK-42",
            expected_fingerprint=checked["fingerprint"],
        )
    assert len(calls) == 2
