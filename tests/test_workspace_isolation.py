from __future__ import annotations

from datetime import datetime, timezone
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.crypto import encrypt_json
from app.product_models import ConnectorCredential, ExtensionCredential


NOW = datetime.now(timezone.utc)


def _register(client: TestClient, email: str) -> str:
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "a-long-test-password",
            "workspace_name": email,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _workspace_for(email: str) -> uuid.UUID:
    with db.session_scope() as session:
        user = session.execute(
            select(models.User).where(models.User.email == email)
        ).scalar_one()
        membership = session.execute(
            select(models.Membership).where(models.Membership.user_id == user.id)
        ).scalar_one()
        return membership.workspace_id


def test_user_cannot_select_another_users_workspace():
    alice = TestClient(entry.app)
    bob = TestClient(entry.app)
    _register(alice, "isolation-alice@example.test")
    _register(bob, "isolation-bob@example.test")
    alice_workspace = _workspace_for("isolation-alice@example.test")

    response = bob.get(
        "/api/app/inventory",
        headers={"X-Workspace-Id": str(alice_workspace)},
    )
    assert response.status_code == 403


def test_extension_device_revocation_is_workspace_scoped():
    alice = TestClient(entry.app)
    bob = TestClient(entry.app)
    alice_csrf = _register(alice, "device-alice@example.test")
    bob_csrf = _register(bob, "device-bob@example.test")

    pairing = alice.post(
        "/api/app/extension/pairings",
        headers={"X-CSRF-Token": alice_csrf},
    )
    paired = TestClient(entry.app).post(
        "/api/extension/pair",
        json={"code": pairing.json()["code"], "extension_version": "2.2.0"},
    )
    assert paired.status_code == 200
    device_id = alice.get("/api/app/extension/devices").json()["devices"][0]["id"]

    response = bob.delete(
        f"/api/app/extension/devices/{device_id}",
        headers={"X-CSRF-Token": bob_csrf},
    )
    assert response.status_code == 404

    with db.session_scope() as session:
        row = session.get(ExtensionCredential, uuid.UUID(device_id))
        assert row is not None
        assert row.revoked_at is None


def test_connector_credential_delete_cannot_touch_other_workspace():
    alice = TestClient(entry.app)
    bob = TestClient(entry.app)
    _register(alice, "connector-alice@example.test")
    bob_csrf = _register(bob, "connector-bob@example.test")
    alice_workspace = _workspace_for("connector-alice@example.test")

    with db.session_scope() as session:
        credential = ConnectorCredential(
            workspace_id=alice_workspace,
            channel="ebay",
            encrypted_payload=encrypt_json({"oauth_token": "alice-secret"}),
        )
        session.add(credential)
        session.flush()
        credential_id = credential.id

    response = bob.delete(
        "/api/app/connectors/ebay/credentials",
        headers={"X-CSRF-Token": bob_csrf},
    )
    assert response.status_code == 200

    with db.session_scope() as session:
        assert session.get(ConnectorCredential, credential_id) is not None


def test_export_and_sale_link_remain_workspace_scoped():
    alice = TestClient(entry.app)
    bob = TestClient(entry.app)
    alice_csrf = _register(alice, "export-alice@example.test")
    bob_csrf = _register(bob, "export-bob@example.test")

    created = alice.post(
        "/api/app/inventory",
        headers={"X-CSRF-Token": alice_csrf},
        json={"sku": "ALICE-PRIVATE", "title": "Alice private item", "quantity": 1},
    )
    assert created.status_code == 200

    bob_item = bob.post(
        "/api/app/inventory",
        headers={"X-CSRF-Token": bob_csrf},
        json={"sku": "BOB-1", "title": "Bob item", "quantity": 1},
    ).json()["item"]

    alice_workspace = _workspace_for("export-alice@example.test")
    with db.session_scope() as session:
        sale = models.Sale(
            workspace_id=alice_workspace,
            channel="vinted",
            external_order_id="ALICE-SALE",
            direction="sell",
            title="Alice private item",
            total_cents=1000,
            currency="EUR",
            status="completed",
            lifecycle_status="completed",
            is_closed=True,
            occurred_at=NOW,
            first_seen_at=NOW,
            last_seen_at=NOW,
            extra={},
        )
        session.add(sale)
        session.flush()
        sale_id = sale.id

    export = bob.get("/api/app/export?format=csv")
    assert export.status_code == 200
    assert "ALICE-PRIVATE" not in export.content.decode("utf-8-sig")

    link = bob.post(
        f"/api/app/sales/{sale_id}/link",
        headers={"X-CSRF-Token": bob_csrf},
        json={"inventory_item_id": bob_item["id"]},
    )
    assert link.status_code == 404
