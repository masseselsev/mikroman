"""per-interface daily traffic rollups

``interface_traffic_rollups`` - one row per (router, interface, router-local
date), rebuilt from the ``interface_metrics`` samples rather than accumulated
live. Two things use it:

* the WireGuard / ZeroTier / tunnel breakdown tab, which needs per-interface
  history beyond the 30-day sample retention;

* the gateway rollup (``router_traffic_rollups``), now derived by summing the
  monitored interfaces' rows for a day instead of differencing a live counter.
  Walking the samples attributes each byte to the date it was actually
  transferred on and is unaffected by a container restart, so a day the old
  accumulator misfiled across the local midnight comes out right on the next
  recompute.

Revision ID: 015_interface_traffic
Revises: 014_billing_anchor_time
Create Date: 2026-09-01 15:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "015_interface_traffic"
down_revision: Union[str, None] = "014_billing_anchor_time"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "interface_traffic_rollups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("router_id", sa.Integer(), nullable=True),
        sa.Column("interface_name", sa.String(length=100), nullable=False),
        sa.Column("record_date", sa.Date(), nullable=False),
        sa.Column("bytes_in", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("bytes_out", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.ForeignKeyConstraint(["router_id"], ["routers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "router_id", "interface_name", "record_date",
            name="uq_interface_traffic_rollup_day",
        ),
    )
    op.create_index(
        "ix_interface_traffic_rollups_router_id", "interface_traffic_rollups", ["router_id"]
    )
    op.create_index(
        "ix_interface_traffic_rollups_interface_name",
        "interface_traffic_rollups", ["interface_name"],
    )
    op.create_index(
        "ix_interface_traffic_rollups_record_date",
        "interface_traffic_rollups", ["record_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_interface_traffic_rollups_record_date", table_name="interface_traffic_rollups")
    op.drop_index("ix_interface_traffic_rollups_interface_name", table_name="interface_traffic_rollups")
    op.drop_index("ix_interface_traffic_rollups_router_id", table_name="interface_traffic_rollups")
    op.drop_table("interface_traffic_rollups")
