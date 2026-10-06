from __future__ import annotations

from datetime import date

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app import entry
from app.runtime_config import validate_configuration
from scripts.release_readiness import collect_checks


def release_env() -> dict[str, str]:
    return {
        "APP_ENV": "production",
        "PUBLIC_APP_URL": "https://reseller.example",
        "PRIVACY_CONTACT_EMAIL": "privacy@reseller.example",
        "COOKIE_SECURE": "true",
        "APP_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
        "DATABASE_URL": "postgresql://user:pass@db.example/reseller",
        "AUTH_SESSION_DAYS": "30",
        "PASSWORD_HASH_ITERATIONS": "310000",
        "BILLING_ENABLED": "false",
        "EXTENSION_LATEST_VERSION": "3.1.0",
        "BACKUP_PROVIDER": "managed-postgres-snapshots",
        "BACKUP_RESTORE_DRILL_AT": "2026-10-01",
    }


def test_release_readiness_passes_static_hosted_requirements():
    result = collect_checks(release_env(), today=date(2026, 10, 2))
    assert result["ok"] is True
    assert result["failures"] == []
    assert all(row["ok"] for row in result["checks"])


def test_release_readiness_rejects_extension_drift_and_stale_restore_drill():
    env = release_env()
    env["EXTENSION_LATEST_VERSION"] = "2.1.0"
    env["BACKUP_RESTORE_DRILL_AT"] = "2026-01-01"
    result = collect_checks(env, today=date(2026, 10, 2), max_restore_drill_age_days=90)
    assert result["ok"] is False
    assert any("EXTENSION_LATEST_VERSION" in row for row in result["failures"])
    assert any("restore drill is stale" in row for row in result["failures"])


def test_production_config_requires_public_privacy_contact():
    env = release_env()
    env.pop("PRIVACY_CONTACT_EMAIL")
    errors = validate_configuration(env)
    assert "PRIVACY_CONTACT_EMAIL must be a valid public contact email" in errors


def test_privacy_page_renders_configured_contact(monkeypatch):
    monkeypatch.setenv("PRIVACY_CONTACT_EMAIL", "privacy@example.test")
    response = TestClient(entry.app).get("/privacy")
    assert response.status_code == 200
    assert "privacy@example.test" in response.text
    assert "mailto:privacy@example.test" in response.text
    assert "__PRIVACY_CONTACT__" not in response.text
    assert "Chrome Web Store Limited Use" in response.text


def test_runtime_summary_does_not_expose_privacy_email(monkeypatch):
    monkeypatch.setenv("PRIVACY_CONTACT_EMAIL", "secret-contact@example.test")
    body = TestClient(entry.app).get("/api/runtime").json()
    assert body["privacy_contact_configured"] is True
    assert "secret-contact@example.test" not in str(body)
