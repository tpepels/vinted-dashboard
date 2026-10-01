# Central media-stack integration

The standalone repository already defines a worker. A deployment where
`vinted-dashboard` is embedded in a larger central Compose project must also
define that worker in the central project.

For the existing layout:

```text
~/media-stack/
  docker-compose.yml
  vinted-dashboard/
    .env
    ...
/srv/vinted-dashboard/data/
```

keep the existing `vinted-dashboard` service and add this sibling service
under the same top-level `services:` key:

```yaml
  vinted-dashboard-worker:
    build: ./vinted-dashboard
    restart: unless-stopped
    env_file:
      - ./vinted-dashboard/.env
    environment:
      RUN_MIGRATIONS: "false"
      RUN_LEGACY_BACKFILL: "false"
    volumes:
      - /srv/vinted-dashboard/data:/app/data
    command: ["python", "-m", "app.worker"]
    depends_on:
      vinted-dashboard:
        condition: service_healthy
```

The worker deliberately has no port and no `container_name`. It shares the
same application database/data volume as the web service. Only the web service
runs Alembic migrations and the legacy backfill.

Validate:

```bash
cd ~/media-stack
docker compose config >/dev/null
docker compose config --services | grep -E 'vinted-dashboard|worker'
```

Expected:

```text
vinted-dashboard
vinted-dashboard-worker
```

Then use the repository's upgrade command for subsequent releases:

```bash
cd ~/media-stack/vinted-dashboard
bash scripts/upgrade.sh --compose-dir ~/media-stack
```

For a deeper post-upgrade check that also logs in to BIBLIO FTP and reads the
current eBay seller inventory:

```bash
bash scripts/upgrade.sh --compose-dir ~/media-stack --live-connectors
```

The live connector smoke check never uploads a BIBLIO file and never changes an
eBay listing. eBay's active inventory is read and the resulting snapshot is
stored locally.

## Manual operational checks

Status without changing anything:

```bash
cd ~/media-stack
docker compose exec -T vinted-dashboard python -m app.ops status
```

Migration/backfill/worker smoke test:

```bash
docker compose exec -T vinted-dashboard python -m app.ops smoke --backfill
```

Full connector smoke test:

```bash
docker compose exec -T vinted-dashboard \
  python -m app.ops smoke --backfill --live-connectors
```

If the worker is missing or stopped, the smoke test fails because a queued
`noop` job is not consumed within the timeout.
