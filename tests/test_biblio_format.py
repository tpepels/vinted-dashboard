from app.connectors.biblio_format import parse_biblio_inventory


def test_parse_biblio_inventory_tab_delimited():
    text = (
        "Book ID\tAuthor\tTitle\tDescription\tPrice\tISBN\tStatus\tQuantity\n"
        "ABC-1\tWilla Cather\tA Lost Lady\tPaperback, good condition\t9.50\t9780000000001\tFor sale\t1\n"
        "ABC-2\tJohn Williams\tStoner\tPaperback\t12,00\t9780000000002\tSold\t0\n"
    )

    rows = parse_biblio_inventory(text, currency="EUR")

    assert len(rows) == 2
    assert rows[0]["source_id"] == "ABC-1"
    assert rows[0]["price_cents"] == 950
    assert rows[0]["description"] == "Paperback, good condition"
    assert rows[0]["status"] == "active"
    assert rows[1]["price_cents"] == 1200
    assert rows[1]["status"] == "sold"


def test_parse_biblio_inventory_requires_identity_columns():
    try:
        parse_biblio_inventory("Author\tPrice\nSomeone\t5.00\n")
        assert False, "expected required-column validation"
    except ValueError as exc:
        assert "sku" in str(exc)
        assert "title" in str(exc)
