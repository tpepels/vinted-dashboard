"""Marketplaces must never blind-retry uncertain FTP/photos/remote writes."""
import pytest
from sqlalchemy import select

from app import db, models
from app.marketplace_operations import (
    begin_operation, fail_operation, queue_operation, retry_operation, serialize,
)
from app.product_models import BackgroundJob, MarketplaceOperation


def new_workspace():
    with db.session_scope() as session:
        row = models.Workspace(name="Safety checks", slug="marketplace-recovery", settings={})
        session.add(row)
        session.flush()
        return row.id


def failed_job(ws, channel, kind, target="all", payload=None, job_type=None):
    job_type = job_type or ("biblio_sync" if channel == "biblio" else f"{channel}_sync")
    with db.session_scope() as session:
        op, created = queue_operation(
            session, ws, channel, kind, target,
            job_type=job_type, payload=payload,
        )
        assert created
        operation_id, job_id = op.id, op.job_id
    assert begin_operation(operation_id, job_id)
    fail_operation(operation_id, "FTP transfer might have been accepted")
    return operation_id


def inspect(ws, op_id):
    with db.session_scope() as session:
        return serialize(session.get(MarketplaceOperation, op_id))


@pytest.mark.parametrize("channel,kind,target,payload,expected", [
    ("biblio", "sync", "all", {}, "biblio_compare"),
    ("biblio", "photos", "book-7", {"photos_only":True,"force_photos":True}, "biblio_photos"),
    ("biblio", "photos", "book-8", {"photos_only":True, "failed_photos_only":True}, "biblio_photos"),
    ("shopify", "photos", "variant-1", {}, "manual_review"),
    ("woocommerce", "update", "product-1", {}, "manual_review"),
])
def test_risky_upload_cannot_be_requeued_from_generic_history(
    channel, kind, target, payload, expected,
):
    ws = new_workspace()
    op_id = failed_job(ws, channel, kind, target, payload)
    history = inspect(ws, op_id)
    assert history["can_retry"] is False
    assert history["next_step"]["kind"] == expected
    with db.session_scope() as session:
        before = session.execute(select(BackgroundJob)).scalars().all()
        with pytest.raises(ValueError, match="Automatic retry is only available"):
            retry_operation(session, ws, op_id)
        after = session.execute(select(BackgroundJob)).scalars().all()
        assert len(after) == len(before) == 1


@pytest.mark.parametrize("channel", [
    "ebay", "etsy", "woocommerce", "shopify", "bigcommerce",
    "squarespace", "wix", "depop",
])
def test_read_only_import_can_be_retried_with_explicit_confirmation(channel):
    ws = new_workspace()
    op_id = failed_job(ws, channel, "sync")
    history = inspect(ws, op_id)
    assert history["can_retry"] is True
    assert history["next_step"]["kind"] == "retry_import"
    with db.session_scope() as session:
        retry = retry_operation(session, ws, op_id)
        assert retry.status == "queued"
        assert retry.job_type == f"{channel}_sync"
        assert retry.job_id is not None
    assert inspect(ws, op_id)["can_retry"] is False


def test_import_with_nonstandard_job_handler_or_payload_cannot_be_replayed():
    ws = new_workspace()
    op_id = failed_job(ws, "shopify", "sync", job_type="cross_channel_close")
    assert inspect(ws, op_id)["can_retry"] is False
    with db.session_scope() as session:
        with pytest.raises(ValueError, match="Automatic retry"):
            retry_operation(session, ws, op_id)

    op_id2 = failed_job(ws, "etsy", "sync", target="some-record",
                        payload={"force_photos": True})
    assert inspect(ws, op_id2)["can_retry"] is False


def test_uncertain_published_write_requires_remote_review_not_repeat():
    ws = new_workspace()
    op_id = failed_job(ws, "shopify", "publish", target="book-5")
    result = inspect(ws, op_id)
    assert result["status"] == "attention"
    assert result["can_retry"] is False
    assert result["next_step"]["kind"] == "manual_review"
    assert "not independently verified" in result["next_step"]["detail"]


def test_successful_unverified_transfer_still_has_explicit_review_step():
    ws = new_workspace()
    op_id = failed_job(ws, "biblio", "photos", target="book-11")
    with db.session_scope() as session:
        op = session.get(MarketplaceOperation, op_id)
        op.status = "needs_verification"
    result = inspect(ws, op_id)
    assert result["can_retry"] is False
    assert result["next_step"]["kind"] == "biblio_photos"
