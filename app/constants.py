"""Shared string constants for the inventory/connector domain model.

These are plain string constants rather than database enums so that adding a
new channel or category later does not require a schema migration. Business
logic should reference these names instead of repeating literal strings.
"""

from __future__ import annotations


class Channel:
    """Known marketplace/connector identifiers."""

    VINTED = "vinted"
    EBAY = "ebay"
    BIBLIO = "biblio"
    ETSY = "etsy"
    WOOCOMMERCE = "woocommerce"
    CSV = "csv"
    EXCEL = "excel"
    MANUAL = "manual"


#: Channels with a real connector implementation today. New connectors are
#: additive: append here and nowhere else should an exhaustive list of
#: channels need updating for basic recognition.
KNOWN_CHANNELS = (
    Channel.VINTED,
    Channel.EBAY,
    Channel.BIBLIO,
    Channel.ETSY,
    Channel.WOOCOMMERCE,
    Channel.CSV,
    Channel.EXCEL,
    Channel.MANUAL,
)


class ItemCategory:
    """Broad master categories. Marketplace-specific taxonomy is retained
    separately in item/listing metadata, so these stay intentionally coarse."""

    BOOK = "book"
    CLOTHING = "clothing"
    ELECTRONICS = "electronics"
    HOME = "home"
    COLLECTIBLES = "collectibles"
    TOYS_GAMES = "toys_games"
    MEDIA = "media"
    SPORTS = "sports"
    BEAUTY = "beauty"
    ART_CRAFTS = "art_crafts"
    GENERAL = "general"


KNOWN_ITEM_CATEGORIES = (
    ItemCategory.BOOK,
    ItemCategory.CLOTHING,
    ItemCategory.ELECTRONICS,
    ItemCategory.HOME,
    ItemCategory.COLLECTIBLES,
    ItemCategory.TOYS_GAMES,
    ItemCategory.MEDIA,
    ItemCategory.SPORTS,
    ItemCategory.BEAUTY,
    ItemCategory.ART_CRAFTS,
    ItemCategory.GENERAL,
)


class ItemStatus:
    ACTIVE = "active"
    SOLD = "sold"
    ARCHIVED = "archived"


class ListingStatus:
    ACTIVE = "active"
    SOLD = "sold"
    ENDED = "ended"
    DRAFT = "draft"
    INACTIVE = "inactive"


class ChannelAccountStatus:
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    ERROR = "error"


class MembershipRole:
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class BillingStatus:
    """Workspace billing state. ``DEV`` is used when billing enforcement is
    disabled (see ``BILLING_ENABLED`` in later phases)."""

    DEV = "dev"
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"


class OrderDirection:
    """Matches the legacy Vinted order direction values ("sell"/"buy") so the
    backfill does not need to translate between vocabularies."""

    SELL = "sell"
    BUY = "buy"


class SyncRunStatus:
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
