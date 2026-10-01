# Security and beta launch checklist

Before giving the hosted beta to external users:

- set \`APP_ENV=production\` so insecure fallback configuration fails startup;
- use HTTPS and \`COOKIE_SECURE=true\`;
- use managed PostgreSQL rather than the local SQLite file;
- set a dedicated Fernet \`APP_ENCRYPTION_KEY\`; do not use derived-key mode;
- disable \`LEGACY_UI_ENABLED\`, \`LEGACY_API_ENABLED\` and
  \`LEGACY_COMPAT_SYNC\`;
- publish the final privacy policy and company/controller contact details;
- configure backup/restore for PostgreSQL;
- create the Chrome Web Store listing and review its data-use disclosure;
- verify extension permissions still match the documented single purpose;
- configure billing only when plan IDs/prices have actually been chosen;
- keep marketplace, Stripe and Chrome publishing credentials in deployment/CI
  secrets, never in the repository;
- run \`python scripts/hosted_smoke.py https://your-domain\` and verify the worker heartbeat;
- test account data export and workspace deletion in staging;
- review Vinted/platform terms before expanding browser-side actions.

The commercial Store extension deliberately does not ship the personal
rendered-market-search module or unattended destructive marketplace actions.
