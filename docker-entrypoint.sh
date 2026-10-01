#!/usr/bin/env bash
set -euo pipefail
cd /app

if [[ "${RUN_MIGRATIONS:-true}" == "true" ]]; then
  echo "entrypoint: applying database migrations..."
  attempt=0
  max_attempts=30
  until alembic upgrade head; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge "$max_attempts" ]; then
      echo "entrypoint: migrations failed after ${max_attempts} attempts, giving up" >&2
      exit 1
    fi
    echo "entrypoint: migration attempt ${attempt} failed, retrying in 2s..." >&2
    sleep 2
  done
  echo "entrypoint: migrations up to date."
fi

if [[ "${RUN_LEGACY_BACKFILL:-true}" == "true" ]]; then
  echo "entrypoint: running legacy data backfill (best-effort)..."
  if ! python -m app.legacy_migration; then
    echo "entrypoint: legacy backfill failed or was skipped; continuing startup" >&2
  fi
fi

echo "entrypoint: starting process..."
exec "$@"
