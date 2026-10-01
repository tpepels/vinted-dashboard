# Chrome Web Store release

The commercial extension lives in \`app/extension\`. It is a thin Manifest V3
bridge: pairing, Vinted snapshot collection, sync status and manual/periodic
sync. Business logic stays in the web app.

The personal/self-hosted extension is separately preserved in
\`app/legacy_extension\`; its rendered market-research behavior is not part of
the Store package.

## Build

Development:
\`\`\`bash
python scripts/build_extension.py --mode dev --api-origin http://localhost:5050 --output dist/reseller-chrome-bridge-dev.zip
\`\`\`

Store:
\`\`\`bash
python scripts/build_extension.py --mode store --api-origin https://YOUR-DOMAIN --output dist/reseller-chrome-bridge.zip --version 2.0.0
\`\`\`

Store mode rejects non-HTTPS/local API origins, unresolved placeholders,
\`<all_urls>\`, source maps and obvious secret-file extensions.

## Store listing draft

**Short description:** Connect your signed-in Vinted tab to your reseller
workspace for inventory, orders and analytics.

**Single purpose:** Bridge the user's signed-in Vinted session to the user's
paired reseller workspace for inventory, order and analytics synchronization.

Permissions: \`storage\` for the revocable bridge token/status, \`alarms\` for
periodic sync, \`tabs\` to find/open Vinted, \`scripting\` to restore the
bundled content script after installation/update, Vinted host access to read
the user's own signed-in marketplace data, and the production dashboard
origin to send it to the paired workspace.

Before public submission replace the privacy-policy draft with final legal
text and provide final artwork/support URLs.

## Publication

Tag builds package an artifact but do not publish. Chrome Web Store publication
is a separate manual workflow using API v2. Required GitHub secrets:
\`CHROME_PUBLISHER_ID\`, \`CHROME_EXTENSION_ID\`, \`CHROME_CLIENT_ID\`,
\`CHROME_CLIENT_SECRET\`, \`CHROME_REFRESH_TOKEN\`. Set repository variable
\`PUBLIC_APP_URL\`. Create the Store listing once manually before API updates.
