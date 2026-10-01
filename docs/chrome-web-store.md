# Chrome Web Store release

The commercial extension lives in `app/extension`. It has one purpose:
synchronize the reseller data needed by a paired workspace from the user's
signed-in Vinted account. Business logic remains in the web application.

The personal/self-hosted extension is separately preserved in
`app/legacy_extension`. Its legacy market-research behavior is not included
in the Store package.

## Store readiness boundary

The repository can produce and upload a validated Store package, but the
following Chrome Web Store setup remains an external/manual gate:

1. enroll the publisher account and enable Google's required two-step
   verification;
2. create the Store item in the Developer Dashboard;
3. complete the Store Listing and Privacy tabs, including support/contact
   details and final artwork;
4. publish the hosted privacy policy at `https://YOUR-DOMAIN/privacy`;
5. configure GitHub repository variable `PUBLIC_APP_URL`;
6. configure the Chrome Web Store publisher/item/OAuth secrets used by the
   release workflow.

The release automation does not invent legal identity, support contacts or
Store artwork.

## Permissions

The Store manifest deliberately requests only:

- `storage` - stores the revocable bridge token, paired workspace metadata,
  remembered Vinted origin and last-sync status;
- `alarms` - schedules periodic sync;
- `scripting` - re-injects the bundled content script if an installed or
  updated extension has no receiver in an already-open Vinted tab;
- explicit `https://www.vinted.*/*` host permissions for the supported
  Vinted sites;
- the single HTTPS dashboard origin selected at build time.

The extension does not request `tabs`, `cookies`, `webRequest`,
`<all_urls>` or remote-code permissions.

## Data disclosed for the Store privacy form

The bridge may synchronize:

- the user's Vinted listings and listing analytics;
- seller and buyer order/purchase metadata used by the dashboard;
- follower/following counts;
- favourite-event metadata needed by dashboard analytics.

Before upload, unrelated Vinted notification text is discarded. The extension
does not transmit Vinted passwords, raw cookies or general browsing history.

The synchronized information is used only for the disclosed reseller-workspace
functions. It is not sold, used for personalized advertising or transferred
to data brokers. The hosted privacy policy states the Chrome Web Store Limited
Use commitment and the available disconnect/export/delete controls.

## Build

Development:

```bash
python scripts/build_extension.py \
  --mode dev \
  --api-origin http://localhost:5050 \
  --output dist/reseller-chrome-bridge-dev.zip
```

Store:

```bash
python scripts/build_extension.py \
  --mode store \
  --api-origin https://YOUR-DOMAIN \
  --version 2.1.0 \
  --output dist/reseller-chrome-bridge.zip
```

Store mode rejects:

- a missing explicit version;
- non-HTTPS, local or path-bearing API origins;
- manifest versions outside Chrome's numeric format;
- permissions broader than the approved Store set;
- non-HTTPS host permissions or content-script matches outside explicit
  Vinted origins;
- unresolved build placeholders;
- source maps and common secret-file extensions;
- remote script tags and obvious dynamic-code execution.

The ZIP is deterministic. The build command prints its SHA-256 checksum.

## Release workflow

A `v*` tag packages a Store-ready artifact but does not publish it.
A manual **Extension release** workflow can optionally submit the package for
review.

The artifact contains:

- `reseller-chrome-bridge.zip`;
- `reseller-chrome-bridge.zip.sha256`.

The publish job runs in the `chrome-web-store` GitHub environment, verifies
the checksum and then uses the Chrome Web Store API v2. Upload status is polled
until success/failure before a publish request is made.

Required secrets:

- `CHROME_PUBLISHER_ID`;
- `CHROME_EXTENSION_ID`;
- `CHROME_CLIENT_ID`;
- `CHROME_CLIENT_SECRET`;
- `CHROME_REFRESH_TOKEN`.

Use environment protection on `chrome-web-store` if publication should
require an explicit reviewer.

## Store listing copy

**Name:** Reseller Dashboard Chrome Bridge

**Short description:** Connect your signed-in Vinted account to your paired
reseller workspace for inventory, orders and analytics sync.

**Single purpose:** Synchronize the reseller data needed by the user's paired
workspace from the user's signed-in Vinted account.

**Permission justification:** The extension stores a revocable pairing token
and sync status, schedules periodic sync, restores its bundled Vinted content
script when necessary, reads only explicit Vinted origins and sends data only
to the configured HTTPS dashboard origin.

Do not claim Store approval, automated Vinted actions, or data categories that
the package does not actually provide.
