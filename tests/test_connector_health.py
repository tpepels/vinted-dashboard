"""Connection checks must read actual APIs without confusing saved keys with access."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, entry, models
from app.constants import Channel
from app.product_models import ConnectorCredential


def registration(email):
    client = TestClient(entry.app)
    response = client.post("/api/auth/register", json={
        "email": email, "password": "a-long-test-password",
        "workspace_name": "Connections health",
    })
    assert response.status_code == 200, response.text
    return client, {"X-CSRF-Token": response.json()["csrf_token"]}


def shopify_credentials():
    return {
        "store_domain": "bookstore.myshopify.com",
        "access_token": "shpat_fake_secret_for_test",
        "api_version": "2026-10",
    }


def connector(client, channel="shopify"):
    response = client.get("/api/app/connectors")
    assert response.status_code == 200, response.text
    return next(row for row in response.json()["connectors"] if row["channel"] == channel)


def test_save_is_not_remote_verification_check_is_read_only_and_reset_on_edit(monkeypatch):
    client, headers = registration("connection-health@example.test")
    response = client.put("/api/app/connectors/shopify/credentials", headers=headers,
                          json={"values": shopify_credentials()})
    assert response.status_code == 200, response.text
    before = connector(client)
    assert before["configured"] and before["operational"]
    assert before["connection_check"]["status"] == "not_checked"
    assert before["connection_check"]["checked_at"] is None

    seen = []
    def fake_test(workspace_id):
        seen.append(workspace_id)
        return {"ok": True, "detail": "Products and orders readable"}
    monkeypatch.setattr("app.product_api.test_shopify_workspace", fake_test)
    checked = client.post("/api/app/connectors/shopify/test-connection", headers=headers)
    assert checked.status_code == 200, checked.text
    assert checked.json()["status"] == "passed"
    assert checked.json()["scope"] == "read_only"
    assert "stock edits and remote listing state were not tested" in checked.json()["detail"]
    assert len(seen) == 1
    after = connector(client)
    assert after["connection_check"]["status"] == "passed"
    assert after["connection_check"]["checked_at"]
    assert after["last_synced_at"] is None
    assert after["last_run"] is None

    # Changing credentials must clear any now-obsolete connection evidence.
    updated = client.put("/api/app/connectors/shopify/credentials", headers=headers,
                         json={"values": {"order_days": "30"}})
    assert updated.status_code == 200, updated.text
    assert connector(client)["connection_check"]["status"] == "not_checked"


def test_failed_access_is_visible_without_disclosing_remote_secrets(monkeypatch):
    client, headers = registration("connection-failed@example.test")
    assert client.put("/api/app/connectors/shopify/credentials", headers=headers,
                      json={"values": shopify_credentials()}).status_code == 200
    def fake_failure(_wid):
        raise RuntimeError("shpat_fake_secret_for_test included in response URL")
    monkeypatch.setattr("app.product_api.test_shopify_workspace", fake_failure)
    result = client.post("/api/app/connectors/shopify/test-connection", headers=headers)
    assert result.status_code == 502
    assert "shpat_" not in result.text
    status = connector(client)["connection_check"]
    assert status["status"] == "failed"
    assert status["checked_at"]
    assert "shpat_" not in str(status)


def test_unconfigured_and_unsupported_connections_never_dispatch(monkeypatch):
    client, headers = registration("connection-unsupported@example.test")
    calls = []
    monkeypatch.setattr("app.product_api.test_shopify_workspace", lambda *args: calls.append(args))
    assert client.post("/api/app/connectors/shopify/test-connection",
                       headers=headers).status_code == 409
    assert client.post("/api/app/connectors/ebay/test-connection",
                       headers=headers).status_code == 409
    assert client.post("/api/app/connectors/notreal/test-connection",
                       headers=headers).status_code == 400
    assert not calls


def test_credentials_deletion_clears_read_access_proof(monkeypatch):
    client, headers = registration("connection-deleted@example.test")
    assert client.put("/api/app/connectors/shopify/credentials", headers=headers,
                      json={"values": shopify_credentials()}).status_code == 200
    monkeypatch.setattr("app.product_api.test_shopify_workspace", lambda _wid: {"ok": True})
    assert client.post("/api/app/connectors/shopify/test-connection", headers=headers).status_code == 200
    assert client.delete("/api/app/connectors/shopify/credentials", headers=headers).status_code == 200
    result = connector(client)
    assert result["configured"] is False
    assert result["connection_check"]["status"] == "not_checked"
    assert client.post("/api/app/connectors/shopify/test-connection",
                       headers=headers).status_code == 409
    with db.session_scope() as session:
        assert not session.execute(
            select(ConnectorCredential).where(ConnectorCredential.channel == Channel.SHOPIFY)
        ).scalars().all()


def test_workspace_access_isolation_for_check_evidence(monkeypatch):
    first, headers = registration("connection-first@example.test")
    other, other_headers = registration("connection-second@example.test")
    assert first.put("/api/app/connectors/shopify/credentials", headers=headers,
                     json={"values": shopify_credentials()}).status_code == 200
    monkeypatch.setattr("app.product_api.test_shopify_workspace", lambda wid: {"ok": True})
    assert first.post("/api/app/connectors/shopify/test-connection", headers=headers).status_code == 200
    assert connector(first)["connection_check"]["status"] == "passed"
    assert connector(other)["connection_check"]["status"] == "not_checked"
    assert other.post("/api/app/connectors/shopify/test-connection",
                      headers=other_headers).status_code == 409


def test_biblio_ftp_check_proves_login_only_and_writes_no_files(monkeypatch):
    client, headers = registration("connection-biblio@example.test")
    saved = client.put("/api/app/connectors/biblio/credentials", headers=headers,
                       json={"values": {"username": "ftp-test", "password": "test-secret"}})
    assert saved.status_code == 200, saved.text
    calls = []
    def test_ftp(workspace_id):
        calls.append(workspace_id)
        return {"ok": True, "detail": "FTP login accepted", "transport": "ftps"}
    monkeypatch.setattr("app.product_api.test_biblio_workspace", test_ftp)
    checked = client.post("/api/app/connectors/biblio/test-connection", headers=headers)
    assert checked.status_code == 200, checked.text
    assert checked.json()["scope"] == "ftp_login"
    assert "No file was uploaded" in checked.json()["detail"]
    assert len(calls) == 1
    status = connector(client, "biblio")["connection_check"]
    assert status["status"] == "passed"
    assert status["scope"] == "ftp_login"


def test_ebay_lightweight_active_listing_read_only(monkeypatch):
    from types import SimpleNamespace
    from app.connectors import hosted

    monkeypatch.setattr(hosted, "_workspace_or_env_ebay_values", lambda wid: {
        "site_id": "0", "compatibility_level": "1477", "oauth_token": "secret",
    })
    monkeypatch.setattr(hosted, "_ebay_access_token", lambda values: "secret")
    calls = []
    def request(url, *, headers, data, timeout):
        calls.append((url, headers, data, timeout))
        return SimpleNamespace(status_code=200, content=(
            b'<GetMyeBaySellingResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
            b'<Ack>Success</Ack></GetMyeBaySellingResponse>'
        ))
    monkeypatch.setattr(hosted.requests, "post", request)
    result = hosted.test_ebay_workspace(None)
    assert result["ok"]
    assert len(calls) == 1
    assert calls[0][1]["X-EBAY-API-CALL-NAME"] == "GetMyeBaySelling"
    assert b"<EntriesPerPage>1</EntriesPerPage>" in calls[0][2]
    assert b"EndItem" not in calls[0][2]
    assert b"ReviseItem" not in calls[0][2]

    def denied(*args, **kwargs):
        return SimpleNamespace(status_code=200, content=(
            b'<GetMyeBaySellingResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
            b'<Ack>Failure</Ack><Errors><LongMessage>token-private-secret</LongMessage></Errors>'
            b'</GetMyeBaySellingResponse>'
        ))
    monkeypatch.setattr(hosted.requests, "post", denied)
    import pytest
    with pytest.raises(RuntimeError, match="did not confirm"):
        hosted.test_ebay_workspace(None)


def test_ebay_workspace_endpoint_checks_listings_not_orders(monkeypatch):
    client, headers = registration("connection-ebay@example.test")
    saved = client.put("/api/app/connectors/ebay/credentials", headers=headers,
                       json={"values": {"oauth_token": "eBay-test-access-token"}})
    assert saved.status_code == 200, saved.text
    monkeypatch.setattr("app.product_api.test_ebay_workspace", lambda wid: {"ok": True})
    response = client.post("/api/app/connectors/ebay/test-connection", headers=headers)
    assert response.status_code == 200, response.text
    assert "Order imports and listing edits were not tested" in response.json()["detail"]
    assert connector(client, "ebay")["connection_check"]["status"] == "passed"
