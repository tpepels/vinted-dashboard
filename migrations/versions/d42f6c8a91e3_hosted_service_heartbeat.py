"""hosted service heartbeat

Revision ID: d42f6c8a91e3
Revises: c8d9f4a27b61
Create Date: 2026-10-01 21:55:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.db


revision: str = "d42f6c8a91e3"
down_revision: Union[str, Sequence[str], None] = "c8d9f4a27b61"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "service_heartbeats",
        sa.Column("service", sa.String(length=50), nullable=False),
        sa.Column("instance_id", sa.String(length=200), nullable=True),
        sa.Column("last_seen_at", app.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column("detail", app.db.JSONVariant, nullable=False),
        sa.PrimaryKeyConstraint("service"),
    )
    op.create_index(
        "ix_service_heartbeats_last_seen_at",
        "service_heartbeats",
        ["last_seen_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_service_heartbeats_last_seen_at", table_name="service_heartbeats")
    op.drop_table("service_heartbeats")
