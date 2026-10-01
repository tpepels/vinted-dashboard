from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from sqlalchemy import select

from app import db, jobs, models, ops


def _workspace(slug: str = "personal"):
    with db.session_scope() as session:
        row = models.Workspace(name="Personal", slug=slug, settings={})
        session.add(row)
        session.flush()
        return row.id


def test_collect_status_reports_workspace_and_channels(monkeypatch):
    workspace_id = _workspace()
    with db.session_scope() as session:
        workspace = session.get(models.Workspace, workspace_id)
        item = models.InventoryItem(
            workspace_id=workspace.id,
            sku="SKU-1",
            title="Example",
            category="general",
            quantity=1,
            status="active",
            attributes={},
        )
        session.add(item)
        session.flush()
        account = models.ChannelAccount(
            workspace_id=workspace.id,
            channel="vinted",
            display_name="Vinted",
            status="connected",
            config={},
        )
        session.add(account)
        session.flush()
        session.add(
            models.ChannelListing(
                workspace_id=workspace.id,
                inventory_item_id=item.id,
                channel_account_id=account.id,
                channel="vinted",
                external_id="V-1",
                title="Example",
                status="active",
                quantity=1,
            )
        )

    monkeypatch.setattr(ops, "_alembic_revisions", lambda: ("head", "head"))
    monkeypatch.setattr(ops, "DEFAULT_LEGACY_SQLITE_PATH", Path("/definitely/missing.sqlite3"))

    result = ops.collect_status("personal")
    assert result["ok"] is True
    assert result["workspace"]["inventory_items"] == 1
    assert result["connectors"][0]["channel"] == "vinted"
    assert result["connectors"][0]["listing_counts"]["active"] == 1


def test_worker_probe_detects_running_worker():
    _workspace()

    original_enqueue = jobs.enqueue

    def completing_enqueue(job_type, payload, workspace_id=None):
        job_id = original_enqueue(job_type, payload, workspace_id)

        def complete_later():
            time.sleep(0.05)
            jobs.complete(str(job_id))

        thread = threading.Thread(target=complete_later, daemon=True)
        thread.start()
        return job_id

    jobs.enqueue = completing_enqueue
    try:
        result = ops.probe_worker(timeout_seconds=2)
    finally:
        jobs.enqueue = original_enqueue

    assert result["ok"] is True



def test_worker_style_process_can_claim_queue_job(tmp_path):
    database_path = tmp_path / "worker.sqlite3"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database_path}"

    setup = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app import db; db.create_all()",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert setup.returncode == 0, setup.stderr

    worker_style = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app import jobs; "
                "job_id = jobs.enqueue('noop', {'probe': True}); "
                "job = jobs.claim_one(); "
                "assert job is not None; "
                "assert job['id'] == str(job_id); "
                "jobs.complete(job['id'])"
            ),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert worker_style.returncode == 0, worker_style.stderr


def test_sqlite_backup_and_restore_roundtrip(tmp_path, monkeypatch):
    database_path = tmp_path / "app.sqlite3"
    db.init_engine(f"sqlite:///{database_path}")
    db.create_all()
    _workspace("restore-me")

    legacy_path = tmp_path / "vinted-history.sqlite3"
    legacy_path.write_bytes(b"")
    monkeypatch.setattr(ops, "DEFAULT_LEGACY_SQLITE_PATH", legacy_path)

    # An empty file is not a valid SQLite source. Keep this test focused on
    # the application DB by treating the legacy DB as absent.
    legacy_path.unlink()

    result = ops.backup(tmp_path / "backups")
    backup_dir = Path(result["backup_dir"])
    assert (backup_dir / "app.sqlite3").exists()
    assert (backup_dir / "manifest.json").exists()

    with db.session_scope() as session:
        workspace = session.execute(
            select(models.Workspace).where(models.Workspace.slug == "restore-me")
        ).scalar_one()
        session.delete(workspace)

    with db.session_scope() as session:
        assert session.execute(
            select(models.Workspace).where(models.Workspace.slug == "restore-me")
        ).scalar_one_or_none() is None

    restored = ops.restore(backup_dir, confirmed=True)
    assert restored["ok"] is True

    with db.session_scope() as session:
        workspace = session.execute(
            select(models.Workspace).where(models.Workspace.slug == "restore-me")
        ).scalar_one_or_none()
        assert workspace is not None


def test_restore_requires_explicit_confirmation(tmp_path):
    try:
        ops.restore(tmp_path, confirmed=False)
    except RuntimeError as exc:
        assert "--confirm-restore" in str(exc)
    else:
        raise AssertionError("restore should require explicit confirmation")
