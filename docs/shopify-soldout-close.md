# Shopify sold-out unpublishing (explicit, check-first)

When one confirmed physical inventory item is sold on a different channel, the
dashboard records a sold-out closure task. The Shopify closure control is
**opt-in** and requires an explicit user confirmation; importing a sale never
automatically unpublishes a Shopify product.

## Why only single-variant products?

Shopify products can contain several saleable variants. Changing the product
status to `DRAFT` hides **every variant**, not just the selected SKU.
Therefore the connector requires exactly one variant on the product and its
GraphQL ID/SKU must match the linked inventory record. If there are two or
more variants, the check refuses closure and the user must resolve it in
Shopify manually.

## User flow

1. Confirm the physical inventory and link the Shopify ProductVariant ID and
   SKU to the master item. A confirmed consuming sale must leave zero physical
   stock and an outstanding cross-channel closure task.
2. Open **Inventory → Marketplaces → Manage this listing → After a sale**.
   Select **Check publication status**. This is read-only.
3. If the product is active and the inspection is less than five minutes old,
   the **Unpublish this product…** action appears. Confirm that the
   **whole single-variant product** should become a draft.
4. A second, independent check verifies identity, sole-variant count, SKU,
   product status and product update timestamp. The adapter sends one
   Shopify GraphQL `productUpdate(product:{id,status:DRAFT})` mutation.
5. An independent GraphQL read must confirm that the exact single-variant
   product is a draft. Only then is the local closure task completed.

An already-draft product is reconciled read-only; archived/unlisted products
require manual review. Prices, quantities, media, descriptions and physical
master stock are never edited by this feature.

## Safety and limitations

- Requires authoritative physical stock, quantity zero, confirmed consuming
  sale, an outstanding manual closure task, and exactly one linked Shopify
  listing with a ProductVariant GraphQL ID and matching SKU.
- Rejects multiple variants, changed fingerprints, archived/unlisted products,
  missing IDs or SKU, expired inspections, restocked items and unsupported
  publications. API access requires the workspace writer role and CSRF.
- An uncertain response is recorded as an operation requiring attention.
  There are **no automatic retries**. A later read-only check verifies whether
  the product actually became draft.
- A restock during the remote request is recorded as a **manual reopen task**;
  the dashboard does not automatically republish.
- Shopify product updates do not provide a general conditional/compare-and-set
  status mutation. Another actor may change product publication between the
  preflight read and the mutation. Live-shop acceptance testing remains needed.
- The connector must have Shopify product read/write permissions.

## Implementation and tests

- `app/connectors/shopify_close.py` — read-only snapshot and single mutation.
- `app/product_api.py` — `/marketplaces/shopify/check-close` and `/close`.
- `app/product_static/app.js` — check and explicit confirmation in the existing
  Inventory marketplace workspace.
- `tests/test_shopify_close.py`, `tests/test_shopify_close_api.py`,
  `scripts/test_shopify_close_ui.js` — unit/API/UI coverage; all remote calls
  mocked. Do not treat green CI as live Shopify store certification.

Shopify API reference:
- https://shopify.dev/docs/api/admin-graphql/latest/mutations/productUpdate
- https://shopify.dev/docs/api/admin-graphql/latest/enums/ProductStatus
- https://shopify.dev/docs/api/admin-graphql/latest/queries/productVariant
