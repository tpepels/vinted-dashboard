# Architecture and maintenance boundaries

This document records the runtime boundaries established by the Phase 34 code
audit. They are intended to prevent the old personal-dashboard architecture
from growing back into the product.

## Runtime composition

`app.entry` is the only web application composition root.

It mounts:

- `app.product_api` - workspace-scoped product/business endpoints;
- `app.bridge_api` - Chrome bridge pairing, device management, status and
  browser snapshot ingestion;
- `app.product_static` - the current product UI.

There is no classic dashboard runtime and no second Vinted persistence path.

## Data ownership

The ORM workspace database is the runtime source of truth for inventory,
listings, sales, observations, connector state and bridge snapshots.

`app.legacy_migration` is the only supported boundary to pre-workspace SQLite
data. The old file is read-only migration input:

- it is never written by the runtime;
- it is not restored as application state;
- normal application backups contain only the application database;
- backfill is additive and idempotent.

## Marketplace connectors

`app.connectors.hosted` owns connector transport and workspace synchronization.
Encrypted workspace credentials are preferred. For self-hosted compatibility,
BIBLIO/eBay environment credentials may be used only by the bootstrap personal
workspace and still write into the workspace database.

Pure file-format logic should not depend on persistence or transport.
`app.connectors.biblio_format` is the current example.

## Chrome bridge

Bridge responsibilities are deliberately separated:

- `app.bridge_package` - framework-independent package/version generation;
- `app.bridge_api` - HTTP pairing/device/sync API;
- `app/extension/background.js` - Chrome tab/session orchestration;
- `app/extension/content.js` - Vinted snapshot collection;
- `app/extension/vinted_age.js` - pure localized Uploaded-age parsing.

Bridge version comes from `app/extension/manifest.json` and must be visible in
the artifact filename, popup and dashboard.

Vinted posting-age invariants:

- exact `listed_at` comes only from strong Vinted timestamp fields;
- relative age comes only from the rendered Vinted `Uploaded` field;
- `first_seen_at` is never a posting-date fallback;
- generic API date/relative fields are not trusted for posting age.

## Background jobs

`app.worker` uses a dispatch table of focused handlers. New job types should
add a handler rather than another branch chain. Connector jobs are
workspace-scoped; no connector job may fall back to an independent legacy
store.

## Deliberate remaining debt

`app.product_api` still contains several product domains in one large router.
Its bridge concern has been removed, and business logic already delegates to
service modules such as reconciliation, stock intake, publishing, analytics and
connector adapters. Future route decomposition should be done domain by domain
when it reduces coupling; file-size-only refactors are not a goal.

`app.connectors.hosted` similarly contains many independent marketplace
adapters. Each adapter is already workspace-scoped, but future connector work
should prefer one adapter module per marketplace once a connector requires
substantial new behavior.

These are maintainability targets, not legacy compatibility requirements.
