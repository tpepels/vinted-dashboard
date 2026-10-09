"""Shopify price writes use identity-checked GraphQL and exact readback."""
import uuid

import pytest

from app.connectors import hosted, shopify_price

VARIANT = "gid://shopify/ProductVariant/42"
PRODUCT = "gid://shopify/Product/52"


def node(price="8.50", *, sku="SKU-42", compare=None, currency="EUR",
         product_id=PRODUCT, status="ACTIVE", variant_id=VARIANT):
    return {
        "shop": {"currencyCode": currency},
        "productVariant": {
            "id": variant_id, "sku": sku, "price": price,
            "compareAtPrice": compare,
            "product": {"id": product_id, "status": status},
        },
    }


def mutation(*, errors=None, product_id=PRODUCT, variant_id=VARIANT):
    return {"productVariantsBulkUpdate": {
        "product": {"id": product_id},
        "productVariants": [{"id": variant_id, "price": "11.25", "compareAtPrice": None}],
        "userErrors": errors or [],
    }}


def prepare(monkeypatch, *responses, configured_currency="EUR"):
    sequence = iter(responses)
    calls = []
    monkeypatch.setattr(hosted, "_credentials", lambda *a: {
        "store_domain": "example.myshopify.com",
        "access_token": "secret",
        "currency": configured_currency,
    })

    def gql(values, query, *, variables=None):
        calls.append((query, variables))
        return next(sequence)

    monkeypatch.setattr(hosted, "_shopify_graphql", gql)
    return calls


def change(*, old=850, new=1125, external_id=VARIANT, sku="SKU-42", currency="EUR"):
    return shopify_price.update_shopify_workspace_price(
        uuid.uuid4(), external_id=external_id, expected_sku=sku,
        expected_currency=currency, old_price_cents=old, new_price_cents=new,
    )


def test_read_is_identity_and_currency_checked(monkeypatch):
    calls = prepare(monkeypatch, node())
    result = shopify_price.read_shopify_workspace_price(
        uuid.uuid4(), external_id=VARIANT, expected_sku="SKU-42",
        expected_currency="EUR",
    )
    assert result["regular_price_cents"] == 850
    assert result["product_id"] == PRODUCT
    assert len(calls) == 1 and calls[0][1] == {"variantId": VARIANT}
    assert "shop { currencyCode }" in calls[0][0]


def test_write_changes_only_variant_base_price_and_checks_readback(monkeypatch):
    calls = prepare(monkeypatch, node(), mutation(), node(price="11.25"))
    result = change()
    assert result["remote_verified"] and not result["already_complete"]
    assert result["price_cents"] == 1125
    assert len(calls) == 3
    assert calls[1][1] == {
        "productId": PRODUCT,
        "variants": [{"id": VARIANT, "price": "11.25"}],
    }
    assert "productVariantsBulkUpdate" in calls[1][0]


def test_matching_price_skips_remote_write(monkeypatch):
    calls = prepare(monkeypatch, node())
    result = change(old=850, new=850)
    assert result["already_complete"] is True
    assert len(calls) == 1


@pytest.mark.parametrize("response,error", [
    (node(sku="DIFFERENT"), "SKU differs"),
    (node(currency="USD"), "base currency differs"),
    (node(compare="10.00"), "compare-at price"),
    (node(status="DRAFT"), "not active"),
    (node(price="8.501"), "two-decimal"),
    (node(price=""), "no base price"),
    (node(product_id="wrong"), "parent product"),
    (node(variant_id="gid://shopify/ProductVariant/99"), "not found"),
])
def test_unsafe_store_state_fails_without_mutation(monkeypatch, response, error):
    calls = prepare(monkeypatch, response)
    with pytest.raises(ValueError, match=error):
        change()
    assert len(calls) == 1


def test_configured_currency_mismatch_refused(monkeypatch):
    calls = prepare(monkeypatch, node(), configured_currency="USD")
    with pytest.raises(ValueError, match="Configured Shopify currency"):
        change()
    assert len(calls) == 1


def test_changed_price_since_check_prevents_mutation(monkeypatch):
    calls = prepare(monkeypatch, node(price="9.25"))
    with pytest.raises(ValueError, match="changed since the last check"):
        change()
    assert len(calls) == 1


def test_mutation_error_and_identity_error_never_claim_success(monkeypatch):
    calls = prepare(monkeypatch, node(), mutation(errors=[{"message": "declined"}]))
    with pytest.raises(RuntimeError, match="rejected"):
        change()
    assert len(calls) == 2
    calls = prepare(monkeypatch, node(), mutation(variant_id="gid://shopify/ProductVariant/99"))
    with pytest.raises(RuntimeError, match="exact variant identity"):
        change()
    assert len(calls) == 2


def test_readback_price_difference_never_claims_success(monkeypatch):
    calls = prepare(monkeypatch, node(), mutation(), node(price="8.50"))
    with pytest.raises(RuntimeError, match="differs after the write"):
        change()
    assert len(calls) == 3


def test_invalid_input_never_contacts_store(monkeypatch):
    monkeypatch.setattr(hosted, "_credentials",
                        lambda *a: pytest.fail("invalid identity must not read credentials"))
    with pytest.raises(ValueError, match="Price must"):
        change(new=0)
    with pytest.raises(ValueError, match="ProductVariant"):
        shopify_price.read_shopify_workspace_price(
            uuid.uuid4(), external_id="../path", expected_sku="SKU-42",
            expected_currency="EUR",
        )
