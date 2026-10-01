"""Verifies the Alembic migration chain actually runs cleanly end-to-end,
independent of any manual ``alembic upgrade head`` testing. This is what
would catch e.g. a bad autogenerate (missing import, wrong column type)
before it reaches a real deployment.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app import models

REPO_ROOT = Path(__file__).resolve().parent.parent


def _alembic_config(monkeypatch, database_url: str) -> Config:
    # migrations/env.py reads DATABASE_URL directly (it's the single source
    # of truth shared with docker-compose/.env), so the Config object's own
    # sqlalchemy.url option must be backed by the same env var here too.
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_alembic_upgrade_head_creates_all_tables(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'migration_test.sqlite3'}"
    config = _alembic_config(monkeypatch, database_url)

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    expected_tables = set(models.Base.metadata.tables.keys())
    # alembic_version is Alembic's own bookkeeping table, not an ORM model.
    assert expected_tables <= tables
    assert "alembic_version" in tables


def test_alembic_downgrade_base_drops_all_tables(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'migration_test.sqlite3'}"
    config = _alembic_config(monkeypatch, database_url)

    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    expected_tables = set(models.Base.metadata.tables.keys())
    assert not (expected_tables & tables)


def test_alembic_autogenerate_detects_no_drift(tmp_path, monkeypatch):
    """Guards against the models and the committed migration silently
    drifting apart (e.g. a model field added without a matching migration)."""

    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    database_url = f"sqlite:///{tmp_path / 'migration_test.sqlite3'}"
    config = _alembic_config(monkeypatch, database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"render_as_batch": True})
            diff = compare_metadata(context, models.Base.metadata)
    finally:
        engine.dispose()

    assert diff == []
