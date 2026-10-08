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


## Phase 4: BIBLIO selective photo recovery and verified inventory boundary

The BIBLIO uploader now maintains **per-file FTP transfer receipts** on each
BIBLIO listing: a SHA-256 digest of the original image URL and its exact BIBLIO
filename (including its Book ID and image index). These receipts are evidence
of a successful `STOR` response **only**. They are not proof that BIBLIO's
image processing or public site has displayed the photo.

**Photo repair choices** under Connections → BIBLIO → Fix photos for one book:

- **Check this book's photos** displays source counts, the last transfer
  summary, each filename and whether that precise filename/source pair has a
  successful FTP receipt.
- **Retry failed photo files** is offered when the latest photo state includes
  an error, some files have receipts, and others have no receipts. It resends
  only unconfirmed files, preserving their original indices and filenames.
  This is a transport-recovery operation, not a remote publication verifier.
- **Resend all photos for this book** deliberately ignores existing file
  receipts. Use it if the BIBLIO website shows missing photos even though all
  transfers succeeded or if there are no reliable individual receipts.
- **New books** still receive a delayed complete photo follow-up if images
  were first transferred before BIBLIO had indexed the listing. That first
  transfer intentionally does not create skippable per-file receipts, so a
  delayed retry cannot incorrectly omit photos that BIBLIO never attached.

The targeted retry endpoint accepts `failed_only: true` when available.
It returns HTTP 409 if an incomplete batch cannot be identified safely.
The default `failed_only: false` preserves the existing all-photos retry.
A scheduled retry for the same listing may be promoted to an immediate user
request without queuing duplicate FTP jobs.

Upload logs and operation records include `photos_skipped` when receipts
allowed files to be omitted. The existing full BIBLIO catalogue and
all-photos recovery commands remain explicit and guarded.

**Still blocked externally:** automatic BIBLIO order retrieval depends on
the private Bulk Order Management integration and seller authorization.
FTP inventory/photograph delivery cannot establish remote publication or
order state. These are not advertised as completed capabilities.


## Phase 5: multi-sale stock and destructive closure safety

When an authoritative physical item has several seller-side orders, the
quantity baseline accounts for every consuming sale once (including
multi-unit orders), while the same remote listing may only have one
actionable close at a time.

**Refund/cancellation safety:** all queued closure actions for the physical
item are re-evaluated, not just those created by the order being cancelled.
When stock becomes available, queued closes are cancelled along with their
unstarted background jobs and operation records. A remote close worker
recomputes physical quantity and checks for a consuming sale immediately
before making the network call. Retrying a failed close is rejected when
stock is again available.

A close already sent to a marketplace cannot be automatically undone.
When stock later becomes available, the completed action records
`needs_reopen` and is shown as a manual reconciliation discrepancy in
**Reconcile → Cross-channel actions**, and in that item's marketplace view.
An in-flight close is rechecked when it finishes; if a refund occurred
during the call, it is likewise flagged for manual reopening.

**Not yet implemented:** automatic per-channel stock adjustments/reservations
on APIs without existing write adapters, automatic remote reopening and
marketplace event ordering guarantees. These remain guarded/manual until
supported, and no BIBLIO FTP acknowledgement is called publication proof.


## Phase 6: hosted-store update adapter, first supported slice

WooCommerce simple products and independently stock-managed product variations
now offer a **manual, item-level stock sync**.
The dashboard takes quantity only from confirmed physical stock, resolves one
linked WooCommerce numeric product ID and original SKU, then:

1. GET the product and reject remote ID/SKU drift or unsupported product types.
2. If it already has the desired quantity under stock management, return a
   verified no-op without issuing another write.
3. PUT only `manage_stock`, `stock_quantity` and `stock_status`.
4. GET the same product again and require matching managed quantity/status.
5. Only then update local channel-listing state and mark the marketplace
   operation `remote_verified`. The physical baseline never changes.

A timeout, drift or ambiguous readback is recorded as requiring attention:
the dashboard will not silently retry the remote write or create another
product. **Check WooCommerce stock** performs a GET-only remote observation
and compares it against current physical inventory. A matching check resolves
an ambiguous operation as verified; a mismatch records the discrepancy and
requires an explicit update action. The action is in **Inventory → Marketplaces**
and cannot be used on provisional stock, grouped products, or parent-managed
variations. Variable products are supported only through an exact linked
`parent_product_id:variation_id` identity, a confirmed variation SKU, a GET
of the variable parent, a GET of the specific variation, and subsequent
independent readback. Variations with disabled or parent-managed stock tracking
or enabled backorders are refused; no parent inventory is changed.

Shopify linked variants now also have **Check Shopify stock** (read-only) and
**Set Shopify stock** (explicit write). The adapter requires a confirmed SKU,
Shopify ProductVariant GID, tracked inventory, and **exactly one active stock
location**. It reads `available` for that one location, uses
`inventorySetQuantities` with `changeFromQuantity` compare-and-set plus
a per-operation `@idempotent` key, then independently reads the same variant
and location. Concurrent stock changes, identity drift and multi-location
inventory are refused. A failed or ambiguous write is not automatically retried;
the user must perform a read-only check before a further update. Shopify
requires `read_products`, `read_inventory` and `write_inventory` permission
for this operation. No changes to master physical inventory occur.

**Remaining Phase 6 work:** equivalent tested adapters for Wix,
general WooCommerce variation configuration, remote price/detail updates
and deletion/close support.
Import-only stores remain read-only until supported account permissions and
verified write paths are available.
