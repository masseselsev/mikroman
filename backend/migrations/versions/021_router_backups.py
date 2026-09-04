"""router backups and config drift

Revision ID: 021_router_backups
Revises: 020_user_destination_stats
Create Date: 2026-09-04 15:30:00.000000

Creates router_backups table for config drift tracking and automated backups.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "021_router_backups"
down_revision: Union[str, None] = "020_user_destination_stats"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "router_backups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("router_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=20), server_default="manual", nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column("rsc_content", sa.Text(), nullable=True),
        sa.Column("rsc_bytes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("backup_file_path", sa.String(length=500), nullable=True),
        sa.Column("backup_bytes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("backup_password", sa.String(length=128), nullable=True),
        sa.Column("is_pinned", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("serial", sa.String(length=100), nullable=True),
        sa.Column("os_version", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["router_id"], ["routers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_router_backups_id", "router_backups", ["id"])
    op.create_index("ix_router_backups_router_id", "router_backups", ["router_id"])
    op.create_index("ix_router_backups_created_at", "router_backups", ["created_at"])
    op.create_index("ix_router_backups_outcome", "router_backups", ["outcome"])
    op.create_index("ix_router_backups_fingerprint", "router_backups", ["fingerprint"])


def downgrade() -> None:
    op.drop_index("ix_router_backups_fingerprint", table_name="router_backups")
    op.drop_index("ix_router_backups_outcome", table_name="router_backups")
    op.drop_index("ix_router_backups_created_at", table_name="router_backups")
    op.drop_index("ix_router_backups_router_id", table_name="router_backups")
    op.drop_index("ix_router_backups_id", table_name="router_backups")
    op.drop_table("router_backups")
