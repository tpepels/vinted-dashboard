"""Auditable secondary-market operation contract.

Status describes *code coverage*, not a claim of successful marketplace
processing. Marketplace access, configuration and remote verification are
reported separately by the API. Keep this registry intentionally explicit:
new connectors or operations must be reviewed, not treated as completed by
generic connector flags.
"""
from __future__ import annotations

from app.constants import Channel
from app.connectors.base import CONNECTORS

VERSION = 1

STATES = {
    "implemented": "A working code path exists; live-account verification is separate.",
    "partial": "Some steps exist, but the end-to-end operation is not complete.",
    "manual": "Human action is required; the dashboard cannot perform this remotely.",
    "blocked": "Access, partner permission, or private protocol is required.",
    "missing": "No supported implementation is present.",
}

OPERATIONS = (
    ("connect", "Connect", "Authenticate, test access, and report failures without exposing secrets."),
    ("read_listings", "Read listings", "Fetch current remote listings with stable remote IDs and reconcile local snapshots."),
    ("publish", "Publish", "Create exactly one remote listing from a linked physical item without duplicates."),
    ("update", "Update", "Change an existing remote listing without creating a second one."),
    ("photos", "Photographs", "Transfer/source photos with per-file outcomes and verify what the marketplace published."),
    ("close", "Close", "End or deactivate one remote listing safely when stock is sold."),
    ("read_orders", "Read orders", "Import seller orders and sales idempotently and associate them with master stock."),
    ("stock", "Stock sync", "Propagate master stock changes without resurrecting sold inventory."),
    ("verify", "Verify remote", "Compare remotely observed listings, fields and photos with local expected state."),
    ("reconcile", "Reconcile sale", "Consume physical stock once and audit all required cross-channel close actions."),
)

# Each code uses I=implemented, P=partial, M=manual, B=blocked,
# N=missing, in exactly the order in OPERATIONS.
# Conservative: an import endpoint is not a publish/update endpoint, and a
# successful FTP upload is not proof BIBLIO made a photo visible.
COVERAGE = {
    Channel.VINTED:       "IIMM PMIPPP".replace(" ", ""),
    Channel.BIBLIO:       "IPIIPIBPPP",
    Channel.EBAY:         "IINNPINPPP",
    Channel.ETSY:         "IINN PMIN PP".replace(" ", ""),
    Channel.WOOCOMMERCE:  "IIIP PPIP PP".replace(" ", ""),
    Channel.SHOPIFY:      "IIIP PPIP PP".replace(" ", ""),
    Channel.BIGCOMMERCE:  "IINN PMIN PP".replace(" ", ""),
    Channel.SQUARESPACE:  "IINN PMIN PP".replace(" ", ""),
    Channel.WIX:          "IIIP PMIP PP".replace(" ", ""),
    Channel.DEPOP:        "BINN PMIN PP".replace(" ", ""),
}
STATE_CODES = {"I": "implemented", "P": "partial", "M": "manual", "B": "blocked", "N": "missing"}

# Cited code locations are paths/symbol names, not external verification.
SYNC_FUNCTIONS = {
    Channel.EBAY: "app.connectors.hosted.sync_ebay_workspace",
    Channel.ETSY: "app.connectors.hosted.sync_etsy_workspace",
    Channel.WOOCOMMERCE: "app.connectors.hosted.sync_woocommerce_workspace",
    Channel.SHOPIFY: "app.connectors.hosted.sync_shopify_workspace",
    Channel.BIGCOMMERCE: "app.connectors.hosted.sync_bigcommerce_workspace",
    Channel.SQUARESPACE: "app.connectors.hosted.sync_squarespace_workspace",
    Channel.WIX: "app.connectors.hosted.sync_wix_workspace",
    Channel.DEPOP: "app.connectors.hosted.sync_depop_workspace",
}
PUBLISH_FUNCTIONS = {
    Channel.BIBLIO: "app.publishing.upsert_biblio_listing; app.connectors.hosted.sync_biblio_workspace",
    Channel.WOOCOMMERCE: "app.connectors.hosted.create_woocommerce_workspace_listing",
    Channel.SHOPIFY: "app.connectors.hosted.create_shopify_workspace_listing",
    Channel.WIX: "app.connectors.hosted.create_wix_workspace_listing",
}
NOTES = {
    (Channel.VINTED, "connect"): "Paired Chrome bridge, not official seller API access.",
    (Channel.VINTED, "publish"): "Create/update through Vinted is deliberately not automated.",
    (Channel.VINTED, "close"): "Closing requires manual acknowledgement; no unattended Vinted action.",
    (Channel.VINTED, "stock"): "Stock is reconciled locally; remote Vinted changes are manual.",
    (Channel.BIBLIO, "read_listings"): "Seller inventory is imported from a user-provided BIBLIO file; there is no automatic remote fetch.",
    (Channel.BIBLIO, "photos"): "FTP sends named JPEGs and reports individual transfers; BIBLIO's processed-photo count cannot be queried.",
    (Channel.BIBLIO, "read_orders"): "BIBLIO Bulk Order Management requires separate enablement and private documentation.",
    (Channel.BIBLIO, "stock"): "Quantity changes are written through inventory FTP; actual remote processing is not confirmed.",
    (Channel.BIBLIO, "verify"): "Manual seller inventory download can verify listing fields, not live image attachments.",
    (Channel.BIBLIO, "reconcile"): "A sale from another linked channel can queue a BIBLIO sold/delete record; BIBLIO sales are not imported automatically.",
    (Channel.EBAY, "read_orders"): "No eBay seller-order importer exists yet, despite the earlier coarse capability flag.",
    (Channel.EBAY, "close"): "EndItem is available when a linked item sells elsewhere; not a general edit/publish adapter.",
    (Channel.EBAY, "stock"): "Can end a sold-out item; routine quantity updates are not implemented.",
    (Channel.WOOCOMMERCE, "update"): "Guarded stock, regular price and title/description edits for eligible linked records; not arbitrary full-product editing.",
    (Channel.WOOCOMMERCE, "close"): "Manual, check-first WooCommerce draft transition for exactly one linked simple product, after a confirmed sold-out sale; no automatic trigger or live-store certification.",
    (Channel.SHOPIFY, "close"): "Manual, check-first Shopify draft transition only when a linked product has exactly one variant; multi-variant products must be handled in Shopify.",
    (Channel.WOOCOMMERCE, "stock"): "Manually triggered physical quantity sync with exact SKU/ID checks, parent verification for variations, and WooCommerce GET readback.",
    (Channel.SHOPIFY, "update"): "Guarded stock and base-price changes for linked variants; not arbitrary full-product editing.",
    (Channel.SHOPIFY, "stock"): "Manual compare-and-set available quantity, idempotent operation key and GraphQL readback; rejects multi-location stock.",
    (Channel.WIX, "update"): "Explicit Wix Catalog V3 stock-only PATCH for one tracked variant/location; other edits unsupported.",
    (Channel.WIX, "stock"): "Manual exact product:variant and SKU check, single-location revision PATCH and remote inventory readback.",
    (Channel.DEPOP, "connect"): "Private Depop Selling API requires approved partner access and valid credentials.",
    (Channel.DEPOP, "read_orders"): "Order importer code exists, but requires Depop partner access.",
}

RELATIONS = (
    ("master", "InventoryItem is the one physical stock/cost record. Never count a second marketplace listing as a second physical copy."),
    ("listing", "ChannelListing identifies one advertisement by workspace + channel + external ID. It can point to one master item and one channel account."),
    ("source", "A Vinted source can provide listing metadata and photographs; it does not establish that another marketplace accepted them."),
    ("order", "An imported Sale is idempotent by workspace + channel + direction + external order ID and must be linked to a master item before destructive actions."),
    ("action", "CrossChannelAction records closure work per linked listing: queued, completed, failed, or needing manual attention."),
    ("proof", "ConnectorSyncRun/job success is transport or local synchronization evidence, not proof of remote listing or image publication."),
)

def contract() -> dict:
    """Static, reviewable, JSON-safe contract. Never infer live readiness here."""
    result = []
    names = [row[0] for row in OPERATIONS]
    for channel, codes in COVERAGE.items():
        if len(codes) != len(names):
            raise ValueError(f"Invalid operation matrix length for {channel}")
        info = CONNECTORS[channel]
        operations = {}
        for key, code in zip(names, codes):
            status = STATE_CODES[code]
            evidence = None
            if key == "read_listings" and channel in SYNC_FUNCTIONS:
                evidence = SYNC_FUNCTIONS[channel]
            elif key == "read_orders" and channel in SYNC_FUNCTIONS:
                evidence = SYNC_FUNCTIONS[channel] + " -> record_workspace_channel_orders"
            elif key == "publish":
                evidence = PUBLISH_FUNCTIONS.get(channel)
            elif key == "update" and channel == Channel.SHOPIFY:
                evidence = "app.connectors.hosted.update_shopify_workspace_stock"
            elif key == "stock" and channel == Channel.SHOPIFY:
                evidence = "app.product_api.update_shopify_item_stock; GraphQL read -> CAS -> read"
            elif key == "update" and channel == Channel.WIX:
                evidence = "app.connectors.wix_stock.update_wix_workspace_stock"
            elif key == "stock" and channel == Channel.WIX:
                evidence = "app.product_api.update_wix_item_stock; variant + inventory read -> PATCH revision -> read"
            elif key == "update" and channel == Channel.WOOCOMMERCE:
                evidence = "app.connectors.hosted.update_woocommerce_workspace_stock"
            elif key == "stock" and channel == Channel.WOOCOMMERCE:
                evidence = "app.product_api.update_woocommerce_item_stock; remote GET -> PUT -> GET"
            elif key == "update" and channel == Channel.BIBLIO:
                evidence = "app.connectors.hosted.sync_biblio_workspace (signature-based incremental FTP)"
            elif key == "close" and channel == Channel.WOOCOMMERCE:
                evidence = "app.product_api.unpublish_woocommerce_item_after_sale; app.connectors.woocommerce_close"
            elif key == "close" and channel == Channel.SHOPIFY:
                evidence = "app.product_api.unpublish_shopify_item_after_sale; app.connectors.shopify_close"
            elif key == "close" and channel in {Channel.BIBLIO, Channel.EBAY}:
                evidence = "app.cross_channel.execute_action; app.connectors.hosted.close_" + channel + "_workspace_listing"
            elif key == "photos" and channel == Channel.BIBLIO:
                evidence = "app.connectors.hosted.sync_biblio_workspace; app.product_api.biblio_retry_listing_photos"
            elif key == "reconcile":
                evidence = "app.cross_channel.reconcile_sale_state"
            elif key == "read_listings" and channel == Channel.BIBLIO:
                evidence = "app.connectors.hosted.import_biblio_workspace"
            elif channel == Channel.VINTED and key in {"read_listings", "read_orders"}:
                evidence = "app.bridge_api.extension_browser_sync; app.workspace_ingest.record_workspace_snapshot"
            operations[key] = {
                "status": status,
                "note": NOTES.get((channel, key), ""),
                "evidence": evidence,
            }
        result.append({
            "channel": channel,
            "display_name": info.display_name,
            "group": info.group,
            "operations": operations,
            "coverage": {
                state: sum(row["status"] == state for row in operations.values())
                for state in STATES
            },
        })
    return {
        "version": VERSION,
        "statuses": STATES,
        "operations": [
            {"key": key, "label": label, "acceptance": acceptance}
            for key, label, acceptance in OPERATIONS
        ],
        "relations": [{"key": key, "rule": description} for key, description in RELATIONS],
        "channels": result,
        "disclaimer": "Implementation status is code audit only. Credentials and last sync show runtime activity; they do not prove marketplace-side publication.",
    }
