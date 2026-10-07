import uuid

import pytest

from app.connectors import hosted


def test_biblio_env_credentials_are_bootstrap_only(monkeypatch):
    workspace_id = uuid.uuid4()
    monkeypatch.setattr(hosted, "has_credentials", lambda *_args: False)
    monkeypatch.setattr(hosted, "_is_bootstrap_workspace", lambda _workspace_id: True)
    monkeypatch.setenv("BIBLIO_FTP_USERNAME", "seller")
    monkeypatch.setenv("BIBLIO_FTP_PASSWORD", "secret")
    monkeypatch.setenv("BIBLIO_FTP_HOST", "ftp.example.test")

    values = hosted._workspace_or_env_biblio_values(workspace_id)
    assert values["username"] == "seller"
    assert values["password"] == "secret"
    assert values["host"] == "ftp.biblio.com"

    monkeypatch.setattr(hosted, "_is_bootstrap_workspace", lambda _workspace_id: False)
    with pytest.raises(RuntimeError, match="this workspace"):
        hosted._workspace_or_env_biblio_values(workspace_id)


def test_ebay_env_credentials_are_bootstrap_only(monkeypatch):
    workspace_id = uuid.uuid4()
    monkeypatch.setattr(hosted, "has_credentials", lambda *_args: False)
    monkeypatch.setattr(hosted, "_is_bootstrap_workspace", lambda _workspace_id: True)
    monkeypatch.setenv("EBAY_OAUTH_TOKEN", "token")

    values = hosted._workspace_or_env_ebay_values(workspace_id)
    assert values["oauth_token"] == "token"

    monkeypatch.setattr(hosted, "_is_bootstrap_workspace", lambda _workspace_id: False)
    with pytest.raises(RuntimeError, match="this workspace"):
        hosted._workspace_or_env_ebay_values(workspace_id)


def test_workspace_connector_credentials_always_win_over_env(monkeypatch):
    workspace_id = uuid.uuid4()
    monkeypatch.setattr(hosted, "has_credentials", lambda *_args: True)
    monkeypatch.setattr(
        hosted,
        "_credentials",
        lambda *_args: {"username": "workspace", "password": "workspace-secret"},
    )
    monkeypatch.setenv("BIBLIO_FTP_USERNAME", "env-user")
    monkeypatch.setenv("BIBLIO_FTP_PASSWORD", "env-secret")

    values = hosted._workspace_or_env_biblio_values(workspace_id)
    assert values["username"] == "workspace"
    assert values["password"] == "workspace-secret"



def test_biblio_connection_requires_explicit_tls_and_private_data_channel(monkeypatch):
    calls = []

    class FakeTLS:
        def connect(self, host, timeout=20):
            calls.append(("connect", host, timeout))

        def auth(self):
            calls.append(("auth",))

        def login(self, username, password):
            calls.append(("login", username, password))

        def prot_p(self):
            calls.append(("prot_p",))

        def set_pasv(self, enabled):
            calls.append(("pasv", enabled))

        def cwd(self, directory):
            calls.append(("cwd", directory))

    monkeypatch.setattr(hosted.ftplib, "FTP_TLS", FakeTLS)
    ftp = hosted._connect_biblio_ftp({
        "host": "ftp.biblio.com",
        "username": "seller",
        "password": "secret",
        "directory": "",
        "timeout_seconds": "20",
    })

    assert isinstance(ftp, FakeTLS)
    assert calls == [
        ("connect", "ftp.biblio.com", 20),
        ("auth",),
        ("login", "seller", "secret"),
        ("prot_p",),
        ("pasv", True),
    ]
