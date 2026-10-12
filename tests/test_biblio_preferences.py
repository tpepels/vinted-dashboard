"""BIBLIO switches persist on change and reflect effective workspace settings."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.connectors import hosted
from app.crypto import decrypt_json
from app.product_models import ConnectorCredential


def register(email):
    client = TestClient(entry.app)
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "a-long-test-password",
            "workspace_name": "BIBLIO preferences",
        },
    )
    assert response.status_code == 200, response.text
    with db.session_scope() as session:
        workspace_id = session.execute(
            select(models.Membership.workspace_id)
        ).scalar_one()
    return client, response.json()["csrf_token"], workspace_id


def biblio_connector(client):
    response = client.get("/api/app/connectors")
    assert response.status_code == 200, response.text
    return next(row for row in response.json()["connectors"] if row["channel"] == "biblio")


def test_biblio_switches_persist_individually_without_erasing_credentials_or_health():
    client, csrf, workspace_id = register("biblio-switch@example.test")
    headers = {"X-CSRF-Token": csrf}
    saved = client.put(
        "/api/app/connectors/biblio/credentials",
        headers=headers,
        json={"values": {
            "username": "seller", "password": "secret",
            "auto_sync": "true", "allow_plain_ftp": "true",
        }},
    )
    assert saved.status_code == 200, saved.text
    with db.session_scope() as session:
        account = session.execute(select(models.ChannelAccount).where(
            models.ChannelAccount.workspace_id == workspace_id,
            models.ChannelAccount.channel == "biblio",
        )).scalar_one()
        account.config = {**dict(account.config or {}), "connection_check_status": "success"}

    off = client.patch(
        "/api/app/connectors/biblio/preferences",
        headers=headers,
        json={"auto_sync": False},
    )
    assert off.status_code == 200, off.text
    assert off.json()["auto_sync"] is False
    assert off.json()["allow_plain_ftp"] is True
    assert hosted.biblio_auto_sync_enabled(workspace_id) is False

    off = client.patch(
        "/api/app/connectors/biblio/preferences",
        headers=headers,
        json={"allow_plain_ftp": False},
    )
    assert off.status_code == 200, off.text
    assert off.json()["auto_sync"] is False
    assert off.json()["allow_plain_ftp"] is False

    prefs = biblio_connector(client)["saved_values"]
    assert prefs["auto_sync"] == "false"
    assert prefs["allow_plain_ftp"] == "false"
    with db.session_scope() as session:
        row = session.execute(select(ConnectorCredential).where(
            ConnectorCredential.workspace_id == workspace_id,
            ConnectorCredential.channel == "biblio",
        )).scalar_one()
        values = decrypt_json(row.encrypted_payload)
        assert values["username"] == "seller"
        assert values["password"] == "secret"
        assert values["auto_sync"] == "false"
        assert values["allow_plain_ftp"] == "false"
        account = session.execute(select(models.ChannelAccount).where(
            models.ChannelAccount.workspace_id == workspace_id,
            models.ChannelAccount.channel == "biblio",
        )).scalar_one()
        assert account.config["connection_check_status"] == "success"


def test_env_configured_switch_can_be_disabled_and_survives_env_default(monkeypatch):
    client, csrf, workspace_id = register("biblio-env-switch@example.test")
    with db.session_scope() as session:
        workspace = session.get(models.Workspace, workspace_id)
        slug = workspace.slug
    monkeypatch.setenv("BOOTSTRAP_WORKSPACE_SLUG", slug)
    monkeypatch.setenv("BIBLIO_FTP_USERNAME", "env-seller")
    monkeypatch.setenv("BIBLIO_FTP_PASSWORD", "env-secret")
    monkeypatch.setenv("BIBLIO_FTP_AUTO_SYNC", "true")
    monkeypatch.setattr(
        hosted, "_is_bootstrap_workspace",
        lambda wid: wid == workspace_id,
    )
    assert biblio_connector(client)["saved_values"]["auto_sync"] == "true"
    response = client.patch(
        "/api/app/connectors/biblio/preferences",
        headers={"X-CSRF-Token": csrf},
        json={"auto_sync": False},
    )
    assert response.status_code == 200, response.text
    assert hosted.biblio_auto_sync_enabled(workspace_id) is False
    assert biblio_connector(client)["saved_values"]["auto_sync"] == "false"
    with db.session_scope() as session:
        credential = session.execute(select(ConnectorCredential).where(
            ConnectorCredential.workspace_id == workspace_id,
            ConnectorCredential.channel == "biblio",
        )).scalar_one()
        assert decrypt_json(credential.encrypted_payload)["username"] == "env-seller"


def test_biblio_switch_rejects_missing_credential_and_empty_patch():
    client, csrf, _workspace_id = register("biblio-unconfigured-switch@example.test")
    headers = {"X-CSRF-Token": csrf}
    missing = client.patch(
        "/api/app/connectors/biblio/preferences",
        headers=headers,
        json={"auto_sync": False},
    )
    assert missing.status_code == 400
    empty = client.patch(
        "/api/app/connectors/biblio/preferences",
        headers=headers,
        json={},
    )
    assert empty.status_code == 400
