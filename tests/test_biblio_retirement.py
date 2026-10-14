"""BIBLIO retirement: preserve historical records, remove UI, block all outbound FTP."""

from pathlib import Path

import pytest

from app import db, jobs, models, worker
from app.cross_channel import REMOTE_CHANNELS
from app.constants import Channel
from app.product_models import BackgroundJob
from app.connectors.hosted import _connect_biblio_ftp

ROOT = Path(__file__).resolve().parents[1]


def test_biblio_transport_is_permanently_retired(monkeypatch):
    # Even credentials from a still-present workspace must never open a socket.
    import ftplib

    def forbidden(*_args, **_kwargs):
        pytest.fail("Retired BIBLIO unexpectedly attempted a network connection")

    monkeypatch.setattr(ftplib, "FTP", forbidden)
    monkeypatch.setattr(ftplib, "FTP_TLS", forbidden)
    with pytest.raises(RuntimeError, match="integration has been removed"):
        _connect_biblio_ftp({"username": "archived", "password": "archived"})


def test_retired_biblio_worker_handler_does_not_send():
    assert worker.handle({
        "job_type": "biblio_sync",
        "workspace_id": None,
        "payload": {"listing_id": "legacy"},
    }) == {"skipped": True, "reason": "BIBLIO integration removed"}


def test_historical_biblio_jobs_are_cancelled_without_retries():
    with db.session_scope() as session:
        workspace = models.Workspace(name="Retired BIBLIO", slug="retired-biblio-test")
        session.add(workspace)
        session.flush()
        workspace_id = workspace.id

    job_id = jobs.enqueue("biblio_sync", {"listing_id": "archived"}, workspace_id)
    claimed = jobs.claim_one()
    assert claimed["id"] == str(job_id)
    assert worker._biblio_job_held_or_skipped(claimed) is True
    with db.session_scope() as session:
        row = session.get(BackgroundJob, job_id)
        assert row.status == "success"
        assert row.attempts == 1

    assert Channel.BIBLIO not in REMOTE_CHANNELS



def test_biblio_is_not_a_visible_marketplace():
    html = (ROOT / "app" / "product_static" / "index.html").read_text()
    js = (ROOT / "app" / "product_static" / "app.js").read_text()
    bridge = (ROOT / "app" / "bridge_api.py").read_text()
    assert 'data-view="connector-biblio"' not in html
    assert '<option value="biblio">' not in html
    assert '"vinted", "biblio", "ebay"' not in js
    assert '.filter(destination => destination.channel !== "biblio")' in js
    assert 'row.channel !== "biblio"' in js
    assert 'jobs.enqueue_unique(\n                "biblio_sync"' not in bridge
