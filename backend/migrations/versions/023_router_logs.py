"""router logs

Revision ID: 023_router_logs
Revises: 022_device_traffic_buckets
Create Date: 2026-09-04 18:55:00.000000

The `RouterLog` model shipped without a migration. `init_db()` happens to create
new tables with `create_all`, which hid this on the running deployment, but a
database managed purely through Alembic would have had no `router_logs` table at
all and the log scraper would have failed on every tick.

Composite indexes match how the table is actually read: newest-first per router
for the viewer, and per-router-by-severity for the 24h stats badge.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "023_router_logs"
down_revision: Union[str, None] = "022_device_traffic_buckets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "router_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("router_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=32), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("topics", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["router_id"], ["routers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_router_logs_id", "router_logs", ["id"])
    op.create_index("ix_router_logs_router_id", "router_logs", ["router_id"])
    op.create_index("ix_router_logs_external_id", "router_logs", ["external_id"])
    op.create_index("ix_router_logs_timestamp", "router_logs", ["timestamp"])
    op.create_index("ix_router_logs_topics", "router_logs", ["topics"])
    op.create_index("ix_router_logs_severity", "router_logs", ["severity"])
    op.create_index("ix_router_logs_category", "router_logs", ["category"])
    op.create_index("ix_router_logs_router_time", "router_logs", ["router_id", "timestamp"])
    op.create_index("ix_router_logs_router_severity", "router_logs", ["router_id", "severity"])


def downgrade() -> None:
    for name in (
        "ix_router_logs_router_severity",
        "ix_router_logs_router_time",
        "ix_router_logs_category",
        "ix_router_logs_severity",
        "ix_router_logs_topics",
        "ix_router_logs_timestamp",
        "ix_router_logs_external_id",
        "ix_router_logs_router_id",
        "ix_router_logs_id",
    ):
        op.drop_index(name, table_name="router_logs")
    op.drop_table("router_logs")
