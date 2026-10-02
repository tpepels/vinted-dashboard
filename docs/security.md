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


## Release gate

A public release is not considered ready from configuration alone. Record the
actual PostgreSQL backup mechanism and the date of the most recent successful
restore drill, then run:

```bash
export BACKUP_PROVIDER=render-managed-postgres
export BACKUP_RESTORE_DRILL_AT=2026-10-01
python scripts/release_readiness.py --origin https://your-domain
```

By default the restore drill must be no more than 90 days old. The command also
requires a valid `PRIVACY_CONTACT_EMAIL`, checks that
`EXTENSION_LATEST_VERSION` matches the source extension manifest, verifies the
privacy/Limited Use policy, validates production runtime settings, and runs the
live hosted smoke check when `--origin` is supplied.

The restore-drill date is operator-supplied evidence. The application does not
pretend it can verify a managed provider's backup contents without performing
a restore.
