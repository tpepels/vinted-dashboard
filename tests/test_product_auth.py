from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.product_models import BackgroundJob


def _register(client, email):
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "a-long-test-password", "workspace_name": email},
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _write_headers(csrf):
    return {"X-CSRF-Token": csrf}


def test_two_accounts_cannot_see_each_others_inventory():
    alice = TestClient(entry.app)
    bob = TestClient(entry.app)
    alice_csrf = _register(alice, "alice@example.test")
    bob_csrf = _register(bob, "bob@example.test")

    response = alice.post(
        "/api/app/inventory",
        headers=_write_headers(alice_csrf),
        json={"sku": "ALICE-1", "title": "Alice item", "category": "general", "quantity": 1},
    )
    assert response.status_code == 200, response.text

    assert [row["sku"] for row in alice.get("/api/app/inventory").json()["items"]] == ["ALICE-1"]
    assert bob.get("/api/app/inventory").json()["items"] == []

    response = bob.patch(
        "/api/app/inventory/" + alice.get("/api/app/inventory").json()["items"][0]["id"],
        headers=_write_headers(bob_csrf),
        json={"title": "stolen"},
    )
    assert response.status_code == 404


def test_extension_pairing_is_revocable_and_workspace_scoped():
    client = TestClient(entry.app)
    csrf = _register(client, "seller@example.test")

    pairing = client.post(
        "/api/app/extension/pairings", headers=_write_headers(csrf)
    )
    assert pairing.status_code == 200

    paired = TestClient(entry.app).post(
        "/api/extension/pair",
        json={"code": pairing.json()["code"], "extension_version": "2.0.0"},
    )
    assert paired.status_code == 200, paired.text
    token = paired.json()["token"]
    auth = {"Authorization": "Bearer " + token}

    snapshot = {
        "collected_at": 1_900_000_000,
        "current_user": {"id": "123", "username": "seller", "followers_count": 10},
        "listings": [
            {
                "id": "999",
                "title": "Paired listing",
                "status": "active",
                "price_cents": 1200,
                "currency": "EUR",
                "views": 4,
                "favourites": 2,
            }
        ],
        "notifications": [],
        "orders": [],
        "market_results": [],
        "extension_version": "2.0.0",
    }
    synced = TestClient(entry.app).post(
        "/api/extension/browser-sync", headers=auth, json=snapshot
    )
    assert synced.status_code == 200, synced.text

    inventory = client.get("/api/app/inventory").json()["items"]
    assert len(inventory) == 1
    assert inventory[0]["title"] == "Paired listing"
    assert inventory[0]["listings"][0]["channel"] == "vinted"

    devices = client.get("/api/app/extension/devices").json()["devices"]
    assert len(devices) == 1
    revoked = client.delete(
        "/api/app/extension/devices/" + devices[0]["id"],
        headers=_write_headers(csrf),
    )
    assert revoked.status_code == 200
    assert TestClient(entry.app).get("/api/extension/status", headers=auth).status_code == 401


def test_vinted_browser_sync_queues_one_deduplicated_biblio_auto_sync(monkeypatch):
    client = TestClient(entry.app)
    csrf = _register(client, "biblio-auto@example.test")
    pairing = client.post(
        "/api/app/extension/pairings", headers=_write_headers(csrf)
    ).json()
    paired = TestClient(entry.app).post(
        "/api/extension/pair",
        json={"code": pairing["code"], "extension_version": "3.4.1"},
    )
    token = paired.json()["token"]
    auth = {"Authorization": "Bearer " + token}

    with db.session_scope() as session:
        membership = session.execute(select(models.Membership)).scalar_one()
        workspace_id = membership.workspace_id
        item = models.InventoryItem(
            workspace_id=workspace_id,
            sku="AUTO-BIB-1",
            title="Auto BIBLIO",
            category="book",
            quantity=1,
            status="active",
            currency="EUR",
            attributes={"author": "Author", "description": "Description"},
        )
        session.add(item)
        session.flush()
        session.add(
            models.ChannelListing(
                workspace_id=workspace_id,
                inventory_item_id=item.id,
                channel="biblio",
                external_id="AUTO-BIB-1",
                external_sku="AUTO-BIB-1",
                title="Auto BIBLIO",
                price_cents=1000,
                currency="EUR",
                status="active",
                quantity=1,
                extra={"author": "Author", "description": "Description"},
            )
        )

    monkeypatch.setattr(
        "app.bridge_api.biblio_auto_sync_enabled",
        lambda workspace_id: True,
    )
    snapshot = {
        "collected_at": 1_900_000_200,
        "current_user": {"id": "123", "username": "seller"},
        "listings": [],
        "notifications": [],
        "orders": [],
        "market_results": [],
        "extension_version": "3.4.1",
    }
    first = TestClient(entry.app).post(
        "/api/extension/browser-sync", headers=auth, json=snapshot
    )
    assert first.status_code == 200, first.text
    assert first.json()["biblio_auto_sync_queued"] is True

    second_payload = dict(snapshot)
    second_payload["collected_at"] = 1_900_000_201
    second = TestClient(entry.app).post(
        "/api/extension/browser-sync", headers=auth, json=second_payload
    )
    assert second.status_code == 200, second.text
    assert second.json()["biblio_auto_sync_job_id"] == first.json()["biblio_auto_sync_job_id"]

    with db.session_scope() as session:
        jobs = session.execute(
            select(BackgroundJob).where(
                BackgroundJob.workspace_id == workspace_id,
                BackgroundJob.job_type == "biblio_sync",
                BackgroundJob.status == "queued",
            )
        ).scalars().all()
        assert len(jobs) == 1
        assert dict(jobs[0].payload or {}) == {}


def test_vinted_browser_sync_does_not_queue_biblio_when_auto_sync_disabled(monkeypatch):
    client = TestClient(entry.app)
    csrf = _register(client, "biblio-manual@example.test")
    pairing = client.post(
        "/api/app/extension/pairings", headers=_write_headers(csrf)
    ).json()
    paired = TestClient(entry.app).post(
        "/api/extension/pair",
        json={"code": pairing["code"]},
    )
    auth = {"Authorization": "Bearer " + paired.json()["token"]}

    monkeypatch.setattr(
        "app.bridge_api.biblio_auto_sync_enabled",
        lambda workspace_id: False,
    )
    snapshot = {
        "collected_at": 1_900_000_300,
        "current_user": {"id": "123"},
        "listings": [],
        "notifications": [],
        "orders": [],
        "market_results": [],
    }
    result = TestClient(entry.app).post(
        "/api/extension/browser-sync", headers=auth, json=snapshot
    )
    assert result.status_code == 200, result.text
    assert result.json()["biblio_auto_sync_queued"] is False

    with db.session_scope() as session:
        assert session.execute(
            select(BackgroundJob).where(BackgroundJob.job_type == "biblio_sync")
        ).scalars().all() == []


def test_same_vinted_notification_id_does_not_collide_between_workspaces():
    first = TestClient(entry.app)
    second = TestClient(entry.app)
    first_csrf = _register(first, "first@example.test")
    second_csrf = _register(second, "second@example.test")

    def bridge(client, csrf):
        code = client.post(
            "/api/app/extension/pairings", headers=_write_headers(csrf)
        ).json()["code"]
        result = TestClient(entry.app).post("/api/extension/pair", json={"code": code})
        return result.json()["token"]

    one = bridge(first, first_csrf)
    two = bridge(second, second_csrf)
    base = {
        "collected_at": 1_900_000_100,
        "current_user": {"id": "x"},
        "listings": [],
        "notifications": [
            {"id": "same-id", "category": "favorite", "item_id": "1", "item_title": "X"}
        ],
        "orders": [],
        "market_results": [],
    }
    TestClient(entry.app).post(
        "/api/extension/browser-sync", headers={"Authorization": "Bearer " + one}, json=base
    )
    second_payload = dict(base)
    second_payload["collected_at"] = 1_900_000_101
    TestClient(entry.app).post(
        "/api/extension/browser-sync", headers={"Authorization": "Bearer " + two}, json=second_payload
    )

    with db.session_scope() as session:
        rows = session.execute(select(models.FavoriteEvent)).scalars().all()
        assert len(rows) == 2
        assert len({row.workspace_id for row in rows}) == 2
