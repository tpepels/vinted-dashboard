"""Phase 2: atomic marketplace operations, verification boundary and retries."""
from datetime import datetime, timedelta, timezone
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, jobs, models, worker
from app.marketplace_operations import (
    begin_operation, complete_operation, fail_operation, queue_operation,
    retry_operation, start_inline, serialize,
)
from app.product_models import BackgroundJob, MarketplaceOperation


def workspace():
    with db.session_scope() as session:
        row = models.Workspace(name="Operations", slug="operations", settings={})
        session.add(row)
        session.flush()
        return row.id


def queue(workspace_id, channel="ebay", operation_type="sync", target="all", **kwargs):
    with db.session_scope() as session:
        op, created = queue_operation(
            session, workspace_id, channel, operation_type, target,
            job_type=("biblio_sync" if channel == "biblio" else f"{channel}_sync"),
            **kwargs,
        )
        return op.id, op.job_id, created


def test_enqueue_atomic_deduplicated_by_workspace_channel_type_and_target():
    ws = workspace()
    op_id, job_id, created = queue(ws)
    repeated_id, repeated_job_id, repeated_created = queue(ws)
    assert created and not repeated_created
    assert op_id == repeated_id
    assert job_id == repeated_job_id
    with db.session_scope() as session:
        assert len(session.execute(select(MarketplaceOperation)).scalars().all()) == 1
        rows = session.execute(select(BackgroundJob)).scalars().all()
        assert len(rows) == 1
        assert rows[0].payload == {"operation_id": str(op_id)}
        assert rows[0].job_type == "ebay_sync"
    with pytest.raises(ValueError, match="still active"):
        queue(ws, payload={"full_sync": True})


def test_successful_import_is_not_claimed_as_verified_remote_publication():
    ws = workspace()
    op_id, job_id, _created = queue(ws)
    assert begin_operation(op_id, job_id)
    complete_operation(op_id, {"items": 17, "orders": 2, "password": "never store"})
    with db.session_scope() as session:
        op = session.get(MarketplaceOperation, op_id)
        assert op.status == "succeeded"
        assert op.verification == "snapshot_imported"
        assert op.result == {"items": 17, "orders": 2}
        assert op.active_key is None
        assert op.attempts == 1
    assert not begin_operation(op_id, job_id)
    next_id, _, created = queue(ws)
    assert created and next_id != op_id


def test_biblio_upload_transport_needs_manual_remote_verification():
    ws = workspace()
    op_id, job_id, _ = queue(
        ws, channel="biblio", operation_type="sync",
        payload={"full_sync": True},
    )
    assert begin_operation(op_id, job_id)
    complete_operation(op_id, {"inventory_uploaded": 5, "photos_uploaded": 4})
    with db.session_scope() as session:
        result = serialize(session.get(MarketplaceOperation, op_id))
        assert result["status"] == "needs_verification"
        assert result["verification"] == "manual_required"
        assert result["result"]["photos_uploaded"] == 4
        assert not result["can_retry"]


def test_retries_are_bounded_to_safe_operations_and_do_not_clone_history():
    ws = workspace()
    op_id, job_id, _ = queue(ws)
    assert begin_operation(op_id, job_id)
    fail_operation(op_id, "temporary network error")
    with db.session_scope() as session:
        op = retry_operation(session, ws, op_id)
        assert op.status == "queued"
        assert op.job_id != job_id
    with db.session_scope() as session:
        rows = session.execute(select(MarketplaceOperation)).scalars().all()
        assert len(rows) == 1
        assert len(session.execute(select(BackgroundJob)).scalars().all()) == 2
    op2, job2, _ = queue(ws, channel="biblio", operation_type="close", target="listing-12")
    assert begin_operation(op2, job2)
    fail_operation(op2, "socket closed after upload")
    with db.session_scope() as session:
        op = session.get(MarketplaceOperation, op2)
        assert op.status == "attention"
        assert not serialize(op)["can_retry"]
        with pytest.raises(ValueError, match="cannot be retried"):
            retry_operation(session, ws, op2)


def test_delayed_biblio_photo_job_is_promoted_by_manual_request():
    ws = workspace()
    original, job_id, _ = queue(
        ws, channel="biblio", operation_type="photos", target="all",
        payload={"photos_only": True, "force_photos": False, "automatic_photo_retry": True},
        delay_seconds=93600,
    )
    op_id, new_job, created = queue(
        ws, channel="biblio", operation_type="photos", target="all",
        payload={"photos_only": True, "force_photos": True},
    )
    assert op_id == original and new_job == job_id and not created
    with db.session_scope() as session:
        job = session.get(BackgroundJob, job_id)
        assert job.payload["force_photos"] is True
        assert "automatic_photo_retry" not in job.payload
        assert job.available_at <= datetime.now(timezone.utc) + timedelta(seconds=3)
        assert len(session.execute(select(BackgroundJob)).scalars().all()) == 1


def test_inline_publish_blocks_ambiguous_repeat_after_network_failure():
    ws = workspace()
    op_id = start_inline(ws, "shopify", "publish", "sku-1")
    with pytest.raises(ValueError, match="previous publish"):
        start_inline(ws, "shopify", "publish", "sku-1")
    fail_operation(op_id, "timeout after remote request")
    with pytest.raises(ValueError, match="previous publish"):
        start_inline(ws, "shopify", "publish", "sku-1")
    with db.session_scope() as session:
        op = session.get(MarketplaceOperation, op_id)
        assert op.status == "attention"


def test_worker_persists_marketplace_operation_on_real_dispatch(monkeypatch):
    ws = workspace()
    op_id, job_id, _ = queue(ws)
    monkeypatch.setattr("app.connectors.hosted.sync_ebay_workspace",
                        lambda _workspace_id: {"items": 2, "active": 2})
    job = jobs.claim_one()
    assert job is not None and job["id"] == str(job_id)
    assert begin_operation(op_id, job_id)
    result = worker.handle(job)
    complete_operation(op_id, result)
    jobs.complete(job["id"])
    with db.session_scope() as session:
        assert session.get(MarketplaceOperation, op_id).result["items"] == 2
        assert session.get(BackgroundJob, job_id).status == "success"


def test_scoped_history_and_explicit_retry_api(monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    clients = []
    for email in ("marketphase-two-a@example.test", "marketphase-two-b@example.test"):
        client = TestClient(entry.app)
        registered = client.post("/api/auth/register", json={
            "email": email, "password": "a-long-test-password",
            "workspace_name": email,
        })
        assert registered.status_code == 200, registered.text
        clients.append((client, registered.json()["csrf_token"]))
    with db.session_scope() as session:
        owner = session.execute(select(models.User).where(
            models.User.email == "marketphase-two-a@example.test"
        )).scalar_one()
        workspace_id = session.execute(select(models.Membership).where(
            models.Membership.user_id == owner.id
        )).scalar_one().workspace_id
    op_id, job_id, _ = queue(workspace_id)
    begin_operation(op_id, job_id)
    fail_operation(op_id, "transient failure")
    first, token = clients[0]
    second, second_token = clients[1]
    assert first.get("/api/app/marketplace-operations").json()["operations"][0]["id"] == str(op_id)
    assert second.get("/api/app/marketplace-operations").json()["operations"] == []
    assert second.get(f"/api/app/marketplace-operations/{op_id}").status_code == 404
    forbidden = second.post(
        f"/api/app/marketplace-operations/{op_id}/retry",
        headers={"X-CSRF-Token": second_token},
    )
    assert forbidden.status_code == 404
    assert first.post(f"/api/app/marketplace-operations/{op_id}/retry").status_code in (403, 419)
    response = first.post(
        f"/api/app/marketplace-operations/{op_id}/retry",
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200, response.text
    assert response.json()["operation"]["status"] == "queued"
