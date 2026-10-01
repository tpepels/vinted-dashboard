"""Connector architecture.

This package holds per-channel connector metadata (:mod:`app.connectors.base`)
and the live dual-write layer (:mod:`app.connectors.workspace_sync`) that
keeps the workspace/inventory ORM schema (``app.models``) continuously up to
date as real Vinted/BIBLIO/eBay syncs run, rather than relying solely on the
one-time legacy backfill (:mod:`app.legacy_migration`).
"""

from __future__ import annotations
