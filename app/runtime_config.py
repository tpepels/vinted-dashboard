"""Runtime configuration validation for hosted deployments.

Local/self-hosted development remains permissive. When APP_ENV=production,
startup fails before migrations/web/worker work begins if a deployment would
silently fall back to insecure or single-host development defaults.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from sqlalchemy import text

from app import db


ROOT = Path(__file__).resolve().parents[1]
_PRODUCTION_NAMES = {"production", "prod"}


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def app_environment(env: Mapping[str, str] | None = None) -> str:
    values = env if env is not None else os.environ
    return str(values.get("APP_ENV", "development")).strip().lower() or "development"


def is_production(env: Mapping[str, str] | None = None) -> bool:
    return app_environment(env) in _PRODUCTION_NAMES


def _public_origin(value: str) -> str | None:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return None
    parsed = urlparse(raw)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        return None
    host = parsed.hostname.lower()
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        return None
    return raw


def public_app_origin(env: Mapping[str, str] | None = None) -> str | None:
    values = env if env is not None else os.environ
    return _public_origin(str(values.get("PUBLIC_APP_URL", "")))


def privacy_contact_email(env: Mapping[str, str] | None = None) -> str | None:
    values = env if env is not None else os.environ
    raw = str(values.get("PRIVACY_CONTACT_EMAIL", "")).strip()
    if not raw or any(ch.isspace() for ch in raw):
        return None
    if raw.count("@") != 1:
        return None
    local, domain = raw.split("@", 1)
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        return None
    return raw


def validate_configuration(
    env: Mapping[str, str] | None = None,
    *,
    database_url: str | None = None,
) -> list[str]:
    values = env if env is not None else os.environ
    if not is_production(values):
        return []

    errors: list[str] = []
    url = str(database_url or values.get("DATABASE_URL") or db.DATABASE_URL)

    if public_app_origin(values) is None:
        errors.append("PUBLIC_APP_URL must be a bare non-local HTTPS origin")

    if privacy_contact_email(values) is None:
        errors.append("PRIVACY_CONTACT_EMAIL must be a valid public contact email")

    if not _truthy(values.get("COOKIE_SECURE")):
        errors.append("COOKIE_SECURE must be true")

    if not url.startswith(("postgresql://", "postgresql+")):
        errors.append("DATABASE_URL must use PostgreSQL in production")

    encryption_key = str(values.get("APP_ENCRYPTION_KEY", "")).strip()
    if not encryption_key:
        errors.append("APP_ENCRYPTION_KEY must be configured")
    else:
        try:
            Fernet(encryption_key.encode("ascii"))
        except (ValueError, TypeError, UnicodeError):
            errors.append("APP_ENCRYPTION_KEY must be a valid Fernet key")

    if _truthy(values.get("LISTING_ASSISTANT_ENABLED")):
        if not str(values.get("OPENAI_API_KEY", "")).strip():
            errors.append(
                "OPENAI_API_KEY must be configured when LISTING_ASSISTANT_ENABLED is true"
            )
        if not str(values.get("OPENAI_VISION_MODEL", "gpt-6-luna")).strip():
            errors.append(
                "OPENAI_VISION_MODEL must be configured when LISTING_ASSISTANT_ENABLED is true"
            )

    try:
        iterations = int(str(values.get("PASSWORD_HASH_ITERATIONS", "310000")))
    except ValueError:
        iterations = 0
    if iterations < 200_000:
        errors.append("PASSWORD_HASH_ITERATIONS must be at least 200000")

    try:
        session_days = int(str(values.get("AUTH_SESSION_DAYS", "30")))
    except ValueError:
        session_days = 0
    if not 1 <= session_days <= 90:
        errors.append("AUTH_SESSION_DAYS must be between 1 and 90")

    if _truthy(values.get("BILLING_ENABLED")):
        try:
            trial_days = int(str(values.get("BILLING_TRIAL_DAYS", "14")))
        except ValueError:
            trial_days = 0
        if not 1 <= trial_days <= 90:
            errors.append("BILLING_TRIAL_DAYS must be between 1 and 90")
        if str(values.get("BILLING_PROVIDER", "stripe")).strip().lower() != "stripe":
            errors.append("BILLING_PROVIDER must be stripe when billing is enabled")
        for name in ("STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET"):
            if not str(values.get(name, "")).strip():
                errors.append(f"{name} must be configured when billing is enabled")

    return errors


def require_valid_configuration(
    env: Mapping[str, str] | None = None,
    *,
    database_url: str | None = None,
) -> None:
    errors = validate_configuration(env, database_url=database_url)
    if errors:
        raise RuntimeError(
            "Invalid production configuration:\n"
            + "\n".join(f"- {error}" for error in errors)
        )


def expected_database_revision() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"Expected one Alembic head, found {len(heads)}")
    return heads[0]


def database_readiness(*, check_migration: bool | None = None) -> dict[str, object]:
    strict = is_production() if check_migration is None else bool(check_migration)
    with db.session_scope() as session:
        session.execute(text("SELECT 1"))
        current = None
        expected = None
        if strict:
            expected = expected_database_revision()
            try:
                current = session.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
            except Exception as exc:
                raise RuntimeError("Database migration state is unavailable") from exc
            if current != expected:
                raise RuntimeError(
                    f"Database migration is not current ({current or 'none'} != {expected})"
                )
    return {
        "database": "ready",
        "migration_checked": strict,
        "migration_revision": current,
        "expected_revision": expected,
    }


def safe_runtime_summary() -> dict[str, object]:
    return {
        "environment": app_environment(),
        "production": is_production(),
        "public_origin_configured": public_app_origin() is not None,
        "privacy_contact_configured": privacy_contact_email() is not None,
        "database_backend": db.engine.url.get_backend_name(),
        "cookie_secure": _truthy(os.getenv("COOKIE_SECURE")),
        "billing_enabled": _truthy(os.getenv("BILLING_ENABLED")),
        "listing_assistant_enabled": _truthy(
            os.getenv("LISTING_ASSISTANT_ENABLED")
        ),
    }


def main() -> None:
    require_valid_configuration()
    summary = safe_runtime_summary()
    print(
        "runtime configuration: "
        + ", ".join(f"{key}={value}" for key, value in summary.items())
    )


if __name__ == "__main__":
    main()
