"""Redacted rotating diagnostics for web/worker processes.

Both containers mount the same data directory, so these logs provide one
application-level view without exposing the Docker socket.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import time
import zipfile
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DIAGNOSTICS_DIR = Path(
    os.getenv("DIAGNOSTICS_DIR", str(ROOT / "data" / "diagnostics"))
)
MAX_BYTES = max(256_000, int(os.getenv("DIAGNOSTICS_LOG_MAX_BYTES", "2097152")))
BACKUPS = max(1, int(os.getenv("DIAGNOSTICS_LOG_BACKUPS", "3")))

_SECRET_NAME = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|authorization|cookie|credential|"
    r"client[_-]?secret|refresh[_-]?token|access[_-]?token|encryption[_-]?key)",
    re.I,
)
_REDACTION_PATTERNS = (
    re.compile(r"(?i)(authorization:\s*(?:bearer|basic)\s+)\S+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)\b(password|passwd|secret|token|api[_-]?key|client[_-]?secret|"
        r"refresh[_-]?token|access[_-]?token)(\s*[=:]\s*)([^\s,;]+)"
    ),
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _known_secret_values() -> list[str]:
    values: list[str] = []
    for key, value in os.environ.items():
        raw = str(value or "").strip()
        if raw and len(raw) >= 8 and _SECRET_NAME.search(key):
            values.append(raw)
    return sorted(set(values), key=len, reverse=True)


def redact_text(value: Any) -> str:
    text = str(value if value is not None else "")
    for secret in _known_secret_values():
        text = text.replace(secret, "[REDACTED]")
    for pattern in _REDACTION_PATTERNS:
        if pattern.groups >= 3:
            text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
        else:
            text = pattern.sub(lambda m: f"{m.group(1)}[REDACTED]", text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def _log_path(service: str) -> Path:
    clean = re.sub(r"[^a-z0-9_-]+", "-", service.lower()).strip("-") or "app"
    return DIAGNOSTICS_DIR / f"{clean}.log"


def setup_diagnostics(service: str) -> Path:
    DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    path = _log_path(service)
    marker = f"reseller-diagnostics:{service}"
    handler = RotatingFileHandler(
        path,
        maxBytes=MAX_BYTES,
        backupCount=BACKUPS,
        encoding="utf-8",
    )
    handler.setLevel(logging.INFO)
    formatter = RedactingFormatter(
        f"%(asctime)sZ %(levelname)s {service} %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    setattr(handler, "_diagnostics_marker", marker)

    targets = [logging.getLogger()]
    if service == "web":
        targets.extend([
            logging.getLogger("uvicorn.error"),
            logging.getLogger("uvicorn.access"),
            logging.getLogger("fastapi"),
        ])
    for logger in targets:
        if any(getattr(h, "_diagnostics_marker", None) == marker for h in logger.handlers):
            continue
        logger.addHandler(handler)
    logging.getLogger(__name__).info("diagnostics logging ready path=%s", path)
    return path


def _iter_log_files() -> Iterable[tuple[str, Path]]:
    if not DIAGNOSTICS_DIR.exists():
        return []
    rows: list[tuple[str, Path]] = []
    for path in sorted(DIAGNOSTICS_DIR.glob("*.log*")):
        if path.is_file():
            rows.append((path.name.split(".log", 1)[0], path))
    return rows


def recent_logs(*, limit: int = 300) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for source, path in _iter_log_files():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines[-max(20, limit):]:
            rows.append({"source": source, "line": redact_text(line)})
    rows.sort(key=lambda row: row["line"][:20])
    return rows[-max(1, min(int(limit), 1000)):]


def _safe_json(value: Any) -> str:
    return redact_text(json.dumps(value, indent=2, sort_keys=True, default=str))


def build_bundle(
    *,
    browser_logs: list[dict[str, Any]] | None = None,
    snapshot: dict[str, Any] | None = None,
    include_server_logs: bool = True,
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        manifest = {
            "generated_at": utcnow().isoformat(),
            "server_logs_included": bool(include_server_logs),
            "note": (
                "Secrets are redacted. Logs can still contain user-entered titles/identifiers. "
                "Raw process logs are excluded from production bundles because they are not workspace-scoped."
            ),
        }
        archive.writestr("README.txt", _safe_json(manifest) + "\n")
        archive.writestr("snapshot.json", _safe_json(snapshot or {}) + "\n")
        archive.writestr("browser.log", "\n".join(
            redact_text(json.dumps(row, sort_keys=True, default=str))
            for row in (browser_logs or [])[-1000:]
        ) + "\n")
        if include_server_logs:
            for _source, path in _iter_log_files():
                try:
                    body = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                archive.writestr(f"server/{path.name}", redact_text(body))
    return buffer.getvalue()
