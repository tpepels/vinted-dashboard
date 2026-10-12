"""Timed BIBLIO pause and auto-sync-off regression tests."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import biblio_pause, db, entry, jobs, models, worker
from app.product_models import BackgroundJob


def setup_client(email):
    client = TestClient(entry.app)
    response = client.post("/api/auth/register", json={
        "email": email,
        "password": "a-long-test-password",
        "workspace_name": "BIBLIO job pause",
    })
    assert response.status_code == 200, response.text
    csrf = response.json()["csrf_token"]
    with db.session_scope() as session:
        workspace = session.execute(select(models.Membership.workspace_id)).scalar_one()
    configured = client.put(
        "/api/app/connectors/biblio/credentials",
        headers={"X-CSRF-Token": csrf},
        json={"values": {"username": "seller", "password": "secret", "auto_sync": "false"}},
    )
    assert configured.status_code == 200, configured.text
    return client, csrf, workspace


def test_one_hour_pause_defers_only_biblio_and_expires_without_restart(monkeypatch):
    client, csrf, workspace = setup_client("pause-automatic@example.test")
    result = client.put(
        "/api/app/connectors/biblio/upload-pause",
        headers={"X-CSRF-Token": csrf},
        json={"minutes": 60},
    )
    assert result.status_code == 200, result.text
    expiry = datetime.fromisoformat(result.json()["paused_until"])
    assert expiry > datetime.now(timezone.utc)
    connector = next(c for c in client.get("/api/app/connectors").json()["connectors"]
                     if c["channel"] == "biblio")
    assert connector["biblio_upload_paused_until"] == expiry.isoformat()

    jid = jobs.enqueue("biblio_sync", {"listing_id": "manual-test"}, workspace)
    claimed = jobs.claim_one()
    assert claimed["id"] == str(jid)
    assert worker._biblio_job_held_or_skipped(claimed) is True
    with db.session_scope() as session:
        deferred = session.get(BackgroundJob, jid)
        assert deferred.status == "queued"
        assert deferred.attempts == 0
        assert deferred.available_at >= expiry
        assert deferred.payload[jobs.BIBLIO_PAUSE_MARKER] is True

    other = jobs.enqueue("noop", {}, workspace)
    next_job = jobs.claim_one()
    assert next_job["id"] == str(other)
    assert worker._biblio_job_held_or_skipped(next_job) is False
    jobs.complete(str(other))

    after_expiry = expiry + timedelta(seconds=2)
    assert biblio_pause.biblio_paused_until(workspace, now=after_expiry) is None
    monkeypatch.setattr(biblio_pause, "utcnow", lambda: after_expiry)
    monkeypatch.setattr(jobs, "utcnow", lambda: after_expiry)
    due = jobs.claim_one()
    assert due["id"] == str(jid)
    assert worker._biblio_job_held_or_skipped(due) is False
    assert jobs.BIBLIO_PAUSE_MARKER not in due["payload"]


def test_early_resume_releases_only_jobs_held_by_pause():
    client, csrf, workspace = setup_client("pause-resume@example.test")
    headers = {"X-CSRF-Token": csrf}
    assert client.put("/api/app/connectors/biblio/upload-pause",
                      headers=headers, json={"minutes": 60}).status_code == 200
    jid = jobs.enqueue("biblio_sync", {"listing_id": "manual-1"}, workspace)
    delayed = jobs.enqueue("biblio_sync", {"listing_id": "manual-2"}, workspace,
                           delay_seconds=7200)
    claimed = jobs.claim_one()
    assert claimed["id"] == str(jid)
    assert worker._biblio_job_held_or_skipped(claimed) is True

    resumed = client.put("/api/app/connectors/biblio/upload-pause",
                         headers=headers, json={"minutes": 0})
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["paused_until"] is None
    assert resumed.json()["resumed_queued_jobs"] == 1
    assert biblio_pause.biblio_paused_until(workspace) is None
    with db.session_scope() as session:
        resumed_job = session.get(BackgroundJob, jid)
        assert resumed_job.status == "queued"
        assert resumed_job.attempts == 0
        assert resumed_job.available_at <= datetime.now(timezone.utc)
        assert jobs.BIBLIO_PAUSE_MARKER not in resumed_job.payload
        untouched = session.get(BackgroundJob, delayed)
        assert untouched.available_at > datetime.now(timezone.utc) + timedelta(hours=1)
    next_job = jobs.claim_one()
    assert next_job["id"] == str(jid)
    assert worker._biblio_job_held_or_skipped(next_job) is False


def test_disabling_auto_sync_skips_already_queued_automatic_upload():
    client, csrf, workspace = setup_client("pause-auto-off@example.test")
    headers = {"X-CSRF-Token": csrf}
    on = client.patch(
        "/api/app/connectors/biblio/preferences", headers=headers,
        json={"auto_sync": True},
    )
    assert on.status_code == 200, on.text
    jid = jobs.enqueue_unique("biblio_sync", {}, workspace)
    off = client.patch(
        "/api/app/connectors/biblio/preferences", headers=headers,
        json={"auto_sync": False},
    )
    assert off.status_code == 200, off.text
    claimed = jobs.claim_one()
    assert claimed["id"] == str(jid)
    assert worker._biblio_job_held_or_skipped(claimed) is True
    with db.session_scope() as session:
        assert session.get(BackgroundJob, jid).status == "success"


def test_pause_inputs_require_auth_and_reject_invalid_durations():
    client, csrf, _ = setup_client("pause-bad-input@example.test")
    endpoint = "/api/app/connectors/biblio/upload-pause"
    assert client.put(endpoint, json={"minutes": 60}).status_code == 403
    for minutes in (1, -1, 90, 1440, "60"):
        response = client.put(
            endpoint, headers={"X-CSRF-Token": csrf}, json={"minutes": minutes},
        )
        assert response.status_code == 422, response.text
