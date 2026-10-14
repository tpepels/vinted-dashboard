"""BIBLIO retirement: preserve historical records, remove UI, block all outbound FTP."""

from pathlib import Path

import pytest

from app import worker
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
