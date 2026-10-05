"""Operational CLI for self-hosted/beta deployments.

This module deliberately avoids exposing secrets. It provides:
- deployment/status verification;
- an end-to-end worker probe;
- idempotent legacy-backfill verification;
- optional live connector smoke checks;
- consistent SQLite backup/restore.

Run inside the application container, e.g.:
    python -m app.ops smoke --backfill
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from app import db, jobs, models
from app.constants import Channel, ListingStatus
from app.legacy_migration import DEFAULT_LEGACY_SQLITE_PATH, run_legacy_backfill
from app.product_models import BackgroundJob, ConnectorCredential, ExtensionCredential


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BACKUP_ROOT = Path(os.getenv("BACKUP_ROOT", "/app/data/backups"))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _age_minutes(value: datetime | None) -> float | None:
    if value is None:
        return None
    return max(0.0, (utcnow() - value).total_seconds() / 60.0)


def _alembic_revisions() -> tuple[str | None, str | None]:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()
    with db.engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    return current, head


def _workspace(session, slug: str | None) -> models.Workspace | None:
    if slug:
        return session.execute(
            select(models.Workspace).where(models.Workspace.slug == slug)
        ).scalar_one_or_none()
    preferred = os.getenv("BOOTSTRAP_WORKSPACE_SLUG", "personal")
    row = session.execute(
        select(models.Workspace).where(models.Workspace.slug == preferred)
    ).scalar_one_or_none()
    if row is not None:
        return row
    return session.execute(
        select(models.Workspace).order_by(models.Workspace.created_at).limit(1)
    ).scalar_one_or_none()


def collect_status(workspace_slug: str | None = None) -> dict[str, Any]:
    current_revision, head_revision = _alembic_revisions()
    url = make_url(db.DATABASE_URL)
    database = {
        "backend": url.get_backend_name(),
        "revision": current_revision,
        "head_revision": head_revision,
        "at_head": bool(current_revision and current_revision == head_revision),
    }
    if url.get_backend_name() == "sqlite":
        database["path"] = url.database

    legacy_path = DEFAULT_LEGACY_SQLITE_PATH
    legacy: dict[str, Any] = {
        "path": str(legacy_path),
        "exists": legacy_path.exists(),
    }
    if legacy_path.exists():
        stat = legacy_path.stat()
        legacy.update({"size_bytes": stat.st_size, "mtime": stat.st_mtime})

    with db.session_scope() as session:
        workspace = _workspace(session, workspace_slug)
        if workspace is None:
            return {
                "ok": False,
                "database": database,
                "legacy": legacy,
                "error": "No workspace exists yet",
            }

        inventory = session.execute(
            select(func.count(models.InventoryItem.id)).where(
                models.InventoryItem.workspace_id == workspace.id
            )
        ).scalar_one()
        sales = session.execute(
            select(func.count(models.Sale.id)).where(
                models.Sale.workspace_id == workspace.id
            )
        ).scalar_one()
        devices = session.execute(
            select(ExtensionCredential).where(
                ExtensionCredential.workspace_id == workspace.id,
                ExtensionCredential.revoked_at.is_(None),
            )
        ).scalars().all()
        latest_device = max(
            (device.last_seen_at for device in devices if device.last_seen_at),
            default=None,
        )
        accounts = session.execute(
            select(models.ChannelAccount)
            .where(models.ChannelAccount.workspace_id == workspace.id)
            .order_by(models.ChannelAccount.channel)
        ).scalars().all()

        connector_rows: list[dict[str, Any]] = []
        for account in accounts:
            listing_rows = session.execute(
                select(models.ChannelListing.status, func.count(models.ChannelListing.id))
                .where(
                    models.ChannelListing.workspace_id == workspace.id,
                    models.ChannelListing.channel == account.channel,
                )
                .group_by(models.ChannelListing.status)
            ).all()
            status_counts = {str(status): int(count) for status, count in listing_rows}
            latest_run = session.execute(
                select(models.ConnectorSyncRun)
                .where(
                    models.ConnectorSyncRun.workspace_id == workspace.id,
                    models.ConnectorSyncRun.channel == account.channel,
                )
                .order_by(models.ConnectorSyncRun.started_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            connector_rows.append(
                {
                    "channel": account.channel,
                    "status": account.status,
                    "last_synced_at": _iso(account.last_synced_at),
                    "last_sync_age_minutes": _age_minutes(account.last_synced_at),
                    "listing_counts": status_counts,
                    "total_listings": sum(status_counts.values()),
                    "latest_run": (
                        {
                            "type": latest_run.run_type,
                            "status": latest_run.status,
                            "started_at": _iso(latest_run.started_at),
                            "error": latest_run.error,
                        }
                        if latest_run else None
                    ),
                }
            )

        latest_backfill = session.execute(
            select(models.LegacyBackfillRun)
            .where(models.LegacyBackfillRun.workspace_id == workspace.id)
            .order_by(models.LegacyBackfillRun.imported_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        queue_counts = dict(
            session.execute(
                select(BackgroundJob.status, func.count(BackgroundJob.id))
                .group_by(BackgroundJob.status)
            ).all()
        )
        stored_credentials = set(
            session.execute(
                select(ConnectorCredential.channel).where(
                    ConnectorCredential.workspace_id == workspace.id
                )
            ).scalars()
        )

        result = {
            "ok": bool(database["at_head"]),
            "database": database,
            "legacy": {
                **legacy,
                "latest_backfill": (
                    {
                        "imported_at": _iso(latest_backfill.imported_at),
                        "source_path": latest_backfill.source_path,
                        "summary": dict(latest_backfill.summary or {}),
                    }
                    if latest_backfill else None
                ),
            },
            "workspace": {
                "id": str(workspace.id),
                "slug": workspace.slug,
                "name": workspace.name,
                "inventory_items": int(inventory or 0),
                "sales": int(sales or 0),
                "active_extension_devices": len(devices),
                "latest_extension_seen_at": _iso(latest_device),
                "latest_extension_age_minutes": _age_minutes(latest_device),
            },
            "connectors": connector_rows,
            "stored_connector_credentials": sorted(stored_credentials),
            "background_jobs": {str(k): int(v) for k, v in queue_counts.items()},
        }
    return result


def _print_status(result: dict[str, Any]) -> None:
    database = result["database"]
    db_state = "OK" if database.get("at_head") else "FAIL"
    print(
        f"[{db_state}] database {database['backend']} "
        f"revision={database.get('revision')} head={database.get('head_revision')}"
    )
    if database.get("path"):
        print(f"     path: {database['path']}")
    legacy = result.get("legacy", {})
    print(
        f"[{'OK' if legacy.get('exists') else 'SKIP'}] legacy database "
        f"{legacy.get('path')}"
    )
    latest = legacy.get("latest_backfill")
    if latest:
        print(f"     latest backfill: {latest['imported_at']} {latest['summary']}")

    workspace = result.get("workspace")
    if not workspace:
        print(f"[FAIL] {result.get('error', 'No workspace')}")
        return
    print(
        f"[OK] workspace {workspace['slug']}: "
        f"{workspace['inventory_items']} items, {workspace['sales']} orders"
    )
    if workspace["active_extension_devices"]:
        age = workspace.get("latest_extension_age_minutes")
        detail = f"{age:.1f}m ago" if age is not None else "never"
        print(
            f"[OK] Chrome bridge: {workspace['active_extension_devices']} active device(s), "
            f"last seen {detail}"
        )
    else:
        print("[WARN] Chrome bridge: no active paired device")

    for connector in result.get("connectors", []):
        age = connector.get("last_sync_age_minutes")
        age_text = f"{age:.1f}m ago" if age is not None else "never"
        print(
            f"[INFO] {connector['channel']}: account={connector['status']}, "
            f"listings={connector['total_listings']}, last sync={age_text}, "
            f"statuses={connector['listing_counts']}"
        )
    print(f"[INFO] background jobs: {result.get('background_jobs', {})}")


def verify_backfill(workspace_slug: str | None = None) -> dict[str, Any]:
    path = DEFAULT_LEGACY_SQLITE_PATH
    if not path.exists():
        return {
            "ok": True,
            "skipped": True,
            "reason": f"Legacy database does not exist at {path}",
        }
    with db.session_scope() as session:
        summary = run_legacy_backfill(session, legacy_path=path)
    return {
        "ok": True,
        "skipped": False,
        "source": str(path),
        "changes": summary.as_dict() if summary is not None else {},
    }


def probe_worker(timeout_seconds: int = 20) -> dict[str, Any]:
    job_id = jobs.enqueue("noop", {"probe": True})
    deadline = time.monotonic() + max(1, timeout_seconds)
    last_status = "queued"
    while time.monotonic() < deadline:
        with db.session_scope() as session:
            job = session.get(BackgroundJob, job_id)
            if job is None:
                return {"ok": False, "error": "Probe job disappeared"}
            last_status = job.status
            if job.status == "success":
                return {
                    "ok": True,
                    "job_id": str(job_id),
                    "completed_at": _iso(job.completed_at),
                }
            if job.status == "error":
                return {
                    "ok": False,
                    "job_id": str(job_id),
                    "error": job.last_error or "Worker probe failed",
                }
        time.sleep(0.5)
    return {
        "ok": False,
        "job_id": str(job_id),
        "error": f"Worker did not process probe within {timeout_seconds}s (status={last_status})",
    }


def _connector_account(workspace_slug: str | None, channel: str) -> tuple[uuid.UUID, str]:
    with db.session_scope() as session:
        workspace = _workspace(session, workspace_slug)
        if workspace is None:
            raise RuntimeError("No workspace exists")
        account = session.execute(
            select(models.ChannelAccount).where(
                models.ChannelAccount.workspace_id == workspace.id,
                models.ChannelAccount.channel == channel,
            )
        ).scalar_one_or_none()
        return workspace.id, account.status if account else "missing"


def live_connector_checks(workspace_slug: str | None = None) -> list[dict[str, Any]]:
    """Perform safe live checks.

    BIBLIO uses an FTP login/PWD only; it uploads nothing.
    eBay fetches active seller inventory and updates only the local snapshot;
    it does not mutate the eBay account.
    Vinted is browser-mediated, so its check is freshness of persisted bridge
    data rather than a server-side Vinted request.
    """
    results: list[dict[str, Any]] = []
    workspace_id, _ = _connector_account(workspace_slug, Channel.VINTED)

    with db.session_scope() as session:
        account = session.execute(
            select(models.ChannelAccount).where(
                models.ChannelAccount.workspace_id == workspace_id,
                models.ChannelAccount.channel == Channel.VINTED,
            )
        ).scalar_one_or_none()
        vinted_count = session.execute(
            select(func.count(models.ChannelListing.id)).where(
                models.ChannelListing.workspace_id == workspace_id,
                models.ChannelListing.channel == Channel.VINTED,
            )
        ).scalar_one()
        age = _age_minutes(account.last_synced_at) if account else None
    results.append(
        {
            "channel": Channel.VINTED,
            "ok": bool(account and account.last_synced_at),
            "mode": "persisted browser data",
            "listings": int(vinted_count or 0),
            "last_sync_age_minutes": age,
            "warning": (
                "No Vinted browser snapshot has been synced yet"
                if not account or not account.last_synced_at
                else None
            ),
        }
    )

    from app.connectors.hosted import (
        biblio_configured,
        ebay_configured,
        sync_ebay_workspace,
        test_biblio_workspace,
    )

    if biblio_configured(workspace_id):
        try:
            detail = test_biblio_workspace(workspace_id)
            results.append({"channel": Channel.BIBLIO, "ok": True, "mode": "FTP login", **detail})
        except Exception as exc:
            results.append({"channel": Channel.BIBLIO, "ok": False, "mode": "FTP login", "error": str(exc)})
    else:
        results.append({"channel": Channel.BIBLIO, "ok": True, "skipped": True, "reason": "not configured"})

    if ebay_configured(workspace_id):
        try:
            detail = sync_ebay_workspace(workspace_id)
            results.append({"channel": Channel.EBAY, "ok": True, "mode": "read inventory", **detail})
        except Exception as exc:
            results.append({"channel": Channel.EBAY, "ok": False, "mode": "read inventory", "error": str(exc)})
    else:
        results.append({"channel": Channel.EBAY, "ok": True, "skipped": True, "reason": "not configured"})

    return results


def smoke(
    workspace_slug: str | None = None,
    *,
    backfill: bool = False,
    live_connectors: bool = False,
    worker_timeout: int = 20,
) -> tuple[dict[str, Any], bool]:
    result: dict[str, Any] = {"status": collect_status(workspace_slug)}
    failures = not bool(result["status"].get("ok"))

    if backfill:
        try:
            result["backfill"] = verify_backfill(workspace_slug)
        except Exception as exc:
            result["backfill"] = {"ok": False, "error": str(exc)}
        failures = failures or not bool(result["backfill"].get("ok"))

    result["worker"] = probe_worker(worker_timeout)
    failures = failures or not bool(result["worker"].get("ok"))

    if live_connectors:
        result["live_connectors"] = live_connector_checks(workspace_slug)
        failures = failures or any(
            not check.get("ok", False)
            for check in result["live_connectors"]
            if not check.get("skipped")
        )

    # Re-read after the probe/live syncs so reported counts are final.
    result["status_after"] = collect_status(workspace_slug)
    failures = failures or not bool(result["status_after"].get("ok"))
    return result, not failures


def _sqlite_path() -> Path:
    url = make_url(db.DATABASE_URL)
    if url.get_backend_name() != "sqlite" or not url.database:
        raise RuntimeError(
            "Native backup/restore currently supports SQLite. "
            "For PostgreSQL use managed snapshots or pg_dump as documented."
        )
    return Path(url.database)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(str(source))
    destination_conn = sqlite3.connect(str(destination))
    try:
        source_conn.backup(destination_conn)
    finally:
        destination_conn.close()
        source_conn.close()


def backup(output_root: Path = DEFAULT_BACKUP_ROOT) -> dict[str, Any]:
    source = _sqlite_path()
    if not source.exists():
        raise RuntimeError(f"Application database does not exist: {source}")
    stamp = utcnow().strftime("%Y%m%d-%H%M%SZ")
    destination = output_root / stamp
    destination.mkdir(parents=True, exist_ok=False)

    app_copy = destination / "app.sqlite3"
    _sqlite_backup(source, app_copy)
    files = {
        "app.sqlite3": {
            "sha256": _sha256(app_copy),
            "size_bytes": app_copy.stat().st_size,
        }
    }

    legacy_path = DEFAULT_LEGACY_SQLITE_PATH
    if legacy_path.exists() and legacy_path.resolve() != source.resolve():
        legacy_copy = destination / "vinted-history.sqlite3"
        _sqlite_backup(legacy_path, legacy_copy)
        files[legacy_copy.name] = {
            "sha256": _sha256(legacy_copy),
            "size_bytes": legacy_copy.stat().st_size,
        }

    current_revision, head_revision = _alembic_revisions()
    manifest = {
        "created_at": utcnow().isoformat(),
        "database_url_backend": "sqlite",
        "source_database": str(source),
        "legacy_database": str(legacy_path),
        "alembic_revision": current_revision,
        "alembic_head": head_revision,
        "files": files,
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {"ok": True, "backup_dir": str(destination), "manifest": manifest}


def restore(backup_dir: Path, *, confirmed: bool = False) -> dict[str, Any]:
    if not confirmed:
        raise RuntimeError("Restore requires --confirm-restore")
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"Backup manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    app_backup = backup_dir / "app.sqlite3"
    if not app_backup.exists():
        raise RuntimeError("Backup does not contain app.sqlite3")
    expected = ((manifest.get("files") or {}).get("app.sqlite3") or {}).get("sha256")
    if expected and _sha256(app_backup) != expected:
        raise RuntimeError("app.sqlite3 checksum does not match backup manifest")

    destination = _sqlite_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    db.engine.dispose()
    temp = destination.with_suffix(destination.suffix + ".restore")
    if temp.exists():
        temp.unlink()
    _sqlite_backup(app_backup, temp)
    os.replace(temp, destination)

    legacy_backup = backup_dir / "vinted-history.sqlite3"
    legacy_restored = False
    if legacy_backup.exists():
        expected_legacy = (
            ((manifest.get("files") or {}).get("vinted-history.sqlite3") or {}).get("sha256")
        )
        if expected_legacy and _sha256(legacy_backup) != expected_legacy:
            raise RuntimeError("vinted-history.sqlite3 checksum does not match backup manifest")
        legacy_destination = DEFAULT_LEGACY_SQLITE_PATH
        legacy_destination.parent.mkdir(parents=True, exist_ok=True)
        legacy_temp = legacy_destination.with_suffix(legacy_destination.suffix + ".restore")
        if legacy_temp.exists():
            legacy_temp.unlink()
        _sqlite_backup(legacy_backup, legacy_temp)
        os.replace(legacy_temp, legacy_destination)
        legacy_restored = True

    return {
        "ok": True,
        "restored_database": str(destination),
        "legacy_restored": legacy_restored,
        "backup_created_at": manifest.get("created_at"),
    }


def _print_smoke(result: dict[str, Any]) -> None:
    _print_status(result["status"])
    if "backfill" in result:
        check = result["backfill"]
        label = "SKIP" if check.get("skipped") else ("OK" if check.get("ok") else "FAIL")
        print(f"[{label}] backfill: {check.get('changes') or check.get('reason') or check.get('error')}")
    worker = result["worker"]
    print(
        f"[{'OK' if worker.get('ok') else 'FAIL'}] worker: "
        f"{worker.get('job_id') or ''} {worker.get('error') or 'processed noop'}"
    )
    for check in result.get("live_connectors", []):
        label = "SKIP" if check.get("skipped") else ("OK" if check.get("ok") else "FAIL")
        detail = check.get("error") or check.get("reason") or check.get("detail") or ""
        if check.get("channel") == Channel.VINTED:
            detail = (
                f"{check.get('listings', 0)} listings, "
                f"last sync age={check.get('last_sync_age_minutes')}m"
            )
        print(f"[{label}] {check.get('channel')}: {check.get('mode', '')} {detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reseller Dashboard deployment operations")
    parser.add_argument("--workspace", help="Workspace slug (defaults to personal/first workspace)")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show database/workspace/connector deployment status")

    smoke_parser = sub.add_parser("smoke", help="Run deployment smoke checks")
    smoke_parser.add_argument("--backfill", action="store_true", help="Re-run legacy backfill idempotently")
    smoke_parser.add_argument(
        "--live-connectors",
        action="store_true",
        help="Test BIBLIO login and read eBay active inventory",
    )
    smoke_parser.add_argument("--worker-timeout", type=int, default=20)

    backup_parser = sub.add_parser("backup", help="Create a consistent SQLite backup")
    backup_parser.add_argument("--output-root", default=str(DEFAULT_BACKUP_ROOT))

    restore_parser = sub.add_parser("restore", help="Restore a SQLite backup")
    restore_parser.add_argument("backup_dir")
    restore_parser.add_argument("--confirm-restore", action="store_true")

    args = parser.parse_args(argv)

    try:
        if args.command == "status":
            result = collect_status(args.workspace)
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                _print_status(result)
            return 0 if result.get("ok") else 1

        if args.command == "smoke":
            result, ok = smoke(
                args.workspace,
                backfill=args.backfill,
                live_connectors=args.live_connectors,
                worker_timeout=args.worker_timeout,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                _print_smoke(result)
            return 0 if ok else 1

        if args.command == "backup":
            result = backup(Path(args.output_root))
            print(json.dumps(result, indent=2, default=str) if args.json else result["backup_dir"])
            return 0

        if args.command == "restore":
            result = restore(Path(args.backup_dir), confirmed=args.confirm_restore)
            print(json.dumps(result, indent=2, default=str) if args.json else f"Restored {result['restored_database']}")
            return 0

    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
