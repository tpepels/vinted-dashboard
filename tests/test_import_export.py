from io import BytesIO

from openpyxl import Workbook
from sqlalchemy import select

from app import db, models
from app.import_export import (
    apply_inventory_import,
    parse_table,
    preview_inventory_import,
    render_xlsx,
    suggest_mapping,
)


def workspace():
    with db.session_scope() as session:
        row = models.Workspace(name="Test", slug="test", settings={})
        session.add(row)
        session.flush()
        ident = row.id
    return ident


def test_csv_preview_then_apply_is_transactional_and_category_aware():
    wid = workspace()
    table = parse_table(
        "stock.csv",
        b"Inventory No.,Book Name,Writer,ISBN,Stock,Sell Price\n"
        b"B-1,Stoner,John Williams,9780099561545,1,12.50\n",
    )
    mapping = suggest_mapping(table.headers)
    assert mapping["Inventory No."] == "sku"
    assert mapping["Book Name"] == "title"
    assert mapping["Writer"] == "author"

    with db.session_scope() as session:
        preview = preview_inventory_import(session, wid, table.rows, mapping)
        assert preview["counts"]["new"] == 1
        assert preview["can_apply"] is True
        result = apply_inventory_import(session, wid, table.rows, mapping)
        assert result["created"] == 1

    with db.session_scope() as session:
        item = session.execute(
            select(models.InventoryItem).where(models.InventoryItem.workspace_id == wid)
        ).scalar_one()
        assert item.category == "book"
        assert item.attributes["author"] == "John Williams"
        assert item.attributes["isbn"] == "9780099561545"
        assert item.attributes["default_price_cents"] == 1250


def test_full_snapshot_archives_missing_item_only_after_apply():
    wid = workspace()
    with db.session_scope() as session:
        session.add_all(
            [
                models.InventoryItem(workspace_id=wid, sku="A", title="A", quantity=1, attributes={}),
                models.InventoryItem(workspace_id=wid, sku="B", title="B", quantity=1, attributes={}),
            ]
        )

    table = parse_table("stock.tsv", b"SKU\tTitle\tQuantity\nB\tB\t1\n")
    mapping = suggest_mapping(table.headers)
    with db.session_scope() as session:
        preview = preview_inventory_import(
            session, wid, table.rows, mapping, full_snapshot=True
        )
        assert preview["missing_existing_count"] == 1
        assert session.execute(
            select(models.InventoryItem).where(models.InventoryItem.sku == "A")
        ).scalar_one().status == "active"

    with db.session_scope() as session:
        apply_inventory_import(session, wid, table.rows, mapping, full_snapshot=True)

    with db.session_scope() as session:
        a = session.execute(select(models.InventoryItem).where(models.InventoryItem.sku == "A")).scalar_one()
        b = session.execute(select(models.InventoryItem).where(models.InventoryItem.sku == "B")).scalar_one()
        assert a.status == "archived"
        assert a.quantity == 0
        assert b.status == "active"


def test_xlsx_import_and_export_roundtrip():
    book = models.InventoryItem(
        sku="X-1",
        title="Book",
        category="book",
        quantity=1,
        currency="EUR",
        attributes={"author": "Author", "isbn": "9780099561545"},
    )
    data = render_xlsx([
        {
            "SKU": "X-1", "Title": "Book", "Category": "book", "Quantity": 1,
            "Condition": "", "Cost": "", "Price": "8.00", "Currency": "EUR",
            "Location": "", "Notes": "", "Author": "Author",
            "ISBN": "9780099561545", "Publisher": "", "Edition": "", "Binding": "",
            "Publication Year": "", "Brand": "", "Size": "", "Colour": "",
            "Material": "", "Measurements": "", "Status": "active",
        }
    ])
    table = parse_table("inventory.xlsx", data)
    assert table.headers[0] == "SKU"
    assert table.rows[0]["Title"] == "Book"
    assert table.rows[0]["ISBN"] == "9780099561545"


def test_duplicate_sku_in_upload_is_a_conflict():
    wid = workspace()
    table = parse_table("x.csv", b"SKU,Title\nA,One\nA,Two\n")
    mapping = suggest_mapping(table.headers)
    with db.session_scope() as session:
        preview = preview_inventory_import(session, wid, table.rows, mapping)
    assert preview["can_apply"] is False
    assert preview["counts"]["conflict"] == 1
