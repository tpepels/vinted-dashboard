# Listing content comparison and controlled WooCommerce updates

## Comparing recorded marketplace listings (phase 1)

Open **Inventory → Marketplaces** for a physical book, then expand **Manage this
listing → Compare listing details**.

The view compares the inventory record with the **last imported/saved listing
snapshot** on each connected marketplace. It covers title, description,
asking price, condition, ISBN, author, publisher, edition and language when
these fields exist. A missing field is shown as **Not recorded**, never a
confirmed discrepancy. Imported values do not prove what is currently live.

For a single, confirmed WooCommerce link, use **Check current WooCommerce
details**. This makes an explicit remote GET, confirms the product or variation
identity and SKU, then shows the verified values and timestamp. Only a fresh
verified WooCommerce read can enable an edit. Other channels remain comparison
only until their own safe content adapters are implemented.

The **Asking price** comparison is informational here. Prices retain their
own check-and-update controls. Stock and publication status are not content
fields and are never modified by the content editor.

## Updating WooCommerce details (phase 2)

When a verified field differs and the remote check is less than five minutes
old, select exactly which fields to update.

- **Simple product:** title and/or description.
- **Variation:** description only; product name belongs to its parent and is
  not edited from a variant.
- **Read-only for now:** condition, ISBN, author, publisher, edition, language,
  category/taxonomy, attributes, price, stock, visibility and photos.

The update uses the current **master inventory values**; the browser submits
only the selected field names. The server rechecks the physical item, the
exact product/variation ID, SKU, master-field fingerprint, fresh read timestamp
and selected field support. It reserves a separately tracked
`{listing_id}:content` operation before a remote write.

The WooCommerce adapter then performs another remote GET and refuses to
proceed if the checked content (including the remote modification timestamp,
when provided) changed. A single PUT sends **only selected keys**:
`name` for title and/or `description`. No arrays, stock or pricing fields
are sent. A second GET verifies each updated value; an ambiguous write is
recorded as **needs attention**, never retried automatically.

After an uncertain outcome, use the read-only WooCommerce content check to
reconcile the prior operation before attempting any new edit. Stock checks,
price checks and ordinary imports cannot clear a pending content write.

### Known limits

- WooCommerce REST API does **not** provide atomic compare-and-set of product
  content: a remote edit between the final GET and PUT is still possible.
- WooCommerce descriptions can contain HTML. Comparison is literal, preserving
  markup and avoiding falsely treating formatted content as equivalent. If
  the master description is plain text, updating it replaces the description
  with that selected plain text.
- The master inventory's description comes from
  `item.attributes.description`, not `item.notes`. An absent description
  cannot be selected or pushed. The item's title is its canonical title.
- Live-store acceptance testing remains required before relying on writes in
  production. No automatic or bulk content propagation is enabled.

Endpoints:

- `GET /api/app/inventory/{item_id}/content-comparison` — saved comparisons
  and provenance; no marketplace network requests
- `POST /api/app/inventory/{item_id}/marketplaces/woocommerce/check-content`
  — explicit read-only live check
- `POST /api/app/inventory/{item_id}/marketplaces/woocommerce/content`
  — explicitly selected `{"fields":["title","description"]}` write

WooCommerce documentation:
- https://developer.woocommerce.com/docs/apis/rest-api/v3/products/
- https://developer.woocommerce.com/docs/apis/rest-api/v3/product-variations/
