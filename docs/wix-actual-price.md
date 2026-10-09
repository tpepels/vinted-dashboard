# Wix actual-price checks and updates (Catalog V3)

Open **Inventory → Marketplaces → Wix → Actual price** for one linked
physical item.

1. Set the inventory item's **default asking price** and **currency**.
2. Configure the Wix connector's **currency** to the same store currency.
3. Select **Check price**. This reads the exact product and its default
   variant from Wix; it does not write anything.
4. If the prices differ, select **Set price to …**, review and confirm.
5. The dashboard fetches the product again, updates the price using its
   current revision, then independently reads back the exact variant.

## Deliberately limited eligibility

**Only single-variant Catalog V3 products with no options or modifiers** can
be price-updated automatically. Wix's Products V3 update replaces an entire
variant array, and requires its current `revision`. Sending only one variant
of a multi-variant product could remove other variants. More complex products
must be edited in Wix. This restriction is intentional.

Additional safeguards:

- Confirmed physical stock linked to **exactly one** Wix listing in the
  workspace, with canonical `productUUID:variantUUID` identity and exact SKU.
- Positive default price with at most two decimals and a matching currency.
  The product's returned currency, if available, must agree; Wix sometimes
  omits it, so the configured Wix store currency is required.
- The product must contain exactly one default variant with an empty choices
  list; options and modifiers must be empty.
- Reject variants with `compareAtPrice` and additional configurations such
  as revenue details or subscriptions that cannot safely be round-tripped.
- Preserve supported existing variant metadata (SKU, barcode, visibility and
  physical properties) in the revision-checked product update.
- Changes only `price.actualPrice.amount`, not inventory, media, product
  visibility, SKU, description, price comparisons or promotions.
- The dashboard requires a price check no older than five minutes; any
  change to the linked ID, SKU, item price or currency invalidates the check.
- Wix product `revision` is checked by the API, and a successful mutation
  must increment the revision. A fresh GET must return the new price.
- Uncertain writes are never automatically retried. Only a subsequent
  read-only price check reconciles the previously attempted update.
- Stock and price operations remain separately tracked.

The feature never automatically pushes prices and never updates a bulk
catalog. Price changes are explicitly confirmed for each item. Storefront
promotions, taxes, discounts and market-specific offers can still affect
the amount a buyer sees.

Official Wix Catalog V3 references:

- https://dev.wix.com/docs/api-reference/business-solutions/stores/catalog-v3/products-v3/get-product
- https://dev.wix.com/docs/api-reference/business-solutions/stores/catalog-v3/products-v3/update-product

Live Wix store acceptance is required before relying on the write adapter.
