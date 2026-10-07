"""Shared pytest fixtures.

Autouse so every test gets a fresh, isolated in-memory workspace/inventory
ORM database. ``app.db``'s engine/session factory is process-global state;
since Phase 2, even legacy-sqlite-only code paths (``app.channels``)
dual-write into it via ``app.connectors.workspace_sync``, so every test
needs isolation from it, not just tests that touch the ORM schema directly.
"""

from __future__ import annotations

import pytest

from app import auth, db


@pytest.fixture(autouse=True)
def _isolated_orm_database():
    db.init_engine("sqlite://")
    db.create_all()
    auth.rate_limiter._events.clear()
    yield
    auth.rate_limiter._events.clear()
