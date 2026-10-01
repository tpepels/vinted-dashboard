from __future__ import annotations

import io
import json
import zipfile

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import billing, db, entry, models
from app.constants import BillingStatus
from app.runtime_config import (
    database_readiness,
    expected_database_revision,
    validate_configuration,
)


def production_env():
    return {
        "APP_ENV": "production",
        "PUBLIC_APP_URL": "https://reseller.example",
        "COOKIE_SECURE": "true",
        "APP_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
        "LEGACY_UI_ENABLED": "false",
        "LEGACY_API_ENABLED": "false",
        "LEGACY_COMPAT_SYNC": "false",
        "EXTENSION_MARKET_RESEARCH_ENABLED": "false",
        "PASSWORD_HASH_ITERATIONS": "310000",
        "AUTH_SESSION_DAYS": "30",
        "BILLING_ENABLED": "false",
    }


def register(client, email, monkeypatch):
    monkeypatch.setattr("app.product_api.rate_limiter.check", lambda *args, **kwargs: None)
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "a-long-test-password",
            "workspace_name": "Hosted beta",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def headers(csrf):
    return {"X-CSRF-Token": csrf}


def test_valid_production_configuration():
    assert validate_configuration(
        production_env(),
        database_url="postgresql+psycopg2://user:pass@db:5432/app",
    ) == []


@pytest.mark.parametrize(
    ("change", "needle"),
    [
        ({"PUBLIC_APP_URL": "http://reseller.example"}, "PUBLIC_APP_URL"),
        ({"COOKIE_SECURE": "false"}, "COOKIE_SECURE"),
        ({"APP_ENCRYPTION_KEY": ""}, "APP_ENCRYPTION_KEY"),
        ({"LEGACY_API_ENABLED": "true"}, "LEGACY_API_ENABLED"),
        ({"EXTENSION_MARKET_RESEARCH_ENABLED": "true"}, "EXTENSION_MARKET_RESEARCH_ENABLED"),
        ({"PASSWORD_HASH_ITERATIONS": "1000"}, "PASSWORD_HASH_ITERATIONS"),
        ({"AUTH_SESSION_DAYS": "365"}, "AUTH_SESSION_DAYS"),
    ],
)
def test_invalid_production_configuration(change, needle):
    env = production_env()
    env.update(change)
    errors = validate_configuration(
        env,
        database_url="postgresql+psycopg2://user:pass@db:5432/app",
    )
    assert any(needle in error for error in errors)


def test_production_rejects_sqlite_and_incomplete_billing():
    env = production_env()
    assert any(
        "PostgreSQL" in error
        for error in validate_configuration(env, database_url="sqlite:////tmp/app.sqlite3")
    )
    env["BILLING_ENABLED"] = "true"
    errors = validate_configuration(env, database_url="postgresql://user:pass@db/app")
    assert any("STRIPE_SECRET_KEY" in error for error in errors)
    assert any("STRIPE_PRICE_ID" in error for error in errors)
    assert any("STRIPE_WEBHOOK_SECRET" in error for error in errors)


def test_development_configuration_remains_permissive():
    assert validate_configuration(
        {"APP_ENV": "development"},
        database_url="sqlite://",
    ) == []


def test_production_readiness_checks_alembic_revision():
    revision = expected_database_revision()
    with db.session_scope() as session:
        session.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        session.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": revision},
        )
    result = database_readiness(check_migration=True)
    assert result["migration_revision"] == revision
    assert result["expected_revision"] == revision


def test_production_readiness_rejects_stale_revision():
    with db.session_scope() as session:
        session.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        session.execute(text("INSERT INTO alembic_version (version_num) VALUES ('stale')"))
    with pytest.raises(RuntimeError, match="not current"):
        database_readiness(check_migration=True)


def test_security_headers_and_runtime_summary(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLIC_APP_URL", "https://reseller.example")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    client = TestClient(entry.app)

    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "max-age=" in response.headers["strict-transport-security"]

    body = client.get("/api/runtime").json()
    assert body["production"] is True
    assert body["public_origin_configured"] is True
    encoded = json.dumps(body).lower()
    assert "app_encryption_key" not in encoded
    assert "password" not in encoded


def test_extension_download_uses_canonical_public_origin(monkeypatch):
    monkeypatch.setenv("PUBLIC_APP_URL", "https://reseller.example")
    response = TestClient(entry.app).get("/downloads/reseller-chrome-bridge.zip")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        background = archive.read("background.js").decode("utf-8")
    assert manifest["homepage_url"] == "https://reseller.example"
    assert "https://reseller.example/*" in manifest["host_permissions"]
    assert 'const API_ORIGIN="https://reseller.example"' in background


def test_billing_makes_workspace_read_only(monkeypatch):
    client = TestClient(entry.app)
    csrf = register(client, "readonly@example.test", monkeypatch)
    response = client.post(
        "/api/app/inventory",
        headers=headers(csrf),
        json={"sku": "A", "title": "Before lock", "quantity": 1},
    )
    assert response.status_code == 200

    with db.session_scope() as session:
        workspace = session.execute(select(models.Workspace)).scalars().one()
        workspace.billing_status = BillingStatus.PAST_DUE

    monkeypatch.setattr(billing, "BILLING_ENABLED", True)
    assert client.get("/api/auth/me").json()["billing"]["read_only"] is True
    assert client.get("/api/app/inventory").status_code == 200

    blocked = client.post(
        "/api/app/inventory",
        headers=headers(csrf),
        json={"sku": "B", "title": "Blocked", "quantity": 1},
    )
    assert blocked.status_code == 402

    checkout = client.post("/api/app/billing/checkout", headers=headers(csrf))
    assert checkout.status_code == 400


def test_billing_lock_blocks_existing_extension_sync(monkeypatch):
    client = TestClient(entry.app)
    csrf = register(client, "extension-lock@example.test", monkeypatch)
    pairing = client.post("/api/app/extension/pairings", headers=headers(csrf))
    completed = TestClient(entry.app).post(
        "/api/extension/pair",
        json={"code": pairing.json()["code"], "extension_version": "2.1.0"},
    )
    assert completed.status_code == 200
    token = completed.json()["token"]

    with db.session_scope() as session:
        workspace = session.execute(select(models.Workspace)).scalars().one()
        workspace.billing_status = BillingStatus.CANCELED

    monkeypatch.setattr(billing, "BILLING_ENABLED", True)
    response = TestClient(entry.app).post(
        "/api/extension/browser-sync",
        headers={"Authorization": "Bearer " + token},
        json={
            "collected_at": 1900000000,
            "current_user": {"id": "x"},
            "listings": [],
            "notifications": [],
            "orders": [],
            "market_results": [],
            "extension_version": "2.1.0",
        },
    )
    assert response.status_code == 402
