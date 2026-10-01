from pathlib import Path


APP_JS = (Path(__file__).resolve().parents[1] / "app" / "product_static" / "app.js").read_text(
    encoding="utf-8"
)


def test_inventory_bulk_selection_uses_multi_element_selector():
    assert 'return $$(".inventory-select:checked").map' in APP_JS
    assert 'const boxes = $$(".inventory-select");' in APP_JS
    assert 'return $(".inventory-select:checked").map' not in APP_JS
    assert 'const boxes = $(".inventory-select");' not in APP_JS


def test_reconciliation_bulk_selection_uses_multi_element_selector():
    assert 'const selected = $$(".reconcile-check").filter' in APP_JS
    assert 'const selected = $(".reconcile-check").filter' not in APP_JS


def test_today_handles_missing_array_payloads_defensively():
    assert "Array.isArray(todayData.actions)" in APP_JS
    assert "Array.isArray(todayData.cross_channel_actions)" in APP_JS


def test_today_does_not_surface_historical_unlinked_sales_as_cross_channel_actions():
    assert "function renderStockActions(rows)" in APP_JS
    assert "sold order(s) still need a master-stock link" not in APP_JS
