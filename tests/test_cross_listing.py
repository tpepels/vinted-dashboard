from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import cross_listing, db, entry, models
from app.constants import Channel, ItemCategory, ListingStatus
from app.connectors import hosted


def _workspace(slug: str = "cross-list"):
    with db.session_scope() as session:
        workspace = models.Workspace(name="Cross list", slug=slug, settings={})
        session.add(workspace)
        session.flush()
        return workspace.id


def _source_item(workspace_id):
    with db.session_scope() as session:
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="VINTED-BOOK-1",
            title="Master title",
            category=ItemCategory.BOOK,
            quantity=2,
            condition="very_good",
            currency="EUR",
            attributes={"author": "Master Author"},
        )
        session.add(item)
        session.flush()
        source = models.ChannelListing(
            workspace_id=workspace_id,
            inventory_item_id=item.id,
            channel=Channel.VINTED,
            external_id="991234",
            external_sku="991234",
            title="Vinted title",
            price_cents=725,
            currency="EUR",
            status=ListingStatus.ACTIVE,
            quantity=1,
            url="https://www.vinted.pt/items/991234",
            extra={
                "metadata": {
                    "description": "Vinted source description",
                    "author": "Vinted Author",
                    "isbn": "9780140328721",
                    "condition": "Very good",
                },
                "image_urls": [
                    "https://images1.vinted.net/t/one.webp",
                    "https://images1.vinted.net/t/two.jpg",
                ],
            },
        )
        session.add(source)
        session.flush()
        return item.id, source.id


def test_cross_list_candidate_uses_vinted_copy_but_master_stock():
    workspace_id = _workspace()
    item_id, source_id = _source_item(workspace_id)

    with db.session_scope() as session:
        candidate = cross_listing.build_candidate(
            session,
            workspace_id,
            item_id,
            source_listing_id=source_id,
        )

    assert candidate["ready"] is True
    assert candidate["fields"]["sku"] == "VINTED-BOOK-1"
    assert candidate["fields"]["title"] == "Vinted title"
    assert candidate["fields"]["description"] == "Vinted source description"
    assert candidate["fields"]["price_cents"] == 725
    assert candidate["fields"]["quantity"] == 2
    assert candidate["fields"]["author"] == "Vinted Author"
    assert candidate["fields"]["isbn"] == "9780140328721"
    assert candidate["source"]["channel"] == Channel.VINTED
    assert candidate["source"]["photo_count"] == 2
    assert candidate["field_sources"]["quantity"] == "master"


def test_woocommerce_cross_list_create_reuses_vinted_images(monkeypatch):
    captured = {}
    monkeypatch.setattr(hosted, "_credentials", lambda *_args: {
        "store_url": "https://shop.example.com",
        "consumer_key": "ck_test",
        "consumer_secret": "cs_test",
        "currency": "EUR",
    })

    def fake_post(values, path, *, body):
        captured.update({"path": path, "body": body})
        return {
            "id": 42,
            "name": body["name"],
            "sku": body["sku"],
            "price": body["regular_price"],
            "regular_price": body["regular_price"],
            "stock_quantity": body["stock_quantity"],
            "stock_status": "instock",
            "status": "publish",
            "permalink": "https://shop.example.com/product/42",
            "images": body.get("images") or [],
        }

    monkeypatch.setattr(hosted, "_woo_post", fake_post)
    candidate = {
        "fields": {
            "sku": "SKU-42",
            "title": "Book title",
            "description": "Description",
            "price_cents": 725,
            "quantity": 2,
            "currency": "EUR",
        },
        "source": {
            "image_urls": [
                "https://images1.vinted.net/t/one.webp",
                "https://images1.vinted.net/t/two.jpg",
            ]
        },
    }

    result = hosted.create_woocommerce_workspace_listing(_workspace("woo-create"), candidate)
    assert captured["path"] == "products"
    assert captured["body"]["regular_price"] == "7.25"
    assert captured["body"]["stock_quantity"] == 2
    assert captured["body"]["images"] == [
        {"src": "https://images1.vinted.net/t/one.webp"},
        {"src": "https://images1.vinted.net/t/two.jpg"},
    ]
    assert result["source_id"] == "42"
    assert result["quantity"] == 2


def test_shopify_cross_list_create_sets_inventory_and_files(monkeypatch):
    calls = []
    monkeypatch.setattr(hosted, "_credentials", lambda *_args: {
        "store_domain": "shop.myshopify.com",
        "access_token": "shpat_test",
        "api_version": "2026-10",
        "currency": "EUR",
    })

    def fake_graphql(values, query, *, variables=None):
        calls.append((query, variables))
        if "locations(first: 1" in query:
            return {"locations": {"nodes": [{"id": "gid://shopify/Location/1", "name": "Main"}]}}
        return {
            "productSet": {
                "product": {
                    "id": "gid://shopify/Product/10",
                    "title": "Book title",
                    "handle": "book-title",
                    "status": "ACTIVE",
                    "onlineStoreUrl": "https://shop.example/products/book-title",
                    "variants": {
                        "nodes": [{
                            "id": "gid://shopify/ProductVariant/11",
                            "sku": "SKU-SHOP",
                            "price": "7.25",
                            "inventoryQuantity": 2,
                        }]
                    },
                },
                "userErrors": [],
            }
        }

    monkeypatch.setattr(hosted, "_shopify_graphql", fake_graphql)
    candidate = {
        "fields": {
            "sku": "SKU-SHOP",
            "title": "Book title",
            "description": "Description",
            "price_cents": 725,
            "quantity": 2,
            "currency": "EUR",
        },
        "source": {"image_urls": ["https://images1.vinted.net/t/one.webp"]},
    }

    result = hosted.create_shopify_workspace_listing(_workspace("shopify-create"), candidate)
    mutation = calls[1][1]["input"]
    assert mutation["productOptions"][0]["name"] == "Title"
    variant = mutation["variants"][0]
    assert variant["optionValues"] == [{"optionName": "Title", "name": "Default"}]
    assert variant["inventoryQuantities"][0] == {
        "locationId": "gid://shopify/Location/1",
        "name": "available",
        "quantity": 2,
    }
    assert mutation["files"][0]["originalSource"].startswith("https://images1.vinted.net/")
    assert result["source_id"] == "gid://shopify/ProductVariant/11"
    assert result["quantity"] == 2


def test_wix_cross_list_create_sets_inventory_and_vinted_media(monkeypatch):
    captured = {}
    monkeypatch.setattr(hosted, "_credentials", lambda *_args: {
        "site_id": "00000000-0000-0000-0000-000000000001",
        "api_key": "test",
        "currency": "EUR",
    })

    def fake_post(values, path, *, body=None):
        captured.update({"path": path, "body": body})
        return {
            "product": {
                "id": "prod-1",
                "name": "Book title",
                "variantsInfo": {
                    "variants": [{"id": "var-1", "sku": "SKU-WIX"}]
                },
            }
        }

    monkeypatch.setattr(hosted, "_wix_post", fake_post)
    candidate = {
        "fields": {
            "sku": "SKU-WIX",
            "title": "Book title",
            "description": "Description",
            "price_cents": 725,
            "quantity": 2,
            "currency": "EUR",
        },
        "source": {
            "image_urls": [
                "https://images1.vinted.net/t/one.webp",
                "https://images1.vinted.net/t/two.jpg",
            ]
        },
    }

    result = hosted.create_wix_workspace_listing(_workspace("wix-create"), candidate)
    assert captured["path"] == "stores/v3/products-with-inventory"
    product = captured["body"]["product"]
    variant = product["variantsInfo"]["variants"][0]
    assert variant["inventoryItem"]["quantity"] == 2
    assert product["media"]["itemsInfo"]["items"] == [
        {"url": "https://images1.vinted.net/t/one.webp"},
        {"url": "https://images1.vinted.net/t/two.jpg"},
    ]
    assert result["source_id"] == "prod-1:var-1"


def test_cross_list_publish_records_remote_listing_on_same_physical_item(monkeypatch):
    workspace_id = _workspace("publish-link")
    item_id, source_id = _source_item(workspace_id)
    with db.session_scope() as session:
        candidate = cross_listing.build_candidate(
            session, workspace_id, item_id, source_listing_id=source_id
        )

    monkeypatch.setattr(
        hosted,
        "create_woocommerce_workspace_listing",
        lambda workspace_id, candidate: {
            "source_id": "9001",
            "sku": candidate["fields"]["sku"],
            "title": candidate["fields"]["title"],
            "status": ListingStatus.ACTIVE,
            "quantity": candidate["fields"]["quantity"],
            "price_cents": candidate["fields"]["price_cents"],
            "currency": candidate["fields"]["currency"],
            "url": "https://shop.example/products/9001",
            "description": candidate["fields"]["description"],
            "image_url": candidate["source"]["image_urls"][0],
        },
    )

    result = cross_listing.publish(
        workspace_id,
        item_id,
        Channel.WOOCOMMERCE,
        candidate,
    )
    assert result["external_id"] == "9001"

    with db.session_scope() as session:
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.channel == Channel.WOOCOMMERCE,
                models.ChannelListing.external_id == "9001",
            )
        ).scalar_one()
        assert listing.inventory_item_id == item_id
        assert listing.extra["source_channel"] == Channel.VINTED
        assert listing.extra["source_listing_id"] == str(source_id)


def test_cross_list_preview_shows_every_destination_instead_of_hiding_it(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "cross-list@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Cross-list UI",
        },
    )
    assert registered.status_code == 200, registered.text
    csrf = registered.json()["csrf_token"]

    with db.session_scope() as session:
        membership = session.execute(select(models.Membership)).scalar_one()
        workspace_id = membership.workspace_id
    item_id, source_id = _source_item(workspace_id)

    saved = client.put(
        "/api/app/connectors/woocommerce/credentials",
        headers={"X-CSRF-Token": csrf},
        json={
            "values": {
                "store_url": "https://shop.example.com",
                "consumer_key": "ck_test",
                "consumer_secret": "cs_test",
            }
        },
    )
    assert saved.status_code == 200, saved.text

    preview = client.get(
        f"/api/app/inventory/{item_id}/cross-list",
        params={"source_listing_id": str(source_id)},
    )
    assert preview.status_code == 200, preview.text
    rows = {row["channel"]: row for row in preview.json()["destinations"]}

    assert rows[Channel.WOOCOMMERCE]["status"] == "ready"
    assert rows[Channel.WOOCOMMERCE]["action"] == "publish"
    assert rows[Channel.SHOPIFY]["status"] == "connect"
    assert rows[Channel.WIX]["status"] == "connect"
    assert rows[Channel.BIBLIO]["channel"] == Channel.BIBLIO
    assert rows[Channel.EBAY]["status"] == "not_writable"
    assert "category" in rows[Channel.EBAY]["reason"].lower()
    assert rows[Channel.ETSY]["status"] == "not_writable"
    assert "taxonomy" in rows[Channel.ETSY]["reason"].lower()
    assert rows[Channel.BIGCOMMERCE]["status"] == "not_writable"
    assert rows[Channel.SQUARESPACE]["status"] == "not_writable"
    assert rows[Channel.DEPOP]["status"] == "not_writable"



def test_cross_list_biblio_review_can_mark_sparse_general_item_as_book(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "cross-list-biblio-review@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Cross-list BIBLIO review",
        },
    )
    assert registered.status_code == 200, registered.text
    csrf = registered.json()["csrf_token"]

    with db.session_scope() as session:
        membership = session.execute(select(models.Membership)).scalar_one()
        workspace_id = membership.workspace_id
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="SPARSE-BOOK-1",
            title="Sparse vintage book",
            category=ItemCategory.GENERAL,
            quantity=1,
            condition="very_good",
            currency="EUR",
            attributes={},
        )
        session.add(item)
        session.flush()
        source = models.ChannelListing(
            workspace_id=workspace_id,
            inventory_item_id=item.id,
            channel=Channel.VINTED,
            external_id="998877",
            title="Sparse vintage book",
            price_cents=500,
            currency="EUR",
            status=ListingStatus.ACTIVE,
            quantity=1,
            url="https://www.vinted.pt/items/998877",
            extra={
                "metadata": {"description": "Old paperback in very good condition."},
                "image_urls": ["https://images1.vinted.net/t/sparse.jpg"],
            },
        )
        session.add(source)
        session.flush()
        item_id = item.id
        source_id = source.id

    preview = client.get(
        f"/api/app/inventory/{item_id}/cross-list",
        params={"source_listing_id": str(source_id)},
    )
    assert preview.status_code == 200, preview.text
    biblio = next(
        row for row in preview.json()["destinations"]
        if row["channel"] == Channel.BIBLIO
    )
    assert biblio["status"] == "review"
    assert biblio["action"] == "biblio_classify"
    assert "confirm this item is a book" in biblio["reason"].lower()

    patched = client.patch(
        f"/api/app/inventory/{item_id}",
        headers={"X-CSRF-Token": csrf},
        json={"category": ItemCategory.BOOK},
    )
    assert patched.status_code == 200, patched.text

    biblio_preview = client.get(
        f"/api/app/inventory/{item_id}/publish/biblio",
        params={"source_listing_id": str(source_id)},
    )
    assert biblio_preview.status_code == 200, biblio_preview.text
    assert biblio_preview.json()["fields"]["title"] == "Sparse vintage book"
    assert "author" in biblio_preview.json()["missing"]
