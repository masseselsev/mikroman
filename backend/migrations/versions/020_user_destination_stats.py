"""user destination stats

Revision ID: 020_user_destination_stats
Revises: 019_router_archive_and_serial
Create Date: 2026-09-04 01:55:00.000000

Creates the user_destination_stats table tracking traffic volume and connection hits
per destination IP/domain per user or device.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "020_user_destination_stats"
down_revision: Union[str, None] = "019_router_archive_and_serial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_destination_stats",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("device_id", sa.Integer(), nullable=True),
        sa.Column("destination_ip", sa.String(length=45), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column("country_code", sa.String(length=8), nullable=True),
        sa.Column("bytes_in", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_out", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_destination_stats_user_id", "user_destination_stats", ["user_id"])
    op.create_index("ix_user_destination_stats_device_id", "user_destination_stats", ["device_id"])
    op.create_index("ix_user_destination_stats_destination_ip", "user_destination_stats", ["destination_ip"])
    op.create_index("ix_user_destination_stats_domain", "user_destination_stats", ["domain"])
    op.create_index("ix_user_destination_stats_total_bytes", "user_destination_stats", ["total_bytes"])
    op.create_index("ix_user_destination_stats_hit_count", "user_destination_stats", ["hit_count"])
    op.create_index("ix_user_destination_stats_last_seen", "user_destination_stats", ["last_seen"])


def downgrade() -> None:
    op.drop_index("ix_user_destination_stats_last_seen", table_name="user_destination_stats")
    op.drop_index("ix_user_destination_stats_hit_count", table_name="user_destination_stats")
    op.drop_index("ix_user_destination_stats_total_bytes", table_name="user_destination_stats")
    op.drop_index("ix_user_destination_stats_domain", table_name="user_destination_stats")
    op.drop_index("ix_user_destination_stats_destination_ip", table_name="user_destination_stats")
    op.drop_index("ix_user_destination_stats_device_id", table_name="user_destination_stats")
    op.drop_index("ix_user_destination_stats_user_id", table_name="user_destination_stats")
    op.drop_table("user_destination_stats")

