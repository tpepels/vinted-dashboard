# Read-only store stock audit

Open **Inventory → Store stock check → Check linked store stock**.

The dashboard queues a worker task to compare the confirmed physical quantity
with one linked WooCommerce, Shopify or Wix stock record. Each remote check
is **read-only**. It never updates marketplace stock, local physical stock,
prices, listing details or listing visibility.

The results prioritize differences, inaccessible records, ambiguous links and
changes made while checking. Matching records remain collapsed. Select **Review
item** to inspect an individual item's linked listings and, when permitted,
perform an explicitly confirmed marketplace stock update.

## Safety and operational limits

- Only physically confirmed master inventory with linked supported store
  listings is eligible. Provisional items and unlinked listings are excluded.
- Multiple linked listings for the same item/channel are reported for review,
  never guessed or queried as one record.
- Each run is capped at 100 linked listings; larger workspaces receive a clear
  error instead of a silent partial scan.
- Jobs are durable and coalesce identical pending scans. A configured worker
  process is required to process the queue. The UI polls a workspace-scoped
  summary while the job runs.
- Each result records its exact job ID, checked timestamp, observed local and
  remote quantities and state. An item/link changed during or after a check is
  marked stale rather than verified.
- Per-listing remote read failures are recorded without canceling the rest of
  the run; exceptions from the HTTP transport are never shown verbatim.
- The audit cannot prove that a product is publicly visible or purchasable.
  Matching quantities only confirm observed stock data.
- New scans are manual. There is no automatic cross-market stock propagation.

This feature complements **Check stock** and **Set stock** on an individual
inventory item's marketplace panel. The latter is a separate, explicit remote
write subject to that marketplace's identity and readback safeguards.
