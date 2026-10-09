"""Wix Catalog V3 price checks and revision-guarded updates."""
import uuid
from types import SimpleNamespace

import pytest

from app.connectors import wix_price, hosted
from tests.test_phase6_wix_stock import PRODUCT, VARIANT, EXTERNAL


def product(price="8.50", *, sku="BOOK-1", revision="3",
            compare_at=None, variants=None, options=None, currency="EUR",
            modifiers=None):
    rows = variants if variants is not None else [{
        "id": VARIANT, "sku": sku, "choices": [],
        "barcode": "9781234567890", "visible": True,
        "physicalProperties": {},
        "price": {
            "actualPrice": {"amount": price},
            "compareAtPrice": compare_at,
        },
    }]
    return {
        "product": {
            "id": PRODUCT, "revision": revision, "currency": currency,
            "options": [] if options is None else options,
            "modifiers": [] if modifiers is None else modifiers,
            "variantsInfo": {"variants": rows},
            "variantSummary": {"variantCount": len(rows)},
        }
    }


def fixture(monkeypatch, *responses, configured_currency="EUR"):
    calls = []
    values = {"api_key": "secret", "site_id": str(uuid.uuid4()),
              "currency": configured_currency}
    monkeypatch.setattr(hosted, "_credentials", lambda *args: values)
    responses = iter(responses)

    def get(url, *, headers, timeout):
        calls.append(("GET",url))
        assert url == f"https://www.wixapis.com/stores/v3/products/{PRODUCT}"
        assert headers["Authorization"] == "secret"
        return SimpleNamespace(status_code=200, json=lambda: next(responses))
    def patch(url, *, headers, json, timeout):
        calls.append(("PATCH", url, json))
        assert headers["Authorization"] == "secret"
        return SimpleNamespace(status_code=200)
    monkeypatch.setattr(wix_price.requests, "get", get)
    monkeypatch.setattr(wix_price.requests, "patch", patch)
    return calls


def update(*, old=850, new=1125, external=EXTERNAL,
           expected_currency="EUR", sku="BOOK-1"):
    return wix_price.update_wix_workspace_price(
        uuid.uuid4(), external_id=external, expected_sku=sku,
        expected_currency=expected_currency,
        old_price_cents=old, new_price_cents=new,
    )


def test_price_check_is_read_only_and_exactly_identified(monkeypatch):
    calls = fixture(monkeypatch, product())
    result = wix_price.read_wix_workspace_price(
        uuid.uuid4(), external_id=EXTERNAL,
        expected_sku="BOOK-1", expected_currency="EUR",
    )
    assert result["regular_price_cents"] == 850
    assert result["revision"] == "3"
    assert [c[0] for c in calls] == ["GET"]


def test_revision_patch_preserves_variant_fields_and_reads_back(monkeypatch):
    calls = fixture(monkeypatch, product(), product(price="11.25", revision="4"))
    result = update()
    assert result["remote_verified"] is True
    assert not result["already_complete"]
    assert [c[0] for c in calls] == ["GET", "PATCH", "GET"]
    assert calls[1][1] == f"https://www.wixapis.com/stores/v3/products/{PRODUCT}"
    assert calls[1][2] == {"product": {
        "id": PRODUCT, "revision": "3", "options": [],
        "variantsInfo": {"variants": [{
            "id": VARIANT, "sku": "BOOK-1", "choices": [],
            "barcode": "9781234567890", "visible": True,
            "physicalProperties": {},
            "price": {"actualPrice": {"amount": "11.25"}},
        }]},
    }}


def test_same_price_does_not_write(monkeypatch):
    calls = fixture(monkeypatch, product())
    assert update(old=850, new=850)["already_complete"]
    assert [c[0] for c in calls] == ["GET"]


@pytest.mark.parametrize("response,reason", [
    (product(sku="OTHER"), "SKU no longer matches"),
    (product(compare_at={"amount": "10.00"}), "promotional"),
    (product(options=[{"name": "Size"}]), "without options"),
    (product(modifiers=[{"name": "Gift"}]), "without options"),
    (product(variants=[{}, {}]), "exactly one"),
    (product(price="8.501"), "two-decimal"),
    (product(currency="USD"), "product currency differs"),
    (product(revision="oops"), "revision is missing"),
    (product(variants=[{"id": VARIANT, "sku":"BOOK-1", "choices":[],
        "price":{"actualPrice":{"amount":"8.50"}}, "revenueDetails":{"cost":"1.00"}}]),
        "additional configuration"),
])
def test_unsafe_or_complex_variants_cannot_write(monkeypatch, response, reason):
    calls = fixture(monkeypatch, response)
    with pytest.raises(ValueError, match=reason):
        update()
    assert [c[0] for c in calls] == ["GET"]


def test_outdated_price_refuses_write(monkeypatch):
    calls = fixture(monkeypatch, product(price="9.00"))
    with pytest.raises(ValueError, match="changed since"):
        update()
    assert [c[0] for c in calls] == ["GET"]


def test_readback_failure_and_unchanged_revision_never_verify(monkeypatch):
    calls = fixture(monkeypatch, product(), product(price="8.50", revision="4"))
    with pytest.raises(RuntimeError, match="readback differs"):
        update()
    assert [c[0] for c in calls] == ["GET", "PATCH", "GET"]
    calls = fixture(monkeypatch, product(), product(price="11.25", revision="3"))
    with pytest.raises(RuntimeError, match="readback differs"):
        update()
    assert [c[0] for c in calls] == ["GET", "PATCH", "GET"]


def test_bad_identity_never_uses_credentials(monkeypatch):
    monkeypatch.setattr(hosted, "_credentials", lambda *args:
        pytest.fail("invalid IDs cannot reach credentials"))
    with pytest.raises(ValueError):
        update(external="../unsafe")
    with pytest.raises(ValueError):
        update(sku="")


def test_price_check_requires_explicit_configured_currency(monkeypatch):
    calls = fixture(monkeypatch, product(), configured_currency="")
    with pytest.raises(ValueError, match="Configure Wix store currency"):
        update()
    assert not calls
