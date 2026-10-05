"""Marketplace connector package.

`base` defines channel metadata, `hosted` contains remote marketplace
adapters, `workspace_sync` is the strict persistence boundary into the shared
workspace data model, and format-specific parsing lives in focused modules such
as `biblio_format`.

Pre-workspace migration is intentionally outside this package in
:mod:`app.legacy_migration`.
"""

from __future__ import annotations
