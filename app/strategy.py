"""Centralized reseller action thresholds.

Defaults are deliberately modest and can be overridden per workspace under
workspace.settings["strategy"].  Keeping them here avoids scattering magic
numbers through UI and API code.
"""

from __future__ import annotations

from typing import Any


DEFAULT_STRATEGY = {
    "stale_days": 45,
    "very_stale_days": 90,
    "high_favourites": 5,
    "momentum_favourites_7d": 3,
    "low_favourites": 1,
}


def strategy_settings(workspace_settings: dict[str, Any] | None) -> dict[str, int]:
    result = dict(DEFAULT_STRATEGY)
    raw = dict((workspace_settings or {}).get("strategy") or {})
    for key in result:
        try:
            value = int(raw[key])
        except (KeyError, TypeError, ValueError):
            continue
        if value >= 0:
            result[key] = value
    return result
