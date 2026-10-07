import io
import zipfile

from fastapi.testclient import TestClient

from app import diagnostics, entry, product_api


def _registered_client(email: str = "diagnostics@example.test"):
    client = TestClient(entry.app)
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "a-long-test-password",
            "workspace_name": "Diagnostics",
        },
    )
    assert response.status_code == 200, response.text
    return client, response.json()["csrf_token"]


def test_diagnostics_redacts_known_and_structured_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-super-secret-value")
    text = diagnostics.redact_text(
        "OPENAI_API_KEY=sk-test-super-secret-value "
        "Authorization: Bearer abc.def.ghi password=hunter2 token=visible-token"
    )
    assert "sk-test-super-secret-value" not in text
    assert "abc.def.ghi" not in text
    assert "hunter2" not in text
    assert "visible-token" not in text
    assert "[REDACTED]" in text


def test_live_diagnostics_are_hidden_in_production(monkeypatch):
    client, _csrf = _registered_client("diag-prod@example.test")
    monkeypatch.setattr(product_api, "is_production", lambda: True)

    status = client.get("/api/app/diagnostics/status")
    assert status.status_code == 200, status.text
    assert status.json()["dev_console"] is False

    logs = client.get("/api/app/diagnostics/logs")
    assert logs.status_code == 404
    assert "disabled in production" in logs.json()["detail"]


def test_diagnostics_bundle_is_authenticated_and_redacted(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostics, "DIAGNOSTICS_DIR", tmp_path)
    (tmp_path / "worker.log").write_text(
        "2026-10-07T22:00:00Z ERROR worker test token=server-secret\n",
        encoding="utf-8",
    )
    client, csrf = _registered_client("diag-download@example.test")

    anonymous = TestClient(entry.app)
    assert anonymous.post(
        "/api/app/diagnostics/download",
        json={"browser_logs": []},
    ).status_code == 401

    response = client.post(
        "/api/app/diagnostics/download",
        headers={"X-CSRF-Token": csrf},
        json={
            "browser_logs": [
                {
                    "at": "2026-10-07T22:00:00Z",
                    "level": "error",
                    "event": "api.failure",
                    "detail": "password=browser-secret",
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/zip")
    assert "reseller-dashboard-diagnostics-" in response.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert "README.txt" in names
        assert "snapshot.json" in names
        assert "browser.log" in names
        assert "server/worker.log" in names
        combined = "\n".join(
            archive.read(name).decode("utf-8", errors="replace")
            for name in names
        )
    assert "server-secret" not in combined
    assert "browser-secret" not in combined
    assert "[REDACTED]" in combined


def test_recent_logs_are_limited_and_redacted(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostics, "DIAGNOSTICS_DIR", tmp_path)
    (tmp_path / "web.log").write_text(
        "\n".join(
            f"2026-10-07T22:00:{index:02d}Z INFO web token=secret-{index}"
            for index in range(40)
        ),
        encoding="utf-8",
    )

    rows = diagnostics.recent_logs(limit=20)
    assert len(rows) == 20
    assert all(row["source"] == "web" for row in rows)
    assert all("secret-" not in row["line"] for row in rows)
