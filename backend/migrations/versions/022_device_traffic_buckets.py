"""device traffic buckets

Revision ID: 022_device_traffic_buckets
Revises: 021_router_backups
Create Date: 2026-09-04 18:40:00.000000

Adds the per-device half-hour bucket table backing the device history modal's
24H view. The user modal already had `user_traffic_buckets`; devices fell back
to day resolution because they had no equivalent source.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "022_device_traffic_buckets"
down_revision: Union[str, None] = "021_router_backups"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "device_traffic_buckets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("bucket_start", sa.DateTime(), nullable=False),
        sa.Column("bytes_in", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_out", sa.BigInteger(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "bucket_start", name="uq_device_traffic_bucket"),
    )
    op.create_index(
        "ix_device_traffic_buckets_device_id", "device_traffic_buckets", ["device_id"]
    )
    op.create_index(
        "ix_device_traffic_buckets_bucket_start", "device_traffic_buckets", ["bucket_start"]
    )


def downgrade() -> None:
    op.drop_index("ix_device_traffic_buckets_bucket_start", table_name="device_traffic_buckets")
    op.drop_index("ix_device_traffic_buckets_device_id", table_name="device_traffic_buckets")
    op.drop_table("device_traffic_buckets")
