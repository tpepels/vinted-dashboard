"""Connector capability registry.

Marketplace/file-specific behavior should stay behind this metadata/adapter
boundary.  A connector advertises capabilities; callers must not infer
behavior from the channel name.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.constants import Channel
from app.workspace_bootstrap import CHANNEL_DISPLAY_NAMES


class Capability:
    IMPORT_INVENTORY = "import_inventory"
    EXPORT_INVENTORY = "export_inventory"
    FETCH_LISTINGS = "fetch_listings"
    FETCH_ORDERS = "fetch_orders"
    SYNC_INVENTORY = "sync_inventory"
    CREATE_LISTING = "create_listing"
    UPDATE_LISTING = "update_listing"
    CLOSE_LISTING = "close_listing"
    ANALYTICS = "analytics"
    BROWSER_ASSISTED = "browser_assisted"


@dataclass(frozen=True)
class ConnectorInfo:
    channel: str
    display_name: str
    group: str
    description: str
    capabilities: frozenset[str]

    @property
    def supports_import(self) -> bool:
        return Capability.IMPORT_INVENTORY in self.capabilities or Capability.FETCH_LISTINGS in self.capabilities

    @property
    def supports_export(self) -> bool:
        return Capability.EXPORT_INVENTORY in self.capabilities or Capability.SYNC_INVENTORY in self.capabilities

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def as_dict(self) -> dict:
        return {
            "channel": self.channel,
            "display_name": self.display_name,
            "group": self.group,
            "description": self.description,
            "capabilities": sorted(self.capabilities),
            "supports_import": self.supports_import,
            "supports_export": self.supports_export,
        }


CONNECTORS: dict[str, ConnectorInfo] = {
    Channel.VINTED: ConnectorInfo(
        channel=Channel.VINTED,
        display_name=CHANNEL_DISPLAY_NAMES[Channel.VINTED],
        group="marketplace",
        description="Vinted inventory and analytics through the paired Chrome bridge.",
        capabilities=frozenset({
            Capability.IMPORT_INVENTORY,
            Capability.FETCH_LISTINGS,
            Capability.ANALYTICS,
            Capability.BROWSER_ASSISTED,
        }),
    ),
    Channel.EBAY: ConnectorInfo(
        channel=Channel.EBAY,
        display_name=CHANNEL_DISPLAY_NAMES[Channel.EBAY],
        group="marketplace",
        description="eBay active-listing import and sold-out close through the seller API; order import is not yet implemented.",
        capabilities=frozenset({
            Capability.IMPORT_INVENTORY,
            Capability.FETCH_LISTINGS,
            Capability.CLOSE_LISTING,
        }),
    ),
    Channel.BIBLIO: ConnectorInfo(
        channel=Channel.BIBLIO,
        display_name=CHANNEL_DISPLAY_NAMES[Channel.BIBLIO],
        group="books",
        description="BIBLIO inventory-file import, FTP publishing/updates and sold-out close; orders require separately enabled Bulk Order Management.",
        capabilities=frozenset({
            Capability.IMPORT_INVENTORY,
            Capability.EXPORT_INVENTORY,
            Capability.SYNC_INVENTORY,
            Capability.CREATE_LISTING,
            Capability.UPDATE_LISTING,
            Capability.CLOSE_LISTING,
        }),
    ),
    Channel.ETSY: ConnectorInfo(
        channel=Channel.ETSY,
        display_name=CHANNEL_DISPLAY_NAMES[Channel.ETSY],
        group="marketplace",
        description="Etsy listings and seller orders through the official Open API v3.",
        capabilities=frozenset({
            Capability.IMPORT_INVENTORY,
            Capability.FETCH_LISTINGS,
            Capability.FETCH_ORDERS,
        }),
    ),
    Channel.WOOCOMMERCE: ConnectorInfo(
        channel=Channel.WOOCOMMERCE,
        display_name=CHANNEL_DISPLAY_NAMES[Channel.WOOCOMMERCE],
        group="store",
        description="WooCommerce products and orders through REST API v3; explicit, readback-verified stock edits for linked simple products only.",
        capabilities=frozenset({
            Capability.CREATE_LISTING,
            Capability.UPDATE_LISTING,
            Capability.IMPORT_INVENTORY,
            Capability.FETCH_LISTINGS,
            Capability.FETCH_ORDERS,
        }),
    ),
    Channel.SHOPIFY: ConnectorInfo(
        channel=Channel.SHOPIFY,
        display_name=CHANNEL_DISPLAY_NAMES[Channel.SHOPIFY],
        group="store",
        description="Shopify variants and orders through GraphQL; manual readback-verified stock edits for single-location linked variants only.",
        capabilities=frozenset({
            Capability.CREATE_LISTING,
            Capability.UPDATE_LISTING,
            Capability.IMPORT_INVENTORY,
            Capability.FETCH_LISTINGS,
            Capability.FETCH_ORDERS,
        }),
    ),
    Channel.BIGCOMMERCE: ConnectorInfo(
        channel=Channel.BIGCOMMERCE,
        display_name=CHANNEL_DISPLAY_NAMES[Channel.BIGCOMMERCE],
        group="store",
        description="BigCommerce catalog and orders through the REST Management API.",
        capabilities=frozenset({
            Capability.IMPORT_INVENTORY,
            Capability.FETCH_LISTINGS,
            Capability.FETCH_ORDERS,
        }),
    ),
    Channel.SQUARESPACE: ConnectorInfo(
        channel=Channel.SQUARESPACE,
        display_name=CHANNEL_DISPLAY_NAMES[Channel.SQUARESPACE],
        group="store",
        description="Squarespace products, inventory and orders through the Commerce APIs.",
        capabilities=frozenset({
            Capability.IMPORT_INVENTORY,
            Capability.FETCH_LISTINGS,
            Capability.FETCH_ORDERS,
        }),
    ),
    Channel.WIX: ConnectorInfo(
        channel=Channel.WIX,
        display_name=CHANNEL_DISPLAY_NAMES[Channel.WIX],
        group="store",
        description="Wix Stores variants, inventory and orders through the current Wix REST APIs.",
        capabilities=frozenset({
            Capability.CREATE_LISTING,
            Capability.IMPORT_INVENTORY,
            Capability.FETCH_LISTINGS,
            Capability.FETCH_ORDERS,
        }),
    ),
    Channel.DEPOP: ConnectorInfo(
        channel=Channel.DEPOP,
        display_name=CHANNEL_DISPLAY_NAMES[Channel.DEPOP],
        group="marketplace",
        description="Depop products and orders through the private Selling API for approved partners.",
        capabilities=frozenset({
            Capability.IMPORT_INVENTORY,
            Capability.FETCH_LISTINGS,
            Capability.FETCH_ORDERS,
        }),
    ),
    Channel.CSV: ConnectorInfo(
        channel=Channel.CSV,
        display_name="CSV / TSV",
        group="files",
        description="Portable CSV/TSV inventory import and export with saved mappings.",
        capabilities=frozenset({
            Capability.IMPORT_INVENTORY,
            Capability.EXPORT_INVENTORY,
        }),
    ),
    Channel.EXCEL: ConnectorInfo(
        channel=Channel.EXCEL,
        display_name="Excel",
        group="files",
        description="XLSX inventory import and export with saved mappings.",
        capabilities=frozenset({
            Capability.IMPORT_INVENTORY,
            Capability.EXPORT_INVENTORY,
        }),
    ),
}


def get_connector(channel: str) -> ConnectorInfo | None:
    return CONNECTORS.get(channel)


def connector_catalog() -> list[dict]:
    return [info.as_dict() for info in CONNECTORS.values()]
