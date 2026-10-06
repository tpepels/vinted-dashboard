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
            attributes={
                "author": "Master Author",
                "barcode": "9780140328721",
                "edition": "Revised edition",
                "publication_year": 1988,
                "publish_date": "1988",
                "binding": "Paperback",
                "pages": 176,
                "subtitle": "A Novel",
            },
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
                    "publisher": "Puffin",
                    "language": "English",
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
    assert candidate["fields"]["barcode"] == "9780140328721"
    assert candidate["fields"]["publisher"] == "Puffin"
    assert candidate["fields"]["edition"] == "Revised edition"
    assert candidate["fields"]["publication_year"] == 1988
    assert candidate["fields"]["binding"] == "Paperback"
    assert candidate["fields"]["pages"] == 176
    assert candidate["fields"]["language"] == "English"
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
            "category": "book",
            "condition": "Very good",
            "author": "Author Name",
            "isbn": "9780140328721",
            "publisher": "Puffin",
            "edition": "Revised edition",
            "language": "English",
            "binding": "Paperback",
            "pages": 176,
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
    assert captured["body"]["global_unique_id"] == "9780140328721"
    woo_attrs = {row["name"]: row["options"] for row in captured["body"]["attributes"]}
    assert woo_attrs["ISBN"] == ["9780140328721"]
    assert woo_attrs["Author"] == ["Author Name"]
    assert woo_attrs["Publisher"] == ["Puffin"]
    assert woo_attrs["Binding"] == ["Paperback"]
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
            "category": "book",
            "condition": "Very good",
            "author": "Author Name",
            "isbn": "9780140328721",
            "publisher": "Puffin",
            "edition": "Revised edition",
            "language": "English",
            "binding": "Paperback",
            "pages": 176,
            "tags": ["fiction", "paperback"],
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
            "category": "book",
            "condition": "Very good",
            "author": "Author Name",
            "isbn": "9780140328721",
            "publisher": "Puffin",
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
    assert variant["barcode"] == "9780140328721"
    assert product["plainDescription"] == "Description"
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



def test_cross_list_candidate_fills_missing_book_metadata_from_isbn(monkeypatch):
    workspace_id = _workspace("isbn-enrichment")
    with db.session_scope() as session:
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="BOOK-ENRICH",
            title="Known book",
            category=ItemCategory.BOOK,
            quantity=1,
            condition="good",
            currency="EUR",
            attributes={"isbn": "9780140328721", "default_price_cents": 600},
        )
        session.add(item)
        session.flush()
        item_id = item.id

    monkeypatch.setattr(
        cross_listing.stock_intake,
        "lookup_isbn",
        lambda isbn: {
            "isbn": isbn,
            "title": "Known book",
            "subtitle": "Subtitle",
            "author": "Author Name",
            "publisher": "Publisher Name",
            "edition": "Second",
            "physical_format": "Paperback",
            "publish_date": "1988",
            "publication_year": 1988,
            "number_of_pages": 176,
        },
    )

    with db.session_scope() as session:
        candidate = cross_listing.build_candidate(
            session,
            workspace_id,
            item_id,
        )

    assert candidate["fields"]["isbn"] == "9780140328721"
    assert candidate["fields"]["subtitle"] == "Subtitle"
    assert candidate["fields"]["author"] == "Author Name"
    assert candidate["fields"]["publisher"] == "Publisher Name"
    assert candidate["fields"]["edition"] == "Second"
    assert candidate["fields"]["binding"] == "Paperback"
    assert candidate["fields"]["publish_date"] == "1988"
    assert candidate["fields"]["pages"] == 176



def test_cross_list_reuses_master_cover_when_no_vinted_photos_exist():
    workspace_id = _workspace("master-cover")
    with db.session_scope() as session:
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="BOOK-COVER",
            title="Cover Book",
            category=ItemCategory.BOOK,
            quantity=1,
            condition="good",
            currency="EUR",
            attributes={
                "author": "Author",
                "subtitle": "Subtitle",
                "publisher": "Publisher",
                "edition": "Edition",
                "publish_date": "2000",
                "binding": "Paperback",
                "pages": 200,
                "isbn": "9780140328721",
                "default_price_cents": 900,
                "cover_url": "https://covers.openlibrary.org/b/id/123-M.jpg",
            },
        )
        session.add(item)
        session.flush()
        item_id = item.id

    with db.session_scope() as session:
        candidate = cross_listing.build_candidate(
            session,
            workspace_id,
            item_id,
        )

    assert candidate["source"]["channel"] == "master"
    assert candidate["source"]["image_urls"] == [
        "https://covers.openlibrary.org/b/id/123-M.jpg"
    ]
    assert candidate["source"]["photo_count"] == 1



def _update_candidate(*, sku="SKU-UP", quantity=1, price_cents=950):
    return {
        "fields": {
            "sku": sku,
            "title": "Updated book",
            "description": "Updated description",
            "price_cents": price_cents,
            "quantity": quantity,
            "currency": "EUR",
            "category": "book",
            "condition": "Very good",
            "author": "Author Name",
            "isbn": "9780140328721",
            "publisher": "Puffin",
            "edition": "Revised",
            "language": "English",
            "binding": "Paperback",
            "pages": 176,
            "tags": ["fiction"],
        },
        "source": {"image_urls": ["https://images1.vinted.net/t/update.jpg"]},
    }


def test_woocommerce_update_changes_existing_product_in_place(monkeypatch):
    captured = {}
    monkeypatch.setattr(hosted, "_credentials", lambda *_args: {
        "store_url": "https://shop.example.com",
        "consumer_key": "ck_test",
        "consumer_secret": "cs_test",
        "currency": "EUR",
    })

    def fake_put(values, path, *, body):
        captured.update({"path": path, "body": body})
        return {
            "id": 42,
            "name": body["name"],
            "sku": "SKU-UP",
            "regular_price": body["regular_price"],
            "price": body["regular_price"],
            "stock_quantity": body["stock_quantity"],
            "stock_status": body["stock_status"],
            "status": "publish",
            "permalink": "https://shop.example.com/product/42",
            "global_unique_id": body["global_unique_id"],
            "attributes": body["attributes"],
            "images": body["images"],
        }

    monkeypatch.setattr(hosted, "_woo_put", fake_put)
    result = hosted.update_woocommerce_workspace_listing(
        _workspace("woo-update"),
        "42",
        _update_candidate(quantity=3, price_cents=975),
    )

    assert captured["path"] == "products/42"
    assert captured["body"]["stock_quantity"] == 3
    assert captured["body"]["regular_price"] == "9.75"
    assert captured["body"]["global_unique_id"] == "9780140328721"
    assert result["source_id"] == "42"
    assert result["quantity"] == 3
    assert result["price_cents"] == 975


def test_shopify_update_changes_copy_variant_and_single_location_stock(monkeypatch):
    calls = []
    monkeypatch.setattr(hosted, "_credentials", lambda *_args: {
        "store_domain": "shop.myshopify.com",
        "access_token": "shpat_test",
        "api_version": "2026-10",
        "currency": "EUR",
    })

    def fake_graphql(values, query, *, variables=None):
        calls.append((query, variables or {}))
        if "ResellerVariantInventory" in query:
            return {
                "productVariant": {
                    "id": "gid://shopify/ProductVariant/11",
                    "sku": "OLD",
                    "price": "7.25",
                    "product": {
                        "id": "gid://shopify/Product/10",
                        "title": "Old title",
                        "status": "ACTIVE",
                        "onlineStoreUrl": "https://shop.example/products/book",
                    },
                    "inventoryItem": {
                        "id": "gid://shopify/InventoryItem/12",
                        "inventoryLevels": {
                            "nodes": [{
                                "location": {"id": "gid://shopify/Location/1"},
                                "quantities": [{"name": "available", "quantity": 1}],
                            }]
                        },
                    },
                }
            }
        if "ResellerProductUpdate" in query:
            return {
                "productUpdate": {
                    "product": {
                        "id": "gid://shopify/Product/10",
                        "title": "Updated book",
                        "status": "ACTIVE",
                        "onlineStoreUrl": "https://shop.example/products/book",
                    },
                    "userErrors": [],
                }
            }
        if "ResellerVariantUpdate" in query:
            variant = variables["variants"][0]
            return {
                "productVariantsBulkUpdate": {
                    "productVariants": [{
                        "id": variant["id"],
                        "sku": variant["inventoryItem"]["sku"],
                        "price": variant["price"],
                        "inventoryQuantity": 1,
                    }],
                    "userErrors": [],
                }
            }
        if "ResellerSetInventory" in query:
            return {
                "inventorySetQuantities": {
                    "inventoryAdjustmentGroup": {
                        "changes": [{"name": "available", "delta": 1, "quantityAfterChange": 2}]
                    },
                    "userErrors": [],
                }
            }
        raise AssertionError(query)

    monkeypatch.setattr(hosted, "_shopify_graphql", fake_graphql)
    result = hosted.update_shopify_workspace_listing(
        _workspace("shopify-update"),
        "gid://shopify/ProductVariant/11",
        _update_candidate(sku="SKU-SHOP-UP", quantity=2, price_cents=975),
    )

    product_call = next(v for q, v in calls if "ResellerProductUpdate" in q)
    assert product_call["product"]["title"] == "Updated book"
    variant_call = next(v for q, v in calls if "ResellerVariantUpdate" in q)
    variant = variant_call["variants"][0]
    assert variant["price"] == "9.75"
    assert variant["inventoryItem"] == {"sku": "SKU-SHOP-UP", "tracked": True}
    assert variant["barcodes"] == [{"value": "9780140328721", "type": "ISBN"}]
    stock_call = next(v for q, v in calls if "ResellerSetInventory" in q)
    assert stock_call["input"]["quantities"] == [{
        "inventoryItemId": "gid://shopify/InventoryItem/12",
        "locationId": "gid://shopify/Location/1",
        "quantity": 2,
        "changeFromQuantity": 1,
    }]
    assert stock_call["idempotencyKey"]
    assert result["quantity"] == 2
    assert result["price_cents"] == 975


def test_wix_update_uses_revision_safe_product_and_inventory_updates(monkeypatch):
    patches = []
    posts = []
    monkeypatch.setattr(hosted, "_credentials", lambda *_args: {
        "site_id": "site-1",
        "api_key": "test",
        "currency": "EUR",
    })
    monkeypatch.setattr(
        hosted,
        "_wix_get",
        lambda values, path: {
            "product": {
                "id": "prod-1",
                "revision": "7",
                "name": "Old",
                "url": {"url": "https://shop.example/product/old"},
            }
        },
    )
    monkeypatch.setattr(
        hosted,
        "_wix_query_variants",
        lambda values: [{
            "variantId": "var-1",
            "productData": {"productId": "prod-1"},
        }],
    )
    monkeypatch.setattr(
        hosted,
        "_wix_query_inventory",
        lambda values: [{
            "id": "inv-1",
            "revision": "4",
            "productId": "prod-1",
            "variantId": "var-1",
            "trackQuantity": True,
            "quantity": 1,
            "inStock": True,
        }],
    )

    def fake_patch(values, path, *, body):
        patches.append((path, body))
        if path == "stores/v3/products/prod-1":
            return {
                "product": {
                    "id": "prod-1",
                    "revision": "8",
                    "name": body["product"]["name"],
                    "url": {"url": "https://shop.example/product/book"},
                }
            }
        return {"inventoryItem": body["inventoryItem"]}

    def fake_post(values, path, *, body=None):
        posts.append((path, body))
        return {"jobId": "price-job-1"}

    monkeypatch.setattr(hosted, "_wix_patch", fake_patch)
    monkeypatch.setattr(hosted, "_wix_post", fake_post)
    result = hosted.update_wix_workspace_listing(
        _workspace("wix-update"),
        "prod-1:var-1",
        _update_candidate(sku="SKU-WIX-UP", quantity=2, price_cents=975),
    )

    assert patches[0] == (
        "stores/v3/products/prod-1",
        {"product": {
            "id": "prod-1",
            "revision": "7",
            "name": "Updated book",
            "visible": True,
            "plainDescription": "Updated description",
        }},
    )
    assert posts[0][0] == "stores/v3/bulk/products/update-variants-by-filter"
    assert posts[0][1]["filter"] == {"id": "prod-1"}
    assert posts[0][1]["variant"]["price"]["actualPrice"]["amount"] == "9.75"
    assert patches[1][0] == "stores/v3/inventory-items/inv-1"
    assert patches[1][1]["inventoryItem"]["revision"] == "4"
    assert patches[1][1]["inventoryItem"]["quantity"] == 2
    assert result["quantity"] == 2
    assert result["attributes"]["price_job_id"] == "price-job-1"


def test_cross_list_update_preserves_existing_remote_identity(monkeypatch):
    workspace_id = _workspace("update-link")
    item_id, source_id = _source_item(workspace_id)
    with db.session_scope() as session:
        account = models.ChannelAccount(
            workspace_id=workspace_id,
            channel=Channel.WOOCOMMERCE,
            display_name="WooCommerce",
            status="connected",
            config={},
        )
        session.add(account)
        session.flush()
        existing = models.ChannelListing(
            workspace_id=workspace_id,
            inventory_item_id=item_id,
            channel_account_id=account.id,
            channel=Channel.WOOCOMMERCE,
            external_id="9001",
            external_sku="VINTED-BOOK-1",
            title="Old title",
            price_cents=500,
            currency="EUR",
            status=ListingStatus.ACTIVE,
            quantity=2,
            extra={},
        )
        session.add(existing)

    with db.session_scope() as session:
        candidate = cross_listing.build_candidate(
            session, workspace_id, item_id, source_listing_id=source_id
        )

    monkeypatch.setattr(
        hosted,
        "update_woocommerce_workspace_listing",
        lambda workspace_id, external_id, candidate: {
            "source_id": external_id,
            "sku": candidate["fields"]["sku"],
            "title": candidate["fields"]["title"],
            "status": ListingStatus.ACTIVE,
            "quantity": candidate["fields"]["quantity"],
            "price_cents": candidate["fields"]["price_cents"],
            "currency": candidate["fields"]["currency"],
            "url": "https://shop.example/products/9001",
            "description": candidate["fields"]["description"],
        },
    )
    result = cross_listing.update(
        workspace_id, item_id, Channel.WOOCOMMERCE, candidate
    )
    assert result["external_id"] == "9001"
    assert result["action"] == "updated"

    with db.session_scope() as session:
        listing = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.channel == Channel.WOOCOMMERCE,
                models.ChannelListing.external_id == "9001",
            )
        ).scalar_one()
        assert listing.inventory_item_id == item_id
        assert listing.extra["cross_list_updated_at"]
        assert listing.extra["source_channel"] == Channel.VINTED



def test_cross_list_existing_store_is_update_ready_and_put_updates_it(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    client = TestClient(entry.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "cross-list-update@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Cross-list update",
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

    with db.session_scope() as session:
        account = session.execute(
            select(models.ChannelAccount).where(
                models.ChannelAccount.workspace_id == workspace_id,
                models.ChannelAccount.channel == Channel.WOOCOMMERCE,
            )
        ).scalar_one()
        session.add(
            models.ChannelListing(
                workspace_id=workspace_id,
                inventory_item_id=item_id,
                channel_account_id=account.id,
                channel=Channel.WOOCOMMERCE,
                external_id="9001",
                external_sku="VINTED-BOOK-1",
                title="Old title",
                price_cents=500,
                currency="EUR",
                status=ListingStatus.ACTIVE,
                quantity=2,
                url="https://shop.example/products/9001",
                extra={},
            )
        )

    preview = client.get(
        f"/api/app/inventory/{item_id}/cross-list",
        params={"source_listing_id": str(source_id)},
    )
    assert preview.status_code == 200, preview.text
    woo = next(
        row for row in preview.json()["destinations"]
        if row["channel"] == Channel.WOOCOMMERCE
    )
    assert woo["status"] == "update_ready"
    assert woo["action"] == "update"
    assert woo["url"] == "https://shop.example/products/9001"

    monkeypatch.setattr(
        hosted,
        "update_woocommerce_workspace_listing",
        lambda workspace_id, external_id, candidate: {
            "source_id": external_id,
            "sku": candidate["fields"]["sku"],
            "title": candidate["fields"]["title"],
            "status": ListingStatus.ACTIVE,
            "quantity": candidate["fields"]["quantity"],
            "price_cents": candidate["fields"]["price_cents"],
            "currency": candidate["fields"]["currency"],
            "url": "https://shop.example/products/9001",
            "description": candidate["fields"]["description"],
        },
    )
    response = client.put(
        f"/api/app/inventory/{item_id}/cross-list/woocommerce",
        headers={"X-CSRF-Token": csrf},
        json={"source_listing_id": str(source_id)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["action"] == "updated"
    assert response.json()["external_id"] == "9001"

    with db.session_scope() as session:
        listings = session.execute(
            select(models.ChannelListing).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.channel == Channel.WOOCOMMERCE,
            )
        ).scalars().all()
        assert len(listings) == 1
        assert listings[0].external_id == "9001"
