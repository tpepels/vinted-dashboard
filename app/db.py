"""Database engine/session management for the workspace/inventory schema.

This is a separate, ORM-backed database from the legacy ``sqlite3`` files
used by :mod:`app.intelligence` and :mod:`app.channels` (``VINTED_HISTORY_DB``).
Keeping them separate lets Phase 1 add the new schema without touching the
still-operational legacy code paths. ``app.legacy_migration`` copies data
from the legacy database(s) into this one.

``DATABASE_URL`` controls which database/engine is used:

- ``sqlite:////app/data/app.sqlite3`` (default) - zero-setup local/dev option.
- ``postgresql+psycopg2://user:pass@host:5432/dbname`` - production, and the
  default when running via ``docker compose`` (see ``docker-compose.yml``).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, TypeDecorator, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

#: Cross-dialect JSON column type: JSONB on PostgreSQL, JSON (TEXT-backed) on
#: SQLite. Use this for any category-specific/opaque metadata column instead
#: of adding new nullable columns per category/connector.
JSONVariant = JSON().with_variant(JSONB(), "postgresql")


class UTCDateTime(TypeDecorator):
    """A timezone-aware UTC ``DateTime`` that behaves identically on SQLite
    and PostgreSQL.

    SQLite has no native timezone-aware datetime type: values written as
    timezone-aware come back from a query as naive ``datetime`` objects,
    while PostgreSQL's ``TIMESTAMPTZ`` preserves them correctly. Comparing a
    freshly-constructed aware datetime against one read back from SQLite
    then raises ``TypeError``. This type normalizes both directions so every
    value handled by the ORM is a timezone-aware UTC ``datetime``,
    regardless of backend.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for every ORM model in the new schema."""


def default_database_url() -> str:
    return "sqlite:////app/data/app.sqlite3"


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        # SQLite connections are not thread-safe by default; FastAPI/uvicorn
        # may use a session across threads within one logical request.
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


def _create_engine(url: str) -> Engine:
    return create_engine(url, **_engine_kwargs(url))


DATABASE_URL = os.getenv("DATABASE_URL", default_database_url())
engine: Engine = _create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_engine(url: str) -> Engine:
    """(Re)configure the module-level engine/session factory.

    Used by tests (and could be used by a CLI/admin command) to point the
    application at an isolated database without relying on process-wide
    environment variables.
    """
    global DATABASE_URL, engine, SessionLocal
    engine.dispose()
    DATABASE_URL = url
    engine = _create_engine(url)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return engine


def create_all() -> None:
    """Create all ORM tables directly from metadata for tests/ad-hoc use."""
    # Import side effects register every model on Base.metadata.
    from app import models as _models  # noqa: F401
    from app import product_models as _product_models  # noqa: F401
    Base.metadata.create_all(bind=engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager yielding a session that commits on success and rolls
    back on exception."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency: ``session: Session = Depends(get_session)``."""
    with session_scope() as session:
        yield session
