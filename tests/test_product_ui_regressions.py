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



def test_connection_device_revoke_uses_multi_element_selector():
    lines = [line.strip() for line in APP_JS.splitlines()]
    multi = "$" + "$" + '(".revoke").forEach((button) => {'
    single = "$" + '(".revoke").forEach((button) => {'
    assert multi in lines
    assert single not in lines


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
    assert 'row.listed_at_source === "first_seen"' not in APP_JS
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
    assert '<option value="shopify">Shopify</option>' in html
    assert '<option value="bigcommerce">BigCommerce</option>' in html
    assert '<option value="squarespace">Squarespace</option>' in html
    assert html.count('<option value="wix">Wix</option>') == 3
    assert html.count('<option value="depop">Depop</option>') == 3
    assert 'id="test-connector"' in html
    assert "etsy: {" in APP_JS
    assert "woocommerce: {" in APP_JS
    assert "shopify: {" in APP_JS
    assert "bigcommerce: {" in APP_JS
    assert "squarespace: {" in APP_JS
    assert "wix: {" in APP_JS
    assert "depop: {" in APP_JS
    assert '"/test-connection"' in APP_JS
    assert "listings_r and transactions_r" in APP_JS
    assert 'id="authorize-etsy"' in html
    assert '"/api/app/connectors/etsy/oauth/start"' in APP_JS
    assert "oauth_redirect_uri" in APP_JS
    assert "WooCommerce REST API v3" in APP_JS
    assert "write_products" in APP_JS
    assert "read_locations" in APP_JS
    assert "BigCommerce" in APP_JS
    assert "Squarespace Commerce APIs" in APP_JS
    assert "Product write and Inventory write" in APP_JS
    assert "Private Depop Selling API" in APP_JS
    assert '<span class="nav-label">Work</span>' in html
    assert '<span class="nav-label">Operations</span>' in html
    assert 'id="today-focus-title"' in html
    assert 'id="today-source-status"' in html
    assert 'id="inventory-reset"' in html
    assert 'id="sales-reset"' in html
    assert "function todayPriorityBand" in APP_JS
    assert 'api("/api/app/connectors")' in APP_JS
    assert "function renderTodaySourceStatus" in APP_JS
    assert "Show all " in APP_JS



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



def test_stock_intake_is_first_class_in_inventory_ui():
    html = (
        Path(__file__).resolve().parents[1] / "app" / "product_static" / "index.html"
    ).read_text(encoding="utf-8")
    assert 'id="add-stock"' in html
    assert 'data-action="add-stock">Add stock</button>' in html
    assert 'id="stock-choice-scan"' in html
    assert 'id="stock-choice-photo"' in html
    assert 'id="stock-choice-import"' in html
    assert 'id="stock-choice-connect"' in html
    assert 'id="stock-choice-manual"' in html
    assert 'id="stock-barcode-input"' in html
    assert 'id="stock-barcode-video"' in html
    assert 'capture="environment"' in html
    assert "RDLOC:BOX-17" in html


def test_barcode_camera_has_native_detection_and_server_fallback():
    assert "window.BarcodeDetector" in APP_JS
    assert "navigator.mediaDevices.getUserMedia" in APP_JS
    assert 'api("/api/app/stock-intake/barcode/decode"' in APP_JS
    assert 'api("/api/app/stock-intake/barcode/lookup"' in APP_JS
    assert "captureBarcodeFrame()" in APP_JS
    assert "state.barcodeMisses < 3" in APP_JS
    assert "state.barcodeMisses < 2" in APP_JS
    assert 'if (view !== "inventory" && state.barcodeStream) stopBarcodeCamera();' in APP_JS


def test_scanned_batch_keeps_each_physical_copy_separate():
    assert "const row = {" in APP_JS
    assert "local_id: stockLocalId()," in APP_JS
    assert "state.stockIntakeQueue.push(row);" in APP_JS
    assert 'api("/api/app/stock-intake/items"' in APP_JS
    assert "existing_copy_count" in APP_JS
    assert "const readyRows = state.stockIntakeQueue.filter(stockRowReady);" in APP_JS



def test_stock_intake_dynamic_rows_use_multi_element_selectors():
    lines = [line.strip() for line in APP_JS.splitlines()]
    assert '$$(".stock-row-input").forEach((field) => {' in lines
    assert '$$(".stock-row-remove").forEach((button) => {' in lines
    assert '$(".stock-row-input").forEach((field) => {' not in lines
    assert '$(".stock-row-remove").forEach((button) => {' not in lines



def test_scanner_enqueue_is_non_blocking_and_enrichment_runs_separately():
    assert "function addScannedBarcode(code, format = \"manual\")" in APP_JS
    assert "state.stockIntakeQueue.push(row);" in APP_JS
    assert "queueStockEnrichment(row.local_id);" in APP_JS
    add_start = APP_JS.index('function addScannedBarcode(code, format = "manual")')
    add_end = APP_JS.index("async function decodeBarcodeImage", add_start)
    add_block = APP_JS[add_start:add_end]
    assert 'await api("/api/app/stock-intake/barcode/lookup"' not in add_block
    assert "function pumpStockEnrichment()" in APP_JS
    assert "state.stockEnrichmentActive < 2" in APP_JS


def test_scanner_session_is_persistent_and_reopens_in_scan_mode():
    assert "localStorage.setItem(stockSessionKey()" in APP_JS
    assert "localStorage.getItem(stockSessionKey())" in APP_JS
    assert "lastStockIntakeMode() === \"scan\"" in APP_JS
    assert "restoreStockIntakeSession();" in APP_JS
    assert "persistStockIntakeSession();" in APP_JS


def test_ready_items_can_be_created_without_blocking_on_review_rows():
    assert "const readyRows = state.stockIntakeQueue.filter(stockRowReady);" in APP_JS
    assert "No ready items yet. Keep scanning or review unidentified rows." in APP_JS
    assert "Unresolved scans remain in the batch." in APP_JS
    assert 'row?.enrichment_state !== "pending"' in APP_JS


def test_camera_duplicate_suppression_requires_barcode_to_leave_frame():
    assert "barcodeCameraLatch" in APP_JS
    assert "function acceptCameraBarcode(code, format)" in APP_JS
    assert "if (state.barcodeCameraLatch === raw) return false;" in APP_JS
    assert "function noteCameraBarcodeMiss()" in APP_JS
    assert "state.barcodeCameraClearFrames >= 2" in APP_JS


def test_stock_scan_ui_exposes_throughput_controls():
    html = (
        Path(__file__).resolve().parents[1] / "app" / "product_static" / "index.html"
    ).read_text(encoding="utf-8")
    assert 'id="stock-undo-last"' in html
    assert "Scan continuously" in html
    assert "Ctrl/Cmd+Z to undo" in html
    assert "Create ready items" in html



def test_universal_cross_listing_is_available_from_inventory_and_vinted_listings():
    html = (
        Path(__file__).resolve().parents[1] / "app" / "product_static" / "index.html"
    ).read_text(encoding="utf-8")
    assert 'id="cross-list-panel"' in html
    assert 'id="cross-list-destinations"' in html
    assert "function openCrossList(itemId, sourceListingId = null)" in APP_JS
    assert "function inventoryCrossListAction(item)" in APP_JS
    assert "function listingCrossListAction(row)" in APP_JS
    assert ">Cross-list</button>" in APP_JS
    assert '"/cross-list" + suffix' in APP_JS
    assert '"/cross-list/" + encodeURIComponent(channel)' in APP_JS
    assert 'id="biblio-publish-panel"' in html
    assert "function openBiblioPublish(itemId, sourceListingId = null)" in APP_JS


def test_biblio_preflight_shows_source_and_reviewable_prefilled_fields():
    assert "Using the linked <strong>Vinted listing</strong> as the source" in APP_JS
    assert 'class="biblio-review-input"' in APP_JS
    assert "data-required" in APP_JS
    assert 'field_sources' in APP_JS
    assert "ISBN lookup" in APP_JS


def test_biblio_publish_posts_source_listing_and_inline_repairs():
    assert 'const payload = { source_listing_id: current.sourceListingId || null };' in APP_JS
    assert 'payload.price_cents = Math.round(Number(value) * 100);' in APP_JS
    assert 'BIBLIO listing and available Vinted photos queued for FTP publication.' in APP_JS



def test_vinted_age_is_first_class_and_supports_relative_vinted_age():
    html = (
        Path(__file__).resolve().parents[1] / "app" / "product_static" / "index.html"
    ).read_text(encoding="utf-8")
    assert 'id="listing-stat-youngest"' in html
    assert 'id="listing-stat-oldest"' in html
    assert 'id="listing-stat-age-known"' in html
    assert "Youngest Vinted" in html
    assert "Oldest Vinted" in html
    assert '<option value="newest">Youngest posting first</option>' in html
    assert '<option value="oldest">Oldest posting first</option>' in html
    assert "function listingAgeSeconds(row)" in APP_JS
    assert "function listingAgeLabel(row)" in APP_JS
    assert 'return (approximate ? "≈ " : "") + days + " day"' in APP_JS


def test_vinted_youngest_and_oldest_ignore_only_rows_without_any_vinted_age():
    assert 'const vinted = rows.filter((row) => row.channel === "vinted");' in APP_JS
    assert ".filter((entry) => entry.ageSeconds != null)" in APP_JS
    assert "const unknownVinted = vinted.length - agedVinted.length;" in APP_JS
    assert '" pending Vinted Uploaded scan"' in APP_JS
    assert '"Exact or Vinted Uploaded age"' in APP_JS


def test_vinted_age_cards_sort_by_exact_or_vinted_relative_age():
    assert '$("#listing-stat-youngest").onclick = () => {' in APP_JS
    assert '$("#listing-channel").value = "vinted";' in APP_JS
    assert '$("#listing-sort").value = "newest";' in APP_JS
    assert '$("#listing-stat-oldest").onclick = () => {' in APP_JS
    assert '$("#listing-sort").value = "oldest";' in APP_JS
    assert "const aa = listingAgeSeconds(a);" in APP_JS
    assert "const ba = listingAgeSeconds(b);" in APP_JS


def test_vinted_age_columns_remain_visible_when_dates_are_unknown():
    assert 'const showDate = filtered.some((row) => row.channel === "vinted")' in APP_JS
    assert 'row.listed_at || null' in APP_JS



def test_relative_vinted_age_never_replaces_exact_listed_at():
    assert "function listingDisplayDate(row)" in APP_JS
    assert "return row.listed_at || null;" in APP_JS
    assert "const relative = Number(row?.listed_age_seconds);" in APP_JS
    assert 'text: "≈ " + dateOnly(new Date(Date.now() - seconds * 1000).toISOString())' in APP_JS
    assert "from Vinted relative age" in APP_JS



def test_dashboard_always_shows_bridge_version_and_versioned_download():
    html = (
        Path(__file__).resolve().parents[1] / "app" / "product_static" / "index.html"
    ).read_text(encoding="utf-8")
    assert 'id="bridge-version-page"' in html
    assert "Bridge v—" in html
    assert '$("#bridge-version-page").textContent = "Bridge v" + (state.me.bridge_version || "unknown");' in APP_JS
    assert "devices.download_url" in APP_JS
    assert "Download bridge v" in APP_JS



def test_chrome_pair_code_renders_inside_vinted_connector():
    html = (
        Path(__file__).resolve().parents[1] / "app" / "product_static" / "index.html"
    ).read_text(encoding="utf-8")
    assert 'id="pairing"' not in html
    assert 'id="pair-code"' not in html
    assert 'class="pairing-inline hidden"' in APP_JS
    assert 'document.querySelectorAll(".pair").forEach((button) => { button.onclick = () => pair(button); });' in APP_JS
    assert 'button?.closest(".connector")' in APP_JS
    assert 'connector?.querySelector(".pair-code-inline")' in APP_JS


def test_page_uploaded_text_is_preserved_in_age_column():
    assert 'String(row.listed_age_source || "").startsWith("vinted_page")' in APP_JS
    assert "return String(row.listed_age_text);" in APP_JS



def test_cross_list_visibility_is_not_gated_by_book_category():
    assert "function inventoryCrossListAction(item)" in APP_JS
    assert "function listingCrossListAction(row)" in APP_JS
    assert 'row.inventory_category === "book"' not in APP_JS
    assert "Cross-list" in APP_JS



def test_cross_list_actions_explain_link_and_destination_gates():
    assert "function listingCrossListAction(row)" in APP_JS
    assert "Link to inventory" in APP_JS
    assert "function linkListingToInventory(listingId)" in APP_JS
    assert "function crossListStatusLabel(status)" in APP_JS
    assert "Needs connection" in APP_JS
    assert "Needs fields" in APP_JS
    assert "Not writable yet" in APP_JS


def test_biblio_preflight_has_direct_stock_repair_and_book_id_override():
    html = (
        Path(__file__).resolve().parents[1] / "app" / "product_static" / "index.html"
    ).read_text(encoding="utf-8")
    assert 'id="biblio-edit-stock"' in html
    assert 'id="biblio-edit-book"' not in html
    assert '["book_id", "Book ID", bookIdValue, sources.book_id, true, true]' in APP_JS
    assert '"unique BIBLIO Book ID"' in APP_JS
    assert 'data.book_id_suggestion' in APP_JS
    assert 'missing.includes("available stock")' in APP_JS


def test_biblio_preflight_shows_automatic_vinted_photo_upload():
    html = (
        Path(__file__).resolve().parents[1] / "app" / "product_static" / "index.html"
    ).read_text(encoding="utf-8")
    assert 'id="biblio-photo-preview"' in html
    assert '["photos", "Photos"' in APP_JS
    assert '" Vinted photo"' in APP_JS
    assert '" - automatic BIBLIO upload"' in APP_JS
    assert "will be uploaded automatically to BIBLIO" in APP_JS
    assert "slice(0, 12)" in APP_JS
    assert "No manual image upload is required." in APP_JS
    assert "source.image_urls" in APP_JS


def test_inventory_items_always_surface_cross_list_action():
    assert "function inventoryCrossListAction(item)" in APP_JS
    assert 'class="btn cross-list"' in APP_JS
    assert "return \"\";" in APP_JS



def test_generic_vinted_relative_age_is_rejected_client_side():
    assert 'row?.channel === "vinted"' in APP_JS
    assert '!String(row?.listed_age_source || "").startsWith("vinted_page")' in APP_JS
    assert '" pending Vinted Uploaded scan"' in APP_JS



def test_cross_list_panel_shows_all_destination_states_and_actions():
    html = (
        Path(__file__).resolve().parents[1] / "app" / "product_static" / "index.html"
    ).read_text(encoding="utf-8")
    assert 'id="cross-list-panel"' in html
    assert "Already listed" in APP_JS
    assert "Needs connection" in APP_JS
    assert "Needs fields" in APP_JS
    assert "Not writable yet" in APP_JS
    assert "cross-destination-publish" in APP_JS
    assert "cross-destination-connect" in APP_JS
    assert "cross-destination-biblio" in APP_JS
    assert "openCrossListConnection(channel)" in APP_JS



def test_cross_list_is_one_universal_action_not_one_column_per_connector():
    html = (
        Path(__file__).resolve().parents[1] / "app" / "product_static" / "index.html"
    ).read_text(encoding="utf-8")
    assert "One Cross-list action handles every destination." in html
    assert "New writable connectors appear here automatically" in html
    assert "function crossListGroup(destination)" in APP_JS
    assert "Ready to publish" in APP_JS
    assert "Needs setup or review" in APP_JS
    assert "Already listed" in APP_JS
    assert "Not writable yet" in APP_JS
    assert "<th>Channels</th><th>Status</th><th>Actions</th>" in APP_JS


def test_biblio_review_gate_is_repaired_inside_cross_list():
    assert 'destination.action === "biblio_classify"' in APP_JS
    assert "Mark as book & review" in APP_JS
    assert 'body: JSON.stringify({ category: "book" })' in APP_JS
    assert "Marked as Book. Review the BIBLIO fields below." in APP_JS
    assert "function reviewBiblioItem" not in APP_JS
    assert "Review BIBLIO" not in APP_JS



def test_biblio_preflight_prepares_isbn_metadata_and_vinted_description():
    assert "ISBN lookup" in APP_JS
    assert "Preparing listing from Vinted, ISBN metadata and master data" in APP_JS
    assert '["description", "Description", fields.description || "", sources.description, true, true]' in APP_JS



def test_biblio_prefilled_fields_remain_editable_and_are_posted_as_reviewed_values():
    assert 'class="biblio-review-input"' in APP_JS
    assert '["title", "Title", fields.title || "", sources.title, true, true]' in APP_JS
    assert '["author", "Author", fields.author || "", sources.author, true, true]' in APP_JS
    assert '["isbn", "ISBN", fields.isbn || "", sources.isbn, true, false]' in APP_JS
    assert '["publisher", "Publisher", enrichment.publisher || "", bibliographicSources.publisher || null, true, false]' in APP_JS
    assert '["edition", "Edition", enrichment.edition || "", bibliographicSources.edition || null, true, false]' in APP_JS
    assert '["publish_date", "Publish date", enrichment.publish_date || "", bibliographicSources.publish_date || null, true, false]' in APP_JS
    assert 'document.querySelectorAll(".biblio-review-input").forEach' in APP_JS
    assert 'const required = field.dataset.required === "true";' in APP_JS
    assert 'payload[field.dataset.field] = value' in APP_JS
    assert "bibliographic_enrichment" in APP_JS



def test_biblio_connections_expose_real_activity_progress_and_recovery_controls():
    assert 'api("/api/app/connectors/biblio/activity")' in APP_JS
    assert "function renderBiblioActivity(activity, operational)" in APP_JS
    assert "View activity" in APP_JS
    assert "Sync changes" in APP_JS
    assert "Retry photos" in APP_JS
    assert "Full resync" in APP_JS
    assert '"/api/app/connectors/biblio/retry-photos"' in APP_JS
    assert '"/api/app/connectors/biblio/full-sync"' in APP_JS
    assert "photos " in APP_JS
    assert "FTP uploaded means the files reached BIBLIO" in APP_JS
    assert "BIBLIO still has to process" in APP_JS


def test_biblio_inventory_and_listing_rows_show_publication_state():
    assert "function biblioListingState(sync)" in APP_JS
    assert "function marketplaceListingBadge(listing)" in APP_JS
    assert '"queued"' in APP_JS
    assert '"uploading"' in APP_JS
    assert '"FTP uploaded"' in APP_JS
    assert "photo error" in APP_JS

    assert "function biblioListingDetails(row)" in APP_JS
    assert "Submitted locally by FTP; not yet verified" in APP_JS
    assert "Verified in BIBLIO inventory" in APP_JS
    assert "remote_mismatch_fields" in APP_JS



def test_biblio_ui_explains_deferred_photo_retry_and_filename_warning():
    assert "photo retry scheduled" in APP_JS
    assert "BIBLIO ignores an image if there is no active listing" in APP_JS
    assert "Photo warning:" in APP_JS
    assert "BookID.jpg, BookID_1.jpg, BookID_2.jpg" in APP_JS
    assert "Multiple photos require BIBLIO to map" in APP_JS



def test_cross_list_preview_surfaces_all_prefilled_reusable_metadata():
    for label in (
        "ISBN", "Barcode", "Author", "Publisher", "Edition", "Published",
        "Language", "Binding", "Pages", "Condition", "Brand", "Size",
        "Colour", "Material",
    ):
        assert f'["{label}",' in APP_JS
    assert '<strong>Prefilled:</strong>' in APP_JS
    assert "data.enrichment_warning" in APP_JS


def test_inventory_edit_form_exposes_all_reusable_identifier_and_book_fields():
    html = (
        Path(__file__).resolve().parents[1] / "app" / "product_static" / "index.html"
    ).read_text(encoding="utf-8")
    for field in (
        "barcode", "author", "isbn", "subtitle", "publisher", "edition",
        "binding", "language", "publish_date", "publication_year", "pages",
    ):
        assert f'name="{field}"' in html
        assert f'"{field}"' in APP_JS


def test_quick_listing_can_capture_and_enrich_all_book_metadata():
    html = (
        Path(__file__).resolve().parents[1] / "app" / "product_static" / "index.html"
    ).read_text(encoding="utf-8")
    for field in (
        "barcode", "author", "isbn", "subtitle", "publisher", "edition",
        "binding", "language", "publish_date", "publication_year", "pages",
    ):
        assert f'name="{field}"' in html
    assert "async function enrichQuickBookFromIsbn()" in APP_JS
    assert 'api("/api/app/stock-intake/barcode/lookup"' in APP_JS
    assert 'fillBlank("binding", metadata.physical_format);' in APP_JS
    assert 'fillBlank("pages", metadata.number_of_pages);' in APP_JS
    assert "if (result.isbn) await enrichQuickBookFromIsbn();" in APP_JS



def test_biblio_review_exposes_every_supported_prefilled_book_field():
    for row in (
        '["subtitle", "Subtitle", enrichment.subtitle || "", bibliographicSources.subtitle || null, true, false]',
        '["publisher", "Publisher", enrichment.publisher || "", bibliographicSources.publisher || null, true, false]',
        '["edition", "Edition", enrichment.edition || "", bibliographicSources.edition || null, true, false]',
        '["binding", "Binding", enrichment.binding || "", bibliographicSources.binding || null, true, false]',
        '["language", "Language", enrichment.language || "", bibliographicSources.language || null, true, false]',
        '["publish_date", "Publish date", enrichment.publish_date || "", bibliographicSources.publish_date || null, true, false]',
        '["pages", "Pages", enrichment.pages || "", bibliographicSources.pages || null, true, false]',
        '["condition", "Condition", enrichment.condition || "", bibliographicSources.condition || null, true, false]',
    ):
        assert row in APP_JS
    assert 'master_barcode: "Master barcode"' in APP_JS
    assert 'vinted_barcode: "Vinted barcode"' in APP_JS
    assert 'review: "Reviewed"' in APP_JS


def test_biblio_extended_upload_profile_is_automatic():
    assert '["upload_profile", "Upload profile (core or extended)"' not in APP_JS
    assert "The extended BIBLIO format is used automatically" in APP_JS
    assert 'data.upload_profile === "core"' not in APP_JS
    assert "The extended BIBLIO format will send" in APP_JS


def test_biblio_connection_is_host_pinned_and_import_is_safe_by_default():
    html = (
        Path(__file__).resolve().parents[1] / "app" / "product_static" / "index.html"
    ).read_text(encoding="utf-8")
    assert '["host", "FTP host"' not in APP_JS
    assert "FTP is locked to BIBLIO's documented ftp.biblio.com host" in APP_JS
    assert 'id="verify-biblio"' in html
    assert 'id="biblio-import-authoritative"' in html
    assert '"/api/app/connectors/biblio/verify"' in APP_JS
    assert "Verification is read-only" in html
    assert "complete active-inventory download" in html
    assert "mark local BIBLIO listings missing from it inactive" in APP_JS
    assert "BIBLIO health:" in APP_JS



def test_connector_forms_prefill_saved_nonsecret_settings_only():
    assert "connector?.saved_values || {}" in APP_JS
    assert 'const value = type === "password" ? "" : (savedValues[name] ?? "");' in APP_JS
    assert "'saved_values':" not in APP_JS



def test_universal_cross_list_reviews_missing_fields_inline_instead_of_leaving_panel():
    html = (
        Path(__file__).resolve().parents[1] / "app" / "product_static" / "index.html"
    ).read_text(encoding="utf-8")
    assert 'id="cross-list-review"' in html
    assert 'id="cross-list-review-name"' in html
    assert 'id="cross-list-review-description"' in html
    assert 'id="cross-list-review-price"' in html
    assert 'id="cross-list-review-stock"' in html
    assert "function openCrossListReview(channel)" in APP_JS
    assert "function publishReviewedCrossList()" in APP_JS
    assert "cross-destination-review" in APP_JS
    assert "Review fields" in APP_JS
    assert "openItemForm(item)" not in APP_JS[
        APP_JS.index("function renderCrossList(data)"):
        APP_JS.index("async function openCrossList(itemId")
    ]


def test_biblio_review_never_falls_back_to_generic_inventory_edit():
    assert 'destination.action === "biblio_classify"' in APP_JS
    assert "Mark as book & review" in APP_JS
    assert "Review / publish" in APP_JS
    assert "Review BIBLIO" not in APP_JS


def test_cross_list_remains_one_action_when_connector_count_grows():
    assert "function inventoryCrossListAction(item)" in APP_JS
    assert "function listingCrossListAction(row)" in APP_JS
    assert 'class="btn cross-list"' in APP_JS
    assert "data-channel=" in APP_JS
    assert "data.destinations" in APP_JS
