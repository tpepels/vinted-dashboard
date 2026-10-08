import uuid

import pytest

from app import worker


def test_worker_dispatch_table_has_one_handler_per_supported_job():
    assert set(worker._JOB_HANDLERS) == {
        "biblio_sync",
        "ebay_sync",
        "etsy_sync",
        "woocommerce_sync",
        "shopify_sync",
        "bigcommerce_sync",
        "squarespace_sync",
        "wix_sync",
        "depop_sync",
        "cross_channel_close",
        "store_stock_audit",
        "noop",
    }


def test_worker_noop_requires_no_workspace():
    worker.handle({"job_type": "noop", "payload": {"probe": True}})


def test_workspace_connector_jobs_require_workspace():
    with pytest.raises(RuntimeError, match="biblio_sync requires a workspace"):
        worker.handle({"job_type": "biblio_sync", "payload": {}})


def test_worker_dispatches_through_registered_handler(monkeypatch):
    called = {}

    def fake(payload, workspace_id):
        called["payload"] = payload
        called["workspace_id"] = workspace_id

    workspace_id = uuid.uuid4()
    monkeypatch.setitem(worker._JOB_HANDLERS, "test_job", fake)
    worker.handle(
        {
            "job_type": "test_job",
            "workspace_id": str(workspace_id),
            "payload": {"value": 42},
        }
    )

    assert called == {"payload": {"value": 42}, "workspace_id": workspace_id}


def test_unknown_worker_job_fails_clearly():
    with pytest.raises(RuntimeError, match="Unknown background job type"):
        worker.handle({"job_type": "does_not_exist"})


def test_worker_biblio_handler_preserves_target_and_photo_modes(monkeypatch):
    called = {}
    workspace_id = uuid.uuid4()
    listing_id = uuid.uuid4()

    def fake_sync(workspace, **kwargs):
        called["workspace_id"] = workspace
        called.update(kwargs)
        return {}

    monkeypatch.setattr("app.connectors.hosted.sync_biblio_workspace", fake_sync)
    worker._sync_biblio(
        {
            "listing_id": str(listing_id),
            "full_sync": False,
            "force_photos": True,
            "photos_only": True,
        },
        workspace_id,
    )

    assert called == {
        "workspace_id": workspace_id,
        "listing_id": listing_id,
        "full_sync": False,
        "force_photos": True,
        "photos_only": True,
        "failed_photos_only": False,
    }



def test_worker_schedules_one_delayed_photo_retry_for_new_listing(monkeypatch):
    workspace_id = uuid.uuid4()
    listing_id = uuid.uuid4()
    queued = []

    monkeypatch.setenv("BIBLIO_PHOTO_RETRY_DELAY_SECONDS", "123")
    monkeypatch.setattr(
        "app.connectors.hosted.sync_biblio_workspace",
        lambda workspace, **kwargs: {
            "deferred_photo_retry_listing_ids": [str(listing_id)]
        },
    )
    def capture_operation(session, workspace, channel, operation_type, target, **kwargs):
        queued.append((workspace, channel, operation_type, target, kwargs))
        return object(), True

    monkeypatch.setattr("app.marketplace_operations.queue_operation", capture_operation)

    worker._sync_biblio({"listing_id": str(listing_id)}, workspace_id)

    assert queued == [
        (
            workspace_id, "biblio", "photos", str(listing_id),
            {
                "job_type": "biblio_sync",
                "payload": {
                    "listing_id": str(listing_id),
                    "photos_only": True,
                    "force_photos": False,
                    "automatic_photo_retry": True,
                },
                "channel_listing_id": listing_id,
                "delay_seconds": 123,
            },
        )
    ]
