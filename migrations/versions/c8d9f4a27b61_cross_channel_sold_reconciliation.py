"""cross-channel sold reconciliation audit

Revision ID: c8d9f4a27b61
Revises: b31c2f0f0e9d
Create Date: 2026-10-01 20:20:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.db


revision: str = "c8d9f4a27b61"
down_revision: Union[str, Sequence[str], None] = "b31c2f0f0e9d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cross_channel_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("inventory_item_id", sa.Uuid(), nullable=True),
        sa.Column("trigger_sale_id", sa.Uuid(), nullable=False),
        sa.Column("channel_listing_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("action_type", sa.String(length=50), nullable=False),
        sa.Column("mode", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("detail", app.db.JSONVariant, nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", app.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", app.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column("completed_at", app.db.UTCDateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["inventory_item_id"], ["inventory_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["trigger_sale_id"], ["sales.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["channel_listing_id"], ["channel_listings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trigger_sale_id",
            "channel_listing_id",
            "action_type",
            name="uq_cross_channel_action_sale_listing_type",
        ),
    )
    op.create_index(
        "ix_cross_channel_actions_workspace_id",
        "cross_channel_actions",
        ["workspace_id"],
    )
    op.create_index(
        "ix_cross_channel_actions_inventory_item_id",
        "cross_channel_actions",
        ["inventory_item_id"],
    )
    op.create_index(
        "ix_cross_channel_actions_trigger_sale_id",
        "cross_channel_actions",
        ["trigger_sale_id"],
    )
    op.create_index(
        "ix_cross_channel_actions_channel_listing_id",
        "cross_channel_actions",
        ["channel_listing_id"],
    )
    op.create_index(
        "ix_cross_channel_actions_workspace_status",
        "cross_channel_actions",
        ["workspace_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_cross_channel_actions_workspace_status", table_name="cross_channel_actions")
    op.drop_index("ix_cross_channel_actions_channel_listing_id", table_name="cross_channel_actions")
    op.drop_index("ix_cross_channel_actions_trigger_sale_id", table_name="cross_channel_actions")
    op.drop_index("ix_cross_channel_actions_inventory_item_id", table_name="cross_channel_actions")
    op.drop_index("ix_cross_channel_actions_workspace_id", table_name="cross_channel_actions")
    op.drop_table("cross_channel_actions")
