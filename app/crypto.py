"""Small encryption helper for connector credentials.

Production should provide APP_ENCRYPTION_KEY as a Fernet key.  For local
self-hosted development we derive a stable key from APP_SECRET_KEY so the
application still starts with a single .env file.  The derived-key mode is
explicitly reported as development-only in settings/diagnostics.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


def _fernet_key() -> bytes:
    configured = os.getenv("APP_ENCRYPTION_KEY", "").strip()
    if configured:
        return configured.encode("ascii")
    secret = os.getenv("APP_SECRET_KEY", "development-only-change-me").encode("utf-8")
    return base64.urlsafe_b64encode(hashlib.sha256(secret).digest())


def using_derived_key() -> bool:
    return not bool(os.getenv("APP_ENCRYPTION_KEY", "").strip())


def encrypt_json(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return Fernet(_fernet_key()).encrypt(payload).decode("ascii")


def decrypt_json(value: str) -> dict[str, Any]:
    try:
        decoded = Fernet(_fernet_key()).decrypt(value.encode("ascii"))
        result = json.loads(decoded.decode("utf-8"))
    except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Stored connector credentials cannot be decrypted") from exc
    if not isinstance(result, dict):
        raise ValueError("Stored connector credentials are invalid")
    return result
