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
    assert "Array.isArray(todayData.work_queue)" in APP_JS
    assert "Array.isArray(rows) ? rows : []" in APP_JS


def test_today_does_not_surface_historical_unlinked_sales_as_cross_channel_actions():
    assert "function renderStockActions(rows)" not in APP_JS
    assert "sold order(s) still need a master-stock link" not in APP_JS



def test_purchase_cost_workflow_is_wired_into_product_ui():
    assert 'id="purchase-cost-card"' in (
        Path(__file__).resolve().parents[1] / "app" / "product_static" / "index.html"
    ).read_text(encoding="utf-8")
    assert 'api("/api/app/purchase-cost-suggestions")' in APP_JS
    assert 'purchase-cost-apply' in APP_JS
    assert 'inventory-cost' in APP_JS


def test_inventory_cost_filter_supports_missing_and_recorded_values():
    html = (
        Path(__file__).resolve().parents[1] / "app" / "product_static" / "index.html"
    ).read_text(encoding="utf-8")
    assert '<option value="missing">Missing cost</option>' in html
    assert '<option value="recorded">Cost recorded</option>' in html
    assert 'item.cost_cents == null' in APP_JS
    assert 'item.cost_cents != null' in APP_JS



def test_purchase_cost_apply_uses_multi_element_selector():
    lines = [line.strip() for line in APP_JS.splitlines()]
    multi = "$" + "$" + '(".purchase-cost-apply").forEach((button) => {'
    single = "$" + '(".purchase-cost-apply").forEach((button) => {'
    assert multi in lines
    assert single not in lines



def test_profitability_analytics_exposes_sortable_sale_economics():
    assert '"Gross margin YTD"' in APP_JS
    assert "<th>Cost</th><th>Profit</th><th>Margin</th><th>ROI</th>" in APP_JS
    assert 'row.gross_profit_cents == null ? "" : row.gross_profit_cents' in APP_JS
    assert 'row.gross_margin_pct == null ? "" : row.gross_margin_pct' in APP_JS
    assert 'row.roi_pct == null ? "" : row.roi_pct' in APP_JS


def test_category_profitability_is_rendered_from_complete_sales_only():
    assert "<th>Costed</th><th>Revenue</th><th>Cost</th><th>Profit</th><th>Margin</th><th>ROI</th>" in APP_JS
    assert 'row.costed_sales + "/" + row.linked_sales' in APP_JS
    assert 'row.costed_sales ? money(row.gross_profit_cents, analyticsCurrency) : "—"' in APP_JS



def test_today_is_rendered_as_one_unified_work_queue():
    html = (
        Path(__file__).resolve().parents[1] / "app" / "product_static" / "index.html"
    ).read_text(encoding="utf-8")
    assert 'id="today-work-count"' in html
    assert 'class="card today-work"' in html
    assert 'id="stock-actions"' not in html
    assert "renderTodayWorkQueue(" in APP_JS
    assert 'row.kind === "stock_action"' in APP_JS
    assert 'button.dataset.kind === "purchase_cost"' in APP_JS


def test_inventory_surfaces_enriched_vinted_metadata():
    assert "item.attributes?.author" in APP_JS
    assert "item.attributes?.brand" in APP_JS
    assert "item.attributes?.size" in APP_JS
    assert "item.attributes?.vinted_category" in APP_JS



def test_today_navigation_uses_multi_element_selector():
    lines = [line.strip() for line in APP_JS.splitlines()]
    multi = "$" + "$" + '(".today-nav").forEach((button) => {'
    single = "$" + '(".today-nav").forEach((button) => {'
    assert multi in lines
    assert single not in lines



def test_listing_dates_use_only_real_vinted_timestamp():
    assert "function listingDisplayDate(row)" in APP_JS
    assert "return row.listed_at || null;" in APP_JS
    assert "row.listed_at || row.first_seen_at" not in APP_JS
    assert "first observed" not in APP_JS
    assert "minimum age" not in APP_JS


def test_vinted_analytics_renders_unknown_age_as_unavailable():
    assert 'row.age_days == null ? "—"' in APP_JS
    assert "summary.actual_age_count" in APP_JS



def test_general_marketplace_integrations_are_exposed_in_product_ui():
    html = (
        Path(__file__).resolve().parents[1] / "app" / "product_static" / "index.html"
    ).read_text(encoding="utf-8")
    assert '<option value="etsy">Etsy</option>' in html
    assert '<option value="woocommerce">WooCommerce</option>' in html
    assert 'id="test-connector"' in html
    assert "etsy: {" in APP_JS
    assert "woocommerce: {" in APP_JS
    assert '"/test-connection"' in APP_JS
    assert "listings_r and transactions_r" in APP_JS
    assert "WooCommerce REST API v3" in APP_JS



def test_expanded_resale_categories_are_available_in_inventory_ui():
    html = (
        Path(__file__).resolve().parents[1] / "app" / "product_static" / "index.html"
    ).read_text(encoding="utf-8")
    for value in (
        "electronics",
        "home",
        "collectibles",
        "toys_games",
        "media",
        "sports",
        "beauty",
        "art_crafts",
    ):
        assert f'<option value="{value}">' in html
