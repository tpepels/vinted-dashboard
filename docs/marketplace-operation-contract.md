# Marketplace relationships and operation contract

This is the product contract for secondary sales channels. **The executable,
reviewed status matrix lives in `app/connectors/development.py`** and is
displayed under **Connections → Marketplace development**. Changing code
does not by itself mark an operation as supported: update the matrix only
after reviewing evidence and tests.

## Definitions

- **Master stock**: one `InventoryItem` represents one physical copy, its
  location, quantity, cost and eventual sale. It is not the Vinted listing.
- **Market listing**: `ChannelListing` is the advertisement in one channel.
  Identity is `(workspace_id, channel, external_id)`. Many marketplace
  listings can refer to one physical `InventoryItem`. A listing also belongs
  to a marketplace `ChannelAccount` if one is available.
- **Source copy**: Vinted can supply title, price, condition and photos, but
  those values are inputs to a channel-specific listing, not remotely
  confirmed state.
- **Sale**: `Sale` identity is `(workspace_id, channel, direction,
  external_order_id)`. Consuming stock twice for duplicate notifications is
  prohibited. Ambiguous sale→master associations require reconciliation.
- **Close action**: `CrossChannelAction` records the target listing,
  triggering sale, outcome, retry and whether execution is remote or manual.
  No remote destructive operation should be inferred from one generic
  `SYNC_INVENTORY` flag.
- **Run and verification evidence**: `BackgroundJob` queues work and
  `ConnectorSyncRun` records attempts. An HTTP success, FTP write or
  local-state update is *not* verification that a marketplace published the
  resulting listing/photograph.

Imported marketplace listings without an existing matching SKU historically
created provisional `InventoryItem` rows. Newly created provisional rows
are tagged `attributes.connector_import_placeholder=true`; do not treat
these as independently verified physical copies or silently merge by title.
Old rows may not be marked and still require manual stock reconciliation.

## Operations and definition of done

| Operation | Acceptance requirement |
| --- | --- |
| Connect | Valid credentials, permission checks, safe failure feedback, revocation |
| Read listings | Correct pagination, stable remote IDs, full vs incremental snapshot rules |
| Publish | Target channel validations, cross-list preview, idempotency, remote ID, no duplicate listing |
| Update | Diff meaningful fields, update same remote ID, durable progress/error |
| Photographs | Source inventory, deterministic photo names/order, upload trace, *remote* processed-image verification when possible |
| Close | Idempotent remote close or explicit manual action, no sold stock resurrection |
| Read orders | Idempotent order import and reliable item link; no speculative sale matching |
| Stock sync | One master quantity propagated according to channel contract, race-safe |
| Verify remote | Compare expected and actually observed remote listing/fields/images; signal drift |
| Reconcile sale | Consume physical stock once and close all other active linked listings or flag required manual work |

Statuses in the matrix mean: **implemented** = an implemented application path,
**partial** = incomplete end-to-end, **manual** = human-operated, **blocked** =
external approval/private protocol needed, **missing** = not implemented.
They are *not* live account certification. Connection configured/operational,
last successful sync and latest job result are displayed separately, as are
per-market physical-stock reference counts.

## Current limitations driving the roadmap

1. **BIBLIO**: full listing FTP export, delta updates, photo retry and sold
   record closure exist. It cannot automatically fetch BIBLIO inventory/orders
   or verify BIBLIO's final image count. Manual inventory import and
   remote-listing comparison exist; photo processing still needs seller UI.
2. **eBay**: active inventory import and `EndItem` are implemented. eBay
   **order import, new listing publishing, price/quantity update** are not.
   The legacy broad `FETCH_ORDERS` flag was incorrect and is now removed.
3. **Etsy**: OAuth, active listings and receipt/order reads exist. Remote
   create/edit/end operations are not implemented.
4. **WooCommerce / Shopify / Wix**: remote create, inventory and order
   reads exist. General edit/update and automatic close after sales do not.
   Photo upload during creation does not prove later media synchronization.
5. **BigCommerce / Squarespace**: read products and orders. Publishing and
   updates are not wired through cross-listing.
6. **Depop**: import code exists, but partner access must be granted by
   Depop; do not describe this as generally usable.
7. **Vinted**: paired Chrome source of listings and order signals. Automated
   write/close operations are intentionally not offered.

## Verification process for each marketplace

For each operation, record:
1. Exact connector and API path used; unit/regression tests.
2. Test account/environment and credential/scope readiness (never secrets).
3. Inputs and intended remote effects.
4. Remote readback/evidence; mark *not verified* when only transfer acceptance
   is visible.
5. Failures, retries, idempotency, and risk to live listings/stock.
6. Any external prerequisites and known undocumented behavior.

Only then can code coverage be upgraded to a verified end-to-end behavior.
Verification must be evidence-based, and publish/delete tests should only
affect expressly designated test listings.
