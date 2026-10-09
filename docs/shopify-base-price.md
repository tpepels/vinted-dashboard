# Shopify base-price checks and updates

Open **Inventory → Marketplaces → Shopify → Base price** for one linked item.

1. Set the physical inventory item's **default asking price** and **currency**.
2. Select **Check price**. The dashboard reads the exact variant's base price
   and the Shopify shop's currency, without changing remote data.
3. If the prices differ, select **Set price to …** and confirm.
4. The dashboard rechecks the price and variant identity before the mutation,
   updates **only the variant price**, then reads it back.

## Supported scope

- One physically confirmed inventory item linked to exactly one Shopify
  `gid://shopify/ProductVariant/…` identifier in that shop, with a confirmed
  exact SKU.
- The item's currency must match both the linked listing currency (when
  recorded), the configured Shopify currency (when set) and the **live Shopify
  shop base currency**.
- Only active products with a strictly positive base price, at most two
  decimal places and within the dashboard's supported pricing range.
- Variants with a compare-at price are **excluded** because changing their
  base price without adjusting the promotion could misrepresent the offer.
- Price checks are usable for five minutes, and a check for an old product ID,
  SKU, currency or master price cannot authorize a new write.
- The mutation `productVariantsBulkUpdate` uses the exact verified product
  and variant IDs and changes **only** `price`. It does not change `sku`,
  stock, compare-at price, images, descriptions, visibility or publication.
- The mutation is followed by another read of the exact variant. Uncertain
  mutations are **not automatically repeated**; a new read-only price check
  resolves the tracked outcome of the previous attempt.
- Price operations are tracked separately from stock operations. Checking
  stock cannot clear a failed or uncertain price update.

**Limitations:** Shopify does not offer an atomic compare-and-set for this
variant price mutation, so a remote edit between the preflight read and the
mutation could still be overwritten. Shopify Markets contextual/regional
prices, automatic discounts, B2B catalogs and other pricing overrides are
outside this feature: the base variant price is **not** necessarily the
price a particular customer sees. This must not be described as synchronizing
all regional prices. No automatic or bulk price writes are performed.

API: `POST /api/app/inventory/{item_id}/marketplaces/shopify/check-price`
(read-only Shopify GraphQL query), then
`POST /api/app/inventory/{item_id}/marketplaces/shopify/price`
(explicit write and remote readback).

Official Shopify references:
- https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/productVariantsBulkUpdate
- https://shopify.dev/docs/api/admin-graphql/2026-10/objects/ProductVariant
- https://shopify.dev/docs/api/admin-graphql/2026-10/objects/Shop
