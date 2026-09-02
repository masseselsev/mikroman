"""user traffic buckets (half-hour intraday history)

Revision ID: 018_user_traffic_buckets
Revises: 017_device_soft_delete
Create Date: 2026-09-02 17:05:00.000000

A short-lived companion to traffic_rollups: one row per user per 30-minute
window of router-local time, so the history modal's 1D view can show the shape
of a single day instead of one flat bar. The daily rollups remain the
authoritative long-term record; these buckets are pruned after ~two weeks.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "018_user_traffic_buckets"
down_revision: Union[str, None] = "017_device_soft_delete"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_traffic_buckets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("bucket_start", sa.DateTime(), nullable=False),
        sa.Column("bytes_in", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_out", sa.BigInteger(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "bucket_start", name="uq_user_traffic_bucket"),
    )
    op.create_index("ix_user_traffic_buckets_user_id", "user_traffic_buckets", ["user_id"])
    op.create_index(
        "ix_user_traffic_buckets_bucket_start", "user_traffic_buckets", ["bucket_start"]
    )


def downgrade() -> None:
    op.drop_index("ix_user_traffic_buckets_bucket_start", table_name="user_traffic_buckets")
    op.drop_index("ix_user_traffic_buckets_user_id", table_name="user_traffic_buckets")
    op.drop_table("user_traffic_buckets")
