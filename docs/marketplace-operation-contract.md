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


## Phase 1 implementation: canonical physical stock ownership

- Market snapshots **never** claim an existing master item simply because
  `sku`, ISBN or title matches. A new remote identity creates a provisional
  import record with the original SKU preserved in `ChannelListing.external_sku`
  and `connector_import_sku` provenance. Colliding local SKUs are allocated a
  distinct synthetic identifier. Subsequent snapshots of that exact remote ID
  update the same listing and its existing explicit physical link.
- Direct master creation, barcode intake, quick listing creation and confirmed
  file imports establish `stock_authority=physical`. Explicit **Link to
  master** and merge actions also confirm physical ownership. These operations
  preserve source provenance and set a physical stock baseline.
- A verified master quantity is a *physical reading*, not the sum or maximum
  of several marketplace advertisements. Incoming remote quantities and
  statuses do not overwrite it. Consuming order identities (with their
  quantities) are counted once against a stable baseline; a later manually
  entered stock count resets that baseline consistently.
- A provisional record whose final listing moves to another master is
  archived with zero stock, not left as an apparent second active copy.
- Orders resolve by retained explicit link or exact source listing ID first.
  An SKU by itself is insufficient to consume master stock. Historical
  exact-title matching in the existing Vinted reconciliation path is still
  constrained to a single plausible listing and requires separate review if
  ambiguous.
- Reconciliation merges preserve the target SKU and cost, carry all linked
  listings, sales and cross-channel action references, and use the maximum
  independently reported quantity instead of adding duplicate advertisements.
- The **Inventory → Check marketplace relationships** audit reports provisional
  records, legacy unclassified inventory, exact-SKU collisions, active
  same-market duplicate listing links, missing master references and candidate
  pairs for explicit review. It is read-only.
- Existing legacy records are intentionally labelled *legacy unclassified*
  unless they already have reliable provenance. No automatic migration
  claims that an unknown legacy item has been physically verified.

**Phase boundary:** Automatic remote quantity updates, robust reservations,
out-of-order events, multi-line order splits and marketplace-side stock
verification belong to Phases 2, 5 and 8. Phase 1 does not claim those
operations have been implemented or live-certified.


## Phase 2: unified marketplace operation engine (implemented)

### Persisted operation lifecycle

`MarketplaceOperation` holds one workspace-scoped request and its background
job, with one active key per `workspace + channel + type + target`. It
records status, attempt count, safe output summary, timestamps and explicit
remote verification state. A job and its operation are inserted in the **same
database transaction**, preventing a queued job with no tracked operation.

```
queued -> running -> succeeded             (read-only marketplace snapshot)
                  -> needs_verification    (remote create/upload/close accepted)
                  -> failed                (retrievable task failed)
                  -> attention             (remote write outcome uncertain)
```

BIBLIO inventory FTP transfers, updates, photo uploads and delete uploads are
**not** marked remotely verified. The target marketplace must process them.
Successful storefront creates similarly await independent readback.
`succeeded` on an import operation means the remote read and local
snapshot completed, not that every listed product is for sale.

### Supported adapters and safeguards

- **Sync/import**: eBay, Etsy, WooCommerce, Shopify, BigCommerce,
  Squarespace, Wix and Depop reuse their existing supported server workers.
- **BIBLIO**: incremental/full inventory sync, queued listing publishes,
  photo-only upload and targeted retries are all tracked as operations.
  A scheduled delayed photo pickup can be promoted to immediate user retry;
  no second FTP job is created for the same target.
- **Other publishers**: WooCommerce, Shopify and Wix retain synchronous
  preflight/preview/remote create behavior but first reserve a durable
  publish operation. An ambiguous result blocks another create until the
  operator verifies remote state. This protects against duplicate listings.
- **Sold-out closing**: supported eBay and BIBLIO close workers are tracked;
  a failed audited remote close does not auto-retry, because the previous
  request may already have reached the marketplace. Existing manual and
  legacy untracked actions remain supported.
- **Retries**: available only for failed sync, photo and verification
  operations. A retry reuses the same audit record, queues a new
  `BackgroundJob`, and leaves all historical attempt counts intact.
- **Isolation**: operation IDs, listing targets, workspace IDs and retries
  are checked server-side. Payloads deliberately exclude connector secrets.

The dashboard exposes operation history under
**Connections → Marketplace operations**, and an API:

- `GET /api/app/marketplace-operations?channel=biblio&limit=60`
- `GET /api/app/marketplace-operations/{operation_id}`
- `POST /api/app/marketplace-operations/{operation_id}/retry`

### Deliberate limitations

This engine does **not** invent missing remote publish/edit/close
capabilities. There is not yet a generic remote readback verifier for BIBLIO
FTP photographs or deletion records, and there is no legitimate way to
recover automatically from a storefront create where the marketplace may
have succeeded but the local process lost its acknowledgement. Such
operations require explicit remote reconciliation. Detailed per-item
operation controls, searchable logs and workflow UX are Phase 3.

All existing background jobs created before this upgrade remain runnable
without an operation ID. Existing jobs are not retroactively classified
as verified. Migration adds an audit table without rewriting inventory.


## Phase 3: per-item marketplace operations (implemented)

**Inventory → Marketplaces** now opens a view for one physical stock item.
The workspace-scoped `GET /api/app/inventory/{item_id}/marketplace-status`
aggregates its linked listings, local/remote evidence, recent tracked
operations and sold-out follow-up actions. No network transfer or
inventory mutation occurs when reviewing this status.

The user can review each channel's listing identity, known local quantity,
last observed import, BIBLIO seller-download comparison state and recent
transfer errors. Where supported, actions go to the existing safe
publishing preflight, BIBLIO single-book photo inspection, BIBLIO file
comparison or explicit retry of a repeatable failed operation.

The interface deliberately **does not** expose a generic Update/Close
button unless the corresponding safe marketplace adapter exists. A
successful FTP send remains unverified until the separate BIBLIO inventory
download check. Cross-channel close status is visible, while creating
destructive remote closes remains managed by sales reconciliation.

Operational audit data is limited to the requesting workspace. Legacy
untracked marketplace activity is described as untracked, not certified.
