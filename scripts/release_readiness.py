#!/usr/bin/env python3
"""Hosted release-readiness gate.

This complements runtime startup validation and hosted_smoke.py:
- runtime validation proves the process is configured safely;
- this script checks repository/release consistency and operational evidence;
- with --origin it also runs the non-destructive live hosted smoke test.

It intentionally does not claim that a backup exists merely because a provider
name is configured. A public release must supply the last restore-drill date as
operational evidence.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.runtime_config import privacy_contact_email, validate_configuration


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "app" / "extension" / "manifest.json"
PRIVACY_PATH = ROOT / "app" / "product_static" / "privacy.html"


def _parse_drill_date(value: str) -> date:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("BACKUP_RESTORE_DRILL_AT is required for release readiness")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("BACKUP_RESTORE_DRILL_AT must use YYYY-MM-DD") from exc


def collect_checks(
    env: dict[str, str] | None = None,
    *,
    today: date | None = None,
    max_restore_drill_age_days: int = 90,
    live_origin: str | None = None,
) -> dict[str, Any]:
    values = dict(os.environ if env is None else env)
    checks: list[dict[str, Any]] = []
    failures: list[str] = []

    config_errors = validate_configuration(values)
    checks.append(
        {
            "name": "production_configuration",
            "ok": not config_errors,
            "detail": config_errors or "Production configuration is valid",
        }
    )
    failures.extend(config_errors)

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    source_version = str(manifest.get("version") or "").strip()
    declared_version = str(values.get("EXTENSION_LATEST_VERSION", "")).strip()
    version_ok = bool(source_version and declared_version == source_version)
    checks.append(
        {
            "name": "extension_version",
            "ok": version_ok,
            "detail": {
                "source": source_version,
                "declared": declared_version or None,
            },
        }
    )
    if not version_ok:
        failures.append(
            "EXTENSION_LATEST_VERSION must match app/extension/manifest.json "
            f"({declared_version or 'unset'} != {source_version or 'unset'})"
        )

    privacy_text = PRIVACY_PATH.read_text(encoding="utf-8")
    contact = privacy_contact_email(values)
    privacy_ok = (
        "Chrome Web Store Limited Use" in privacy_text
        and "__PRIVACY_CONTACT__" in privacy_text
        and "Privacy policy draft" not in privacy_text
        and contact is not None
    )
    checks.append(
        {
            "name": "privacy_policy",
            "ok": privacy_ok,
            "detail": {
                "limited_use": "Chrome Web Store Limited Use" in privacy_text,
                "contact_configured": contact is not None,
                "template_contact_slot": "__PRIVACY_CONTACT__" in privacy_text,
                "draft_marker": "Privacy policy draft" in privacy_text,
            },
        }
    )
    if not privacy_ok:
        failures.append("Privacy policy/contact is not release-ready")

    backup_provider = str(values.get("BACKUP_PROVIDER", "")).strip()
    backup_provider_ok = bool(backup_provider)
    checks.append(
        {
            "name": "backup_provider",
            "ok": backup_provider_ok,
            "detail": backup_provider or None,
        }
    )
    if not backup_provider_ok:
        failures.append("BACKUP_PROVIDER must name the hosted PostgreSQL backup mechanism")

    drill_ok = False
    drill_detail: dict[str, Any] = {}
    try:
        drill_date = _parse_drill_date(values.get("BACKUP_RESTORE_DRILL_AT", ""))
        current = today or datetime.now(timezone.utc).date()
        age = (current - drill_date).days
        drill_ok = 0 <= age <= max(1, int(max_restore_drill_age_days))
        drill_detail = {
            "date": drill_date.isoformat(),
            "age_days": age,
            "max_age_days": max(1, int(max_restore_drill_age_days)),
        }
        if age < 0:
            failures.append("BACKUP_RESTORE_DRILL_AT cannot be in the future")
        elif not drill_ok:
            failures.append(
                "PostgreSQL restore drill is stale "
                f"({age} days old; max {max_restore_drill_age_days})"
            )
    except ValueError as exc:
        drill_detail = {"error": str(exc)}
        failures.append(str(exc))
    checks.append({"name": "restore_drill", "ok": drill_ok, "detail": drill_detail})

    live: dict[str, Any] | None = None
    if live_origin:
        try:
            from scripts.hosted_smoke import run as hosted_smoke

            live = hosted_smoke(live_origin)
            checks.append({"name": "live_hosted_smoke", "ok": True, "detail": live})
        except Exception as exc:
            live = {"ok": False, "error": str(exc)}
            checks.append({"name": "live_hosted_smoke", "ok": False, "detail": live})
            failures.append(f"Live hosted smoke failed: {exc}")

    return {
        "ok": not failures,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "failures": failures,
        "live": live,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check whether a hosted Reseller Dashboard release is ready"
    )
    parser.add_argument(
        "--origin",
        help="Optional deployed HTTPS origin; runs scripts/hosted_smoke.py checks too",
    )
    parser.add_argument(
        "--max-restore-drill-age-days",
        type=int,
        default=90,
        help="Maximum accepted age of the recorded restore drill (default: 90)",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = collect_checks(
        max_restore_drill_age_days=args.max_restore_drill_age_days,
        live_origin=args.origin,
    )
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        for check in result["checks"]:
            label = "OK" if check["ok"] else "FAIL"
            print(f"[{label}] {check['name']}: {check['detail']}")
        if result["failures"]:
            print("Release blockers:")
            for failure in result["failures"]:
                print(f"- {failure}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
