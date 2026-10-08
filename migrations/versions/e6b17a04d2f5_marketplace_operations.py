"""Persistent marketplace operation ledger

Revision ID: e6b17a04d2f5
Revises: d42f6c8a91e3
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import app.db


revision: str = "e6b17a04d2f5"
down_revision: Union[str, Sequence[str], None] = "d42f6c8a91e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "marketplace_operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("operation_type", sa.String(length=30), nullable=False),
        sa.Column("target_key", sa.String(length=180), nullable=False),
        sa.Column("active_key", sa.String(length=240), nullable=True),
        sa.Column("inventory_item_id", sa.Uuid(), nullable=True),
        sa.Column("channel_listing_id", sa.Uuid(), nullable=True),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("job_type", sa.String(length=80), nullable=True),
        sa.Column("job_payload", app.db.JSONVariant, nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("verification", sa.String(length=30), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result", app.db.JSONVariant, nullable=False),
        sa.Column("created_at", app.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column("started_at", app.db.UTCDateTime(timezone=True), nullable=True),
        sa.Column("completed_at", app.db.UTCDateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["inventory_item_id"], ["inventory_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["channel_listing_id"], ["channel_listings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_id"], ["background_jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "active_key", name="uq_marketplace_operations_active_key"),
    )
    op.create_index("ix_marketplace_operations_workspace_id", "marketplace_operations", ["workspace_id"])
    op.create_index(
        "ix_marketplace_operations_workspace_created",
        "marketplace_operations", ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_marketplace_operations_workspace_status",
        "marketplace_operations", ["workspace_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_marketplace_operations_workspace_status", table_name="marketplace_operations")
    op.drop_index("ix_marketplace_operations_workspace_created", table_name="marketplace_operations")
    op.drop_index("ix_marketplace_operations_workspace_id", table_name="marketplace_operations")
    op.drop_table("marketplace_operations")
