"""Regression checks for SQLite contention observed in diagnostics logs."""
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import auth, db, entry
from app.product_models import AuthSession, ExtensionCredential


def test_file_sqlite_uses_wal_busy_timeout_and_hides_sql_parameters(tmp_path):
    database = tmp_path / "concurrent.sqlite3"
    engine = db._create_engine(f"sqlite:///{database}")
    try:
        assert engine.hide_parameters is True
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() >= 30_000
        # WAL is persistent and survives pool connection recycling.
        engine.dispose()
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
    finally:
        engine.dispose()


def test_authenticated_polling_does_not_write_session_activity_each_time():
    client = TestClient(entry.app)
    result = client.post(
        "/api/auth/register",
        json={
            "email": "polling@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Polling",
        },
    )
    assert result.status_code == 200, result.text

    with db.session_scope() as session:
        initial = session.execute(select(AuthSession)).scalar_one().last_seen_at

    for _ in range(4):
        response = client.get("/api/app/settings")
        assert response.status_code == 200, response.text

    with db.session_scope() as session:
        current = session.execute(select(AuthSession)).scalar_one()
        assert current.last_seen_at == initial
        current.last_seen_at = auth.utcnow() - timedelta(minutes=10)

    response = client.get("/api/app/settings")
    assert response.status_code == 200, response.text
    with db.session_scope() as session:
        current = session.execute(select(AuthSession)).scalar_one()
        assert current.last_seen_at > initial - timedelta(minutes=2)


def test_extension_polling_does_not_write_credential_activity_each_time():
    client = TestClient(entry.app)
    register = client.post(
        "/api/auth/register",
        json={
            "email": "extension-poll@example.test",
            "password": "a-long-test-password",
            "workspace_name": "Extension polling",
        },
    )
    assert register.status_code == 200, register.text
    pairing = client.post(
        "/api/app/extension/pairings",
        headers={"X-CSRF-Token": register.json()["csrf_token"]},
    )
    assert pairing.status_code == 200, pairing.text
    paired = TestClient(entry.app).post(
        "/api/extension/pair",
        json={"code": pairing.json()["code"]},
    )
    assert paired.status_code == 200, paired.text
    headers = {"Authorization": "Bearer " + paired.json()["token"]}

    with db.session_scope() as session:
        initial = session.execute(select(ExtensionCredential)).scalar_one().last_seen_at

    for _ in range(4):
        response = TestClient(entry.app).get("/api/extension/status", headers=headers)
        assert response.status_code == 200, response.text

    with db.session_scope() as session:
        current = session.execute(select(ExtensionCredential)).scalar_one()
        assert current.last_seen_at == initial
        current.last_seen_at = auth.utcnow() - timedelta(minutes=10)

    response = TestClient(entry.app).get("/api/extension/status", headers=headers)
    assert response.status_code == 200, response.text
    with db.session_scope() as session:
        current = session.execute(select(ExtensionCredential)).scalar_one()
        assert current.last_seen_at > initial - timedelta(minutes=2)
