"""WooCommerce regular price safety tests."""
import uuid
from types import SimpleNamespace

import pytest
from app.connectors import hosted, woocommerce_price


def sample(price="8.50", sku="SKU-1", **kwargs):
    return {"id":42,"sku":sku,"type":"simple","price":price,
            "regular_price":price,"sale_price":"","on_sale":False,**kwargs}


def setup(monkeypatch, *records, currency="EUR"):
    rows=iter(records)
    writes=[]
    monkeypatch.setattr(hosted,"_credentials",lambda *args:{
        "store_url":"https://example.test","consumer_key":"key",
        "consumer_secret":"secret","currency":currency,
    })
    monkeypatch.setattr(hosted,"_woocommerce_base",lambda *_:"https://example.test")
    monkeypatch.setattr(hosted,"_woo_get",lambda *a,**kw:next(rows))
    def put(url, *, headers, json, timeout):
        writes.append((url,json))
        return SimpleNamespace(status_code=200)
    monkeypatch.setattr(woocommerce_price.requests,"put",put)
    return writes


def change(old=850,new=1125,external_id="42",sku="SKU-1",currency="EUR"):
    return woocommerce_price.update_woocommerce_workspace_price(
        uuid.uuid4(),external_id=external_id,expected_sku=sku,
        expected_currency=currency,old_price_cents=old,new_price_cents=new,
    )


def test_regular_price_only_and_exact_readback(monkeypatch):
    writes=setup(monkeypatch,sample(),sample(price="11.25"))
    result=change()
    assert result["remote_verified"] and result["price_cents"]==1125
    assert writes == [("https://example.test/wp-json/wc/v3/products/42",
                       {"regular_price":"11.25"})]


def test_already_matching_price_never_written(monkeypatch):
    writes=setup(monkeypatch,sample())
    assert change(old=850,new=850)["already_complete"]
    assert not writes


@pytest.mark.parametrize("record,message",[
    (sample(sku="OTHER"),"SKU no longer matches"),
    (dict(sample(),sale_price="5.00",on_sale=True),"promotion"),
    (sample(price="8.501"),"two-decimal range"),
    (sample(price=""),"regular price"),
])
def test_unsafe_remote_data_refuses_write(monkeypatch,record,message):
    writes=setup(monkeypatch,record)
    with pytest.raises(ValueError,match=message):
        change()
    assert not writes


def test_currency_and_remote_price_drift_refuse_write(monkeypatch):
    writes=setup(monkeypatch,sample(),currency="USD")
    with pytest.raises(ValueError,match="currency differ"):
        change()
    assert not writes
    writes=setup(monkeypatch,sample(price="9.00"))
    with pytest.raises(ValueError,match="changed after"):
        change()
    assert not writes


def test_readback_difference_is_not_success(monkeypatch):
    writes=setup(monkeypatch,sample(),sample())
    with pytest.raises(RuntimeError,match="differs after update"):
        change()
    assert len(writes)==1


def test_variation_updates_only_variation_price(monkeypatch):
    parent={"id":11,"type":"variable","status":"publish"}
    variation={**sample(),"id":7,"type":"variation"}
    changed={**sample(price="11.25"),"id":7,"type":"variation"}
    writes=setup(monkeypatch,parent,variation,parent,changed)
    result=change(external_id="11:7")
    assert result["remote_verified"]
    assert writes==[("https://example.test/wp-json/wc/v3/products/11/variations/7",
                     {"regular_price":"11.25"})]
