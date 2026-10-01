#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_DIR=""
LIVE_CONNECTORS=0

usage() {
  cat <<'EOF'
Usage: scripts/upgrade.sh [--compose-dir DIR] [--live-connectors]

Upgrades a self-hosted installation safely:
  1. git pull --ff-only
  2. validate Compose and require web + worker services
  3. build the new images without starting them
  4. create a consistent SQLite backup with the new image
  5. start web + worker (migrations/backfill run on web startup)
  6. run migration/backfill/worker smoke checks

--live-connectors additionally tests BIBLIO FTP login and reads current eBay
inventory. It does not upload to BIBLIO or modify eBay listings.
EOF
}

while (($#)); do
  case "$1" in
    --compose-dir)
      COMPOSE_DIR="$2"
      shift 2
      ;;
    --live-connectors)
      LIVE_CONNECTORS=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

has_service() {
  local dir="$1" name="$2"
  (cd "$dir" && docker compose config --services 2>/dev/null | grep -Fxq "$name")
}

if [[ -z "$COMPOSE_DIR" ]]; then
  for candidate in "$(dirname "$REPO_ROOT")" "$REPO_ROOT"; do
    if [[ -f "$candidate/docker-compose.yml" || -f "$candidate/compose.yml" || -f "$candidate/compose.yaml" ]]; then
      if has_service "$candidate" "vinted-dashboard"; then
        COMPOSE_DIR="$candidate"
        break
      fi
    fi
  done
fi

if [[ -z "$COMPOSE_DIR" ]]; then
  echo "Could not find a Compose project containing service 'vinted-dashboard'." >&2
  echo "Use --compose-dir /path/to/your/media-stack." >&2
  exit 2
fi

COMPOSE_DIR="$(cd "$COMPOSE_DIR" && pwd)"
echo "Compose project: $COMPOSE_DIR"
echo "Repository:      $REPO_ROOT"

git -C "$REPO_ROOT" pull --ff-only

cd "$COMPOSE_DIR"
docker compose config >/dev/null
SERVICES="$(docker compose config --services)"
if ! grep -Fxq "vinted-dashboard" <<<"$SERVICES"; then
  echo "Compose project does not contain vinted-dashboard." >&2
  exit 2
fi

WORKER_SERVICE=""
if grep -Fxq "vinted-dashboard-worker" <<<"$SERVICES"; then
  WORKER_SERVICE="vinted-dashboard-worker"
elif grep -Fxq "worker" <<<"$SERVICES"; then
  WORKER_SERVICE="worker"
else
  cat >&2 <<EOF
The central Compose project has no dashboard worker yet.

Add the worker service from:
  $REPO_ROOT/docs/media-stack.md

Then rerun:
  $REPO_ROOT/scripts/upgrade.sh --compose-dir "$COMPOSE_DIR"
EOF
  exit 2
fi

echo "Building web and worker..."
docker compose build vinted-dashboard "$WORKER_SERVICE"

echo "Creating pre-upgrade backup..."
docker compose run --rm --no-deps   -e RUN_MIGRATIONS=false   -e RUN_LEGACY_BACKFILL=false   vinted-dashboard python -m app.ops backup

echo "Starting upgraded services..."
docker compose up -d --no-build vinted-dashboard "$WORKER_SERVICE"

echo "Waiting for readiness..."
ready=0
for _ in $(seq 1 45); do
  if docker compose exec -T vinted-dashboard       python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/ready', timeout=3)"       >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if [[ "$ready" != "1" ]]; then
  echo "Dashboard did not become ready. Recent logs:" >&2
  docker compose logs --tail=100 vinted-dashboard "$WORKER_SERVICE" >&2 || true
  exit 1
fi

SMOKE=(python -m app.ops smoke --backfill)
if [[ "$LIVE_CONNECTORS" == "1" ]]; then
  SMOKE+=(--live-connectors)
fi

echo "Running post-upgrade smoke checks..."
docker compose exec -T vinted-dashboard "${SMOKE[@]}"

echo
docker compose ps vinted-dashboard "$WORKER_SERVICE"
echo "Upgrade completed successfully."
