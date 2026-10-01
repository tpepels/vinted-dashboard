# Deployment

## Local / self-hosted

A fresh clone is deliberately one command after creating the environment file:

\`\`\`bash
cp .env.example .env
docker compose up -d --build
\`\`\`

Open \`http://localhost:5050\` (or the server's LAN address). The web container
runs Alembic migrations automatically and backfills an existing legacy
\`vinted-history.sqlite3\` into the workspace schema. The worker container
uses the same database and handles queued server-side jobs.

Health checks:

\`\`\`bash
curl http://localhost:5050/api/health
curl http://localhost:5050/api/ready
docker compose ps
docker compose logs -f vinted-dashboard worker
\`\`\`

SQLite remains the zero-setup default. For the bundled Postgres service set a
strong \`POSTGRES_PASSWORD\`, point \`DATABASE_URL\` at \`db\`, then:

\`\`\`bash
docker compose --profile postgres up -d --build
\`\`\`

## Hosted beta

The application is provider-neutral: one web container, one worker process and
PostgreSQL. The reference \`render.yaml\` shows a Render deployment.

Before a public deployment set at least:

- \`DATABASE_URL\` to managed PostgreSQL
- \`APP_SECRET_KEY\` to a long random value
- \`APP_ENCRYPTION_KEY\` to a Fernet key
- \`PUBLIC_APP_URL=https://your-domain\`
- \`COOKIE_SECURE=true\`
- \`LEGACY_UI_ENABLED=false\`
- \`LEGACY_API_ENABLED=false\`
- \`LEGACY_COMPAT_SYNC=false\`

Generate an encryption key:

\`\`\`bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
\`\`\`

Billing can stay disabled for a private beta. Later enable Stripe with
\`BILLING_ENABLED=true\`, \`STRIPE_SECRET_KEY\`, \`STRIPE_PRICE_ID\` and
\`STRIPE_WEBHOOK_SECRET\`.

### Render reference

1. Create a new Blueprint from this repository using \`render.yaml\`.
2. Set the secret environment variables marked \`sync: false\`.
3. Deploy the web and worker services.
4. Point a custom domain at the web service in Render and update
   \`PUBLIC_APP_URL\` to that HTTPS origin.
5. Set the same origin as GitHub repository variable \`PUBLIC_APP_URL\` for
   Chrome extension release builds.
6. Leave legacy APIs disabled on a public instance.

The first registration creates a new workspace. On an upgraded personal
installation, the first real registration claims the placeholder bootstrap
owner and therefore keeps the migrated historical inventory instead of
starting from an empty workspace.

## Current connector scope

- Vinted: paired Chrome bridge, workspace-scoped inventory/orders/history.
- CSV/TSV/XLSX: workspace-scoped import/export, including channel-specific
  exports.
- BIBLIO: workspace-scoped initial inventory import plus FTP test and
  inventory/delete synchronization.
- eBay: workspace-scoped active seller inventory synchronization.
- The migrated personal/bootstrap workspace can keep using its existing
  environment-backed BIBLIO/eBay setup when no workspace credential has been
  saved.
