"""BIBLIO photo preflight and narrowly scoped retry regressions."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.constants import Channel, ListingStatus
from app.product_models import BackgroundJob, MarketplaceOperation


def register(email):
    client = TestClient(entry.app)
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "a-long-test-password", "workspace_name": "Photo support"},
    )
    assert response.status_code == 200, response.text
    return client, response.json()["csrf_token"]


def make_book(workspace_id, *, book_id="VINTED-10253402699", count=4, source_count=5):
    with db.session_scope() as session:
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="PHOTO-TEST",
            title="Photo test book",
            category="book",
            quantity=1,
            currency="EUR",
        )
        session.add(item)
        session.flush()
        listing = models.ChannelListing(
            workspace_id=workspace_id,
            inventory_item_id=item.id,
            channel=Channel.BIBLIO,
            external_id=book_id,
            external_sku=book_id,
            title="Photo test book",
            price_cents=600,
            currency="EUR",
            status=ListingStatus.ACTIVE,
            quantity=1,
            extra={
                "image_urls": [f"https://images1.vinted.net/t/p{i}.jpg" for i in range(count)],
                "photo_count": count,
                "photo_sync_state": "ftp_uploaded",
            },
        )
        session.add(listing)
        session.add(models.ChannelListing(
            workspace_id=workspace_id,
            inventory_item_id=item.id,
            channel=Channel.VINTED,
            external_id="10253402699",
            title="Photo test book",
            price_cents=600,
            currency="EUR",
            status=ListingStatus.ACTIVE,
            quantity=1,
            extra={
                "image_urls": [f"https://images1.vinted.net/t/p{i}.jpg" for i in range(source_count)]
            },
        ))
        session.flush()
        return str(listing.id)


def test_photo_status_identifies_missing_staged_source_and_only_retries_one_book():
    client, csrf = register("photo-recovery@example.test")
    credentials = client.put(
        "/api/app/connectors/biblio/credentials",
        headers={"X-CSRF-Token": csrf},
        json={"values": {"username": "seller", "password": "secret"}},
    )
    assert credentials.status_code == 200, credentials.text
    with db.session_scope() as session:
        workspace = session.execute(select(models.Membership)).scalar_one().workspace_id
    listing_id = make_book(workspace)

    status = client.get(
        "/api/app/connectors/biblio/photo-status",
        params={"book_id": "VINTED-10253402699"},
    )
    assert status.status_code == 200, status.text
    info = status.json()
    assert info["active"] is True
    assert info["biblio_source_photos"] == 4
    assert info["vinted_source_photos"] == 5
    assert info["filenames"][-1] == "VINTED-10253402699_3.jpg"
    assert info["last_ftp_photo_count"] == 4

    payload = {"book_id": "VINTED-10253402699"}
    first = client.post(
        "/api/app/connectors/biblio/retry-listing-photos",
        headers={"X-CSRF-Token": csrf},
        json=payload,
    )
    assert first.status_code == 200, first.text
    second = client.post(
        "/api/app/connectors/biblio/retry-listing-photos",
        headers={"X-CSRF-Token": csrf},
        json=payload,
    )
    assert second.status_code == 200, second.text
    assert first.json()["job_id"] == second.json()["job_id"]

    with db.session_scope() as session:
        queued = session.execute(
            select(BackgroundJob).where(BackgroundJob.job_type == "biblio_sync")
        ).scalars().all()
        assert len(queued) == 1
        assert queued[0].workspace_id == workspace
        assert queued[0].payload == {
            "listing_id": listing_id,
            "photos_only": True,
            "force_photos": True,
            "operation_id": first.json()["operation_id"],
        }
        op = session.get(MarketplaceOperation, __import__("uuid").UUID(first.json()["operation_id"]))
        assert op is not None
        assert op.job_id == queued[0].id
        assert op.status == "queued"
        assert op.operation_type == "photos"


def test_photo_recovery_requires_auth_and_does_not_expose_other_workspaces():
    first, csrf = register("photo-owner@example.test")
    with db.session_scope() as session:
        workspace = session.execute(select(models.Membership)).scalar_one().workspace_id
    make_book(workspace)
    second, other_csrf = register("photo-other@example.test")

    assert second.get(
        "/api/app/connectors/biblio/photo-status",
        params={"book_id": "VINTED-10253402699"},
    ).status_code == 404
    assert second.post(
        "/api/app/connectors/biblio/retry-listing-photos",
        headers={"X-CSRF-Token": other_csrf},
        json={"book_id": "VINTED-10253402699"},
    ).status_code == 400  # No connector credentials for second workspace.
    assert first.post(
        "/api/app/connectors/biblio/retry-listing-photos",
        json={"book_id": "VINTED-10253402699"},
    ).status_code == 403  # Missing CSRF.
    assert TestClient(entry.app).get(
        "/api/app/connectors/biblio/photo-status",
        params={"book_id": "VINTED-10253402699"},
    ).status_code == 401


def test_empty_source_photos_cannot_queue_retry():
    client, csrf = register("empty-photos@example.test")
    response = client.put(
        "/api/app/connectors/biblio/credentials",
        headers={"X-CSRF-Token": csrf},
        json={"values": {"username": "seller", "password": "secret"}},
    )
    assert response.status_code == 200, response.text
    with db.session_scope() as session:
        workspace = session.execute(select(models.Membership)).scalar_one().workspace_id
    make_book(workspace, count=0, source_count=0)
    response = client.post(
        "/api/app/connectors/biblio/retry-listing-photos",
        headers={"X-CSRF-Token": csrf},
        json={"book_id": "VINTED-10253402699"},
    )
    assert response.status_code == 409, response.text


def test_failed_only_retry_requires_partial_ftp_evidence_and_scopes_job_to_book():
    from app.connectors.hosted import biblio_photo_file_signature
    import uuid

    client, csrf = register("selective-photo-recovery@example.test")
    response = client.put(
        "/api/app/connectors/biblio/credentials",
        headers={"X-CSRF-Token": csrf},
        json={"values": {"username": "seller", "password": "secret"}},
    )
    assert response.status_code == 200, response.text
    with db.session_scope() as session:
        ws = session.execute(select(models.Membership)).scalar_one().workspace_id
    listing_id = make_book(ws, book_id="BK-FAILED", count=3, source_count=3)
    with db.session_scope() as session:
        listing = session.get(models.ChannelListing, uuid.UUID(listing_id))
        attrs = dict(listing.extra or {})
        attrs["photo_sync_state"] = "error"
        attrs["photo_sync_error"] = "second image transfer failed"
        attrs["photo_file_receipts"] = {
            "BK-FAILED.jpg": biblio_photo_file_signature(
                "BK-FAILED", 0, "https://images1.vinted.net/t/p0.jpg"),
            "BK-FAILED_2.jpg": biblio_photo_file_signature(
                "BK-FAILED", 2, "https://images1.vinted.net/t/p2.jpg"),
        }
        listing.extra = attrs
    preflight = client.get(
        "/api/app/connectors/biblio/photo-status", params={"book_id": "BK-FAILED"},
    )
    assert preflight.status_code == 200, preflight.text
    info = preflight.json()
    assert info["successful_file_transfers"] == 2
    assert info["unconfirmed_file_transfers"] == 1
    assert [row["filename"] for row in info["file_progress"] if not row["sent_to_ftp"]] == ["BK-FAILED_1.jpg"]

    queued = client.post(
        "/api/app/connectors/biblio/retry-listing-photos",
        headers={"X-CSRF-Token": csrf},
        json={"book_id": "BK-FAILED", "failed_only": True},
    )
    assert queued.status_code == 200, queued.text
    with db.session_scope() as session:
        jobs = session.execute(select(BackgroundJob)).scalars().all()
        assert len(jobs) == 1
        assert jobs[0].payload["listing_id"] == listing_id
        assert jobs[0].payload["photos_only"] is True
        assert jobs[0].payload["failed_photos_only"] is True
        assert jobs[0].payload["force_photos"] is False


def test_selective_retry_fails_closed_without_partial_success_evidence():
    client, csrf = register("selective-photo-unsafe@example.test")
    response = client.put(
        "/api/app/connectors/biblio/credentials",
        headers={"X-CSRF-Token": csrf},
        json={"values": {"username": "seller", "password": "secret"}},
    )
    assert response.status_code == 200, response.text
    with db.session_scope() as session:
        ws = session.execute(select(models.Membership)).scalar_one().workspace_id
    make_book(ws, book_id="BK-UNVERIFIED", count=3, source_count=3)
    response = client.post(
        "/api/app/connectors/biblio/retry-listing-photos",
        headers={"X-CSRF-Token": csrf},
        json={"book_id": "BK-UNVERIFIED", "failed_only": True},
    )
    assert response.status_code == 409, response.text
    assert "No verifiable partial photo transfer" in response.json()["detail"]
