# WooCommerce regular-price updates

Under **Inventory → Marketplaces → WooCommerce → Regular price**, first select
**Check price**. The dashboard compares the store's current regular price with
the item's **default asking price** (set on the physical inventory record).
Only when the values differ, and the check is less than five minutes old, is
**Set price to …** offered. A confirmation dialog precedes the update.

The remote request updates **only `regular_price`** on the exact linked
WooCommerce simple product or `parent:variation`. No stock, visibility,
description, images, sale prices or scheduled promotions are changed.

## Safeguards

- The physical item, one linked WooCommerce listing, numeric external identity,
  confirmed SKU, default asking price and three-letter item currency are
  required. The item's currency, listing currency (if available), and the
  configured WooCommerce store currency must agree.
- Products with an active sale price, a scheduled sale, an unsupported price
  precision or an effective price different from regular price are refused.
  Resolve such cases directly in WooCommerce.
- A write requires a server-recorded, fresh check. The adapter re-reads the
  exact product before PUT and rejects a price that changed after the check.
- After PUT, the adapter independently reads back the exact product and
  confirms the regular and effective prices match the requested amount.
- Uncertain writes are recorded as needing attention. They must **never**
  automatically retry. Another read-only price check establishes whether the
  previously requested price reached the store before another write.
- Price operations use a separate reservation target from stock operations.
  A stock check cannot resolve or approve an uncertain price write.
- WooCommerce has no native atomic compare-and-set on product prices. A
  remote edit between the preflight GET and PUT remains possible; this is why
  the update is explicit and remote verification is mandatory.
- The configured WooCommerce currency is operator-managed metadata, not a
  live currency confirmation from the store. Keep it correct when configuring
  the connector.
- The item and remote link are rechecked after the remote write. If either
  changed, the result requires reconciliation.

Other marketplaces continue to offer stock-only writes. Bulk price propagation
and automated cross-market price synchronization are not part of this feature.

Official endpoints: WooCommerce REST API v3
`GET/PUT /wp-json/wc/v3/products/{id}` and
`GET/PUT /wp-json/wc/v3/products/{parent_id}/variations/{id}`.
