#!/usr/bin/env python3
"""Non-destructive smoke check for a deployed hosted-beta instance."""
from __future__ import annotations

import argparse
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request


REQUIRED_SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "same-origin",
}


def canonical_origin(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urllib.parse.urlparse(raw)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Hosted smoke URL must be a bare HTTPS origin")
    return raw


def fetch(origin: str, path: str) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(
        origin + path,
        method="GET",
        headers={"User-Agent": "reseller-dashboard-hosted-smoke/1"},
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=20,
            context=ssl.create_default_context(),
        ) as response:
            return (
                int(response.status),
                {key.lower(): value for key, value in response.headers.items()},
                response.read(),
            )
    except urllib.error.HTTPError as exc:
        return (
            int(exc.code),
            {key.lower(): value for key, value in exc.headers.items()},
            exc.read(),
        )


def require_status(origin: str, path: str) -> tuple[dict[str, str], bytes]:
    status, headers, body = fetch(origin, path)
    if status != 200:
        raise RuntimeError(f"{path} returned HTTP {status}")
    return headers, body


def validate_security_headers(headers: dict[str, str]) -> None:
    for name, expected in REQUIRED_SECURITY_HEADERS.items():
        actual = headers.get(name)
        if actual != expected:
            raise RuntimeError(f"Missing/invalid {name}: {actual!r}")
    hsts = headers.get("strict-transport-security", "")
    if "max-age=" not in hsts:
        raise RuntimeError("Strict-Transport-Security is missing")
    csp = headers.get("content-security-policy", "")
    for directive in ("default-src 'self'", "object-src 'none'", "frame-ancestors 'none'"):
        if directive not in csp:
            raise RuntimeError(f"Content-Security-Policy is missing {directive}")


def run(origin: str) -> dict[str, object]:
    origin = canonical_origin(origin)

    health_headers, health_body = require_status(origin, "/api/health")
    validate_security_headers(health_headers)
    health = json.loads(health_body.decode("utf-8"))
    if health.get("ok") is not True:
        raise RuntimeError("/api/health did not report ok")

    _ready_headers, ready_body = require_status(origin, "/api/ready")
    ready = json.loads(ready_body.decode("utf-8"))
    if ready.get("ok") is not True or ready.get("database") != "ready":
        raise RuntimeError("/api/ready did not report a ready database")
    if ready.get("migration_checked") is not True:
        raise RuntimeError("/api/ready did not verify the production migration revision")
    if ready.get("migration_revision") != ready.get("expected_revision"):
        raise RuntimeError("Database migration revision does not match the application")

    _runtime_headers, runtime_body = require_status(origin, "/api/runtime")
    runtime = json.loads(runtime_body.decode("utf-8"))
    expected_runtime = {
        "production": True,
        "public_origin_configured": True,
        "database_backend": "postgresql",
        "cookie_secure": True,
        "legacy_enabled": False,
    }
    for key, expected in expected_runtime.items():
        if runtime.get(key) != expected:
            raise RuntimeError(
                f"/api/runtime {key}={runtime.get(key)!r}, expected {expected!r}"
            )
    worker = runtime.get("worker") or {}
    if worker.get("healthy") is not True:
        raise RuntimeError(
            "Hosted worker heartbeat is missing or stale"
            + (
                f" (last seen {worker.get('last_seen_at')})"
                if worker.get("last_seen_at")
                else ""
            )
        )

    privacy_headers, privacy_body = require_status(origin, "/privacy")
    validate_security_headers(privacy_headers)
    privacy_text = privacy_body.decode("utf-8", errors="replace")
    if "Chrome Web Store Limited Use" not in privacy_text:
        raise RuntimeError("Hosted privacy page is missing the Limited Use disclosure")
    if "Privacy policy draft" in privacy_text:
        raise RuntimeError("Hosted privacy page is still marked as a draft")

    return {
        "ok": True,
        "origin": origin,
        "database_revision": ready.get("migration_revision"),
        "billing_enabled": runtime.get("billing_enabled"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("origin", help="Production origin, for example https://reseller.example")
    args = parser.parse_args()
    print(json.dumps(run(args.origin), indent=2))


if __name__ == "__main__":
    main()
