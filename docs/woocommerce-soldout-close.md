# WooCommerce sold-out product closure (manual, verified)

A physical copy sold on a different marketplace may leave its WooCommerce
listing published. The existing cross-channel sale reconciliation creates a
manual closure task. This feature adds an **optional, explicit** remote action
for a single WooCommerce simple product. It does not automatically close
WooCommerce items on sale import.

## User workflow

1. Link the WooCommerce listing to the physical inventory item using its
   product ID and matching SKU. Ensure sale reconciliation has an actual
   confirmed consuming sale and has created a sold-out closure task.
2. In **Inventory → Marketplaces**, expand **Manage this listing** on the
   WooCommerce row and select **Check publication status** under **After a
   sale**. This only reads the remote product.
3. If the product is still `publish`, select **Unpublish this product…**
   and confirm the change. The dashboard sends only
   `{"status":"draft"}` for that product.
4. The operation succeeds only when a separate remote read confirms that
   exact product is now a draft. Physical master stock, product data,
   pricing and photos are not changed.

If the product is already a draft when checked, the read-only check can
resolve the closure task without any new write. A product with `private`
or `pending` status requires manual review.

## Safety boundary

- Requires a physically authoritative master item, quantity zero after
  recomputation, a confirmed sale consuming the item, a pending task and
  **exactly one** linked WooCommerce simple product with a confirmed SKU.
- Variations and variable parent products are **not** supported for closure.
  Other marketplaces remain on the existing manual/connector paths.
- The publication snapshot expires after **5 minutes**. The write uses an
  independent preflight GET, matching fingerprint, one status-only PUT and
  remote readback. A stale or changed snapshot blocks the write.
- The operation is recorded under `MarketplaceOperation` with a unique
  `:unpublish` target suffix. Remote ambiguity is recorded as attention;
  no automatic retries. A new remote publication check must resolve it.
- A sale refund or new stock during remote processing is surfaced as a
  manual reopening requirement, not claimed as an automatic republish.
- WooCommerce does not provide a general compare-and-swap update guarantee
  for product status. A concurrent change between preflight and PUT remains
  a possible race. Live-store acceptance testing is still required.

## API

- `POST /api/app/inventory/{item_id}/marketplaces/woocommerce/check-close`
  reads and records publication status. It also resolves an uncertain
  earlier write if the remote product is now draft.
- `POST /api/app/inventory/{item_id}/marketplaces/woocommerce/close`
  requires a recent published check and explicit frontend confirmation.
- `GET /api/app/inventory/{item_id}/marketplace-status` exposes the task
  and the checked publication status for the relevant per-item UI.

Validation: `tests/test_woocommerce_close.py`,
`tests/test_woocommerce_close_api.py`, and
`scripts/test_marketplace_close_ui.js`. All remote writes in tests are
mocked; do not interpret passing CI as live WooCommerce certification.

References:
- https://developer.woocommerce.com/docs/apis/rest-api/v3/products/
- https://developer.woocommerce.com/docs/apis/store-api/resources-endpoints/products/
