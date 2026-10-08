"""Price check must precede every explicit WooCommerce regular-price write."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.constants import ItemCategory, ItemStatus, ListingStatus
from app.product_models import MarketplaceOperation
from app.stock_relations import record_physical_quantity


def setup():
    client=TestClient(entry.app)
    response=client.post("/api/auth/register",json={
        "email":"woo-price@example.test","password":"a-long-test-password",
        "workspace_name":"Pricing",
    })
    assert response.status_code == 200,response.text
    csrf=response.json()["csrf_token"]
    with db.session_scope() as session:
        wid=session.execute(select(models.Membership)).scalar_one().workspace_id
        item=models.InventoryItem(
            workspace_id=wid,sku="SKU-1",title="Book",
            category=ItemCategory.BOOK,status=ItemStatus.ACTIVE,
            quantity=1,currency="EUR",attributes={"default_price_cents":1125},
        )
        session.add(item)
        session.flush()
        record_physical_quantity(session,item,1)
        listing=models.ChannelListing(
            workspace_id=wid,inventory_item_id=item.id,channel="woocommerce",
            external_id="42",external_sku="SKU-1",title=item.title,
            status=ListingStatus.ACTIVE,price_cents=850,currency="EUR",
            quantity=1,extra={},
        )
        session.add(listing)
        session.flush()
        return client,csrf,wid,item.id,listing.id


def test_price_check_then_write_preserves_master_stock(monkeypatch):
    client,csrf,wid,item_id,listing_id=setup()
    headers={"X-CSRF-Token":csrf}
    monkeypatch.setattr("app.product_api.read_woocommerce_workspace_price",
                        lambda *args,**kw:{"regular_price_cents":850})
    calls=[]
    def update(workspace,**kwargs):
        calls.append((workspace,kwargs))
        return {"remote_verified":True,"price_cents":1125,
                "currency":"EUR","external_id":"42","already_complete":False}
    monkeypatch.setattr("app.product_api.update_woocommerce_workspace_price",update)
    initial=client.get(f"/api/app/inventory/{item_id}/marketplace-status").json()
    assert initial["listings"][0]["can_sync_woocommerce_price"]
    assert initial["listings"][0]["price_verification"]=="not_checked"
    direct=client.post(f"/api/app/inventory/{item_id}/marketplaces/woocommerce/price",
                       headers=headers)
    assert direct.status_code==409
    checked=client.post(f"/api/app/inventory/{item_id}/marketplaces/woocommerce/check-price",
                        headers=headers)
    assert checked.status_code==200,checked.text
    assert checked.json()["remote_price_cents"]==850
    assert not checked.json()["matches"]
    write=client.post(f"/api/app/inventory/{item_id}/marketplaces/woocommerce/price",
                      headers=headers)
    assert write.status_code==200,write.text
    assert calls==[(wid,{
        "external_id":"42","expected_sku":"SKU-1",
        "expected_currency":"EUR","old_price_cents":850,"new_price_cents":1125,
    })]
    with db.session_scope() as session:
        item=session.get(models.InventoryItem,item_id)
        listing=session.get(models.ChannelListing,listing_id)
        assert item.quantity==1 and item.attributes["default_price_cents"]==1125
        assert listing.price_cents==1125 and listing.quantity==1
        op=session.execute(select(MarketplaceOperation)).scalar_one()
        assert op.target_key==f"{listing_id}:price"
        assert op.verification=="remote_verified"


def test_uncertain_price_write_requires_price_read_not_stock_read(monkeypatch):
    client,csrf,wid,item_id,listing_id=setup()
    headers={"X-CSRF-Token":csrf}
    monkeypatch.setattr("app.product_api.read_woocommerce_workspace_price",
                        lambda *a,**kw:{"regular_price_cents":850})
    assert client.post(f"/api/app/inventory/{item_id}/marketplaces/woocommerce/check-price",
                       headers=headers).status_code==200
    def uncertain(*a,**kw):
        raise RuntimeError("not confirmed")
    monkeypatch.setattr("app.product_api.update_woocommerce_workspace_price",uncertain)
    response=client.post(f"/api/app/inventory/{item_id}/marketplaces/woocommerce/price",
                         headers=headers)
    assert response.status_code==502
    with db.session_scope() as session:
        op=session.execute(select(MarketplaceOperation)).scalar_one()
        assert op.status=="attention"
        assert op.job_payload["price_cents"]==1125
    monkeypatch.setattr("app.product_api.read_woocommerce_workspace_price",
                        lambda *a,**kw:{"regular_price_cents":1125})
    checked=client.post(f"/api/app/inventory/{item_id}/marketplaces/woocommerce/check-price",
                        headers=headers)
    assert checked.status_code==200
    with db.session_scope() as session:
        op=session.execute(select(MarketplaceOperation)).scalar_one()
        assert op.status=="succeeded" and op.verification=="remote_verified"


def test_price_check_snapshot_cannot_be_reused_after_link_changes(monkeypatch):
    client,csrf,wid,item_id,listing_id=setup()
    headers={"X-CSRF-Token":csrf}
    monkeypatch.setattr("app.product_api.read_woocommerce_workspace_price",
                        lambda *a,**kw:{"regular_price_cents":850})
    assert client.post(
        f"/api/app/inventory/{item_id}/marketplaces/woocommerce/check-price",
        headers=headers).status_code==200
    with db.session_scope() as session:
        listing=session.get(models.ChannelListing,listing_id)
        listing.external_id="43"
    response=client.post(
        f"/api/app/inventory/{item_id}/marketplaces/woocommerce/price",
        headers=headers)
    assert response.status_code==409
    with db.session_scope() as session:
        assert not session.execute(select(MarketplaceOperation)).scalars().all()
