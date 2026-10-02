# Backup and restore

## Current self-hosted SQLite deployment

The application can take a consistent online SQLite backup while the dashboard
is running. It uses SQLite's backup API rather than copying a live database
file.

From the central media stack:

```bash
cd ~/media-stack
docker compose exec -T vinted-dashboard python -m app.ops backup
```

Backups are created under:

```text
/app/data/backups/YYYYMMDD-HHMMSSZ/
```

which maps to the persistent host data directory. With the current deployment
that is:

```text
/srv/vinted-dashboard/data/backups/
```

Each backup contains:

- `app.sqlite3` - workspace/auth/master-inventory database;
- `vinted-history.sqlite3` when the legacy history database exists;
- `manifest.json` with timestamps, Alembic revision, sizes and SHA-256
  checksums.

The `.env` file is intentionally not placed in these backups because it may
contain marketplace credentials. Back it up separately in a secure secret
store.

## Restore SQLite

Restoring replaces the application database, so stop web and worker first.

```bash
cd ~/media-stack
docker compose stop vinted-dashboard vinted-dashboard-worker
```

Then run a one-off application container with startup migrations/backfill
disabled:

```bash
docker compose run --rm --no-deps \
  -e RUN_MIGRATIONS=false \
  -e RUN_LEGACY_BACKFILL=false \
  vinted-dashboard \
  python -m app.ops restore \
    /app/data/backups/YYYYMMDD-HHMMSSZ \
    --confirm-restore
```

Restart and verify:

```bash
docker compose up -d vinted-dashboard vinted-dashboard-worker
docker compose exec -T vinted-dashboard python -m app.ops smoke --backfill
```

The restore command checks the manifest SHA-256 checksums before replacing
either SQLite database.

## Before every upgrade

`scripts/upgrade.sh` builds the new image first, uses it to take a
pre-migration backup, starts the new web/worker containers, waits for
`/api/ready`, then runs migration/backfill/worker smoke checks.

This gives one normal upgrade path instead of a different series of commands
for every release:

```bash
cd ~/media-stack/vinted-dashboard
bash scripts/upgrade.sh --compose-dir ~/media-stack
```

## PostgreSQL deployments

Do not use the SQLite backup/restore command for PostgreSQL. Use the managed
database provider's snapshots/backups and test restores periodically. For a
self-managed PostgreSQL deployment use `pg_dump`/`pg_restore` from matching
PostgreSQL client tools.

The operations CLI deliberately refuses to pretend that copying PostgreSQL
files is a valid backup.


## Hosted PostgreSQL restore drill

Before external launch, and at least every 90 days thereafter, test a real
restore rather than only confirming that snapshots exist.

1. Select a recent managed PostgreSQL snapshot/backup.
2. Restore it into a separate temporary database - never over production.
3. Point a temporary web/worker deployment at the restored database.
4. Run migrations only if the restored snapshot predates the current release.
5. Run `python scripts/hosted_smoke.py https://temporary-restore-host`.
6. Verify representative workspace counts and account-data export.
7. Delete the temporary deployment/database after verification.
8. Record `BACKUP_RESTORE_DRILL_AT=YYYY-MM-DD` and identify the mechanism in
   `BACKUP_PROVIDER`.

`scripts/release_readiness.py` uses that dated operational evidence because a
managed-provider backup setting alone does not prove that a restore succeeds.
