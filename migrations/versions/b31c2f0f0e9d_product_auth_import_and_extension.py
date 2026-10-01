"""product auth, import/export and extension pairing

Revision ID: b31c2f0f0e9d
Revises: 70dfdacc746d
Create Date: 2026-10-01 18:20:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.db


revision: str = "b31c2f0f0e9d"
down_revision: Union[str, Sequence[str], None] = "70dfdacc746d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", app.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", app.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column("created_at", app.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])

    op.create_table(
        "extension_pairings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", app.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", app.db.UTCDateTime(timezone=True), nullable=True),
        sa.Column("created_at", app.db.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash"),
    )
    op.create_index("ix_extension_pairings_workspace_id", "extension_pairings", ["workspace_id"])
    op.create_index("ix_extension_pairings_expires_at", "extension_pairings", ["expires_at"])

    op.create_table(
        "extension_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("extension_version", sa.String(length=50), nullable=True),
        sa.Column("last_seen_at", app.db.UTCDateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", app.db.UTCDateTime(timezone=True), nullable=True),
        sa.Column("created_at", app.db.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_extension_credentials_workspace_id", "extension_credentials", ["workspace_id"])
    op.create_index(
        "ix_extension_credentials_workspace_active",
        "extension_credentials",
        ["workspace_id", "revoked_at"],
    )

    op.create_table(
        "mapping_presets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("direction", sa.String(length=20), nullable=False),
        sa.Column("file_type", sa.String(length=20), nullable=False),
        sa.Column("mapping", app.db.JSONVariant, nullable=False),
        sa.Column("options", app.db.JSONVariant, nullable=False),
        sa.Column("created_at", app.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", app.db.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "direction", "name",
            name="uq_mapping_preset_workspace_direction_name",
        ),
    )
    op.create_index("ix_mapping_presets_workspace_id", "mapping_presets", ["workspace_id"])

    op.create_table(
        "import_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("file_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("mapping", app.db.JSONVariant, nullable=False),
        sa.Column("options", app.db.JSONVariant, nullable=False),
        sa.Column("summary", app.db.JSONVariant, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", app.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column("applied_at", app.db.UTCDateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_import_jobs_workspace_id", "import_jobs", ["workspace_id"])

    op.create_table(
        "export_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("file_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("mapping", app.db.JSONVariant, nullable=False),
        sa.Column("options", app.db.JSONVariant, nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", app.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column("completed_at", app.db.UTCDateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_export_jobs_workspace_id", "export_jobs", ["workspace_id"])

    op.create_table(
        "connector_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("created_at", app.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", app.db.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "channel",
            name="uq_connector_credential_workspace_channel",
        ),
    )
    op.create_index("ix_connector_credentials_workspace_id", "connector_credentials", ["workspace_id"])

    op.create_table(
        "background_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("job_type", sa.String(length=80), nullable=False),
        sa.Column("payload", app.db.JSONVariant, nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", app.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column("locked_at", app.db.UTCDateTime(timezone=True), nullable=True),
        sa.Column("completed_at", app.db.UTCDateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", app.db.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_background_jobs_workspace_id", "background_jobs", ["workspace_id"])
    op.create_index(
        "ix_background_jobs_status_available",
        "background_jobs",
        ["status", "available_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_background_jobs_status_available", table_name="background_jobs")
    op.drop_index("ix_background_jobs_workspace_id", table_name="background_jobs")
    op.drop_table("background_jobs")
    op.drop_index("ix_connector_credentials_workspace_id", table_name="connector_credentials")
    op.drop_table("connector_credentials")
    op.drop_index("ix_export_jobs_workspace_id", table_name="export_jobs")
    op.drop_table("export_jobs")
    op.drop_index("ix_import_jobs_workspace_id", table_name="import_jobs")
    op.drop_table("import_jobs")
    op.drop_index("ix_mapping_presets_workspace_id", table_name="mapping_presets")
    op.drop_table("mapping_presets")
    op.drop_index("ix_extension_credentials_workspace_active", table_name="extension_credentials")
    op.drop_index("ix_extension_credentials_workspace_id", table_name="extension_credentials")
    op.drop_table("extension_credentials")
    op.drop_index("ix_extension_pairings_expires_at", table_name="extension_pairings")
    op.drop_index("ix_extension_pairings_workspace_id", table_name="extension_pairings")
    op.drop_table("extension_pairings")
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
