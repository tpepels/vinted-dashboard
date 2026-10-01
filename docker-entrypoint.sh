#!/usr/bin/env bash
# Container entrypoint: apply database migrations, best-effort backfill the
# new ORM schema from any existing legacy sqlite3 data, then hand off to the
# real process (uvicorn by default, see CMD in the Dockerfile).
set -euo pipefail

cd /app

echo "entrypoint: applying database migrations..."
attempt=0
max_attempts=30
until alembic upgrade head; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge "$max_attempts" ]; then
    echo "entrypoint: migrations failed after ${max_attempts} attempts, giving up" >&2
    exit 1
  fi
  echo "entrypoint: migration attempt ${attempt} failed (database may still be starting), retrying in 2s..." >&2
  sleep 2
done
echo "entrypoint: migrations up to date."

# Additive and idempotent: copies any existing legacy sqlite3 data into the
# new schema. Never touches the legacy files, so a failure here must not
# prevent the (still fully functional) application from starting.
echo "entrypoint: running legacy data backfill (best-effort)..."
if ! python -m app.legacy_migration; then
  echo "entrypoint: legacy backfill failed or was skipped; continuing startup" >&2
fi

echo "entrypoint: starting application..."
exec "$@"
