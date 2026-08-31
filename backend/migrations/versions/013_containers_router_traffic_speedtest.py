"""container devices, router self-traffic accounting, speed test history

Three related additions, all consequences of the router being able to run
workloads of its own:

* ``devices.is_container`` - a container reached over a ``veth`` interface
  appears in the ARP table exactly like a laptop does, so without this it lands
  in the unassigned inbox waiting for somebody to claim it. Nobody owns it.

* ``router_self_traffic_rollups`` - per-device accounting matches the
  ``forward`` chain and therefore cannot see anything the router does for
  itself, including whatever its containers pull. That volume was previously
  visible only as part of the unexplained gap between the WAN total and the sum
  of the devices.

* ``speed_test_results`` - history for WAN speed tests run from a container on
  the router. Kept as a series rather than one latest value, because a single
  reading of a noisy quantity says very little.

Revision ID: 013_containers_router_traffic
Revises: 012_router_comment
Create Date: 2026-09-01 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '013_containers_router_traffic'
down_revision: Union[str, None] = '012_router_comment'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column("is_container", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )

    op.create_table(
        "router_self_traffic_rollups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("router_id", sa.Integer(), nullable=True),
        sa.Column("record_date", sa.Date(), nullable=False),
        sa.Column("bytes_in", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("bytes_out", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.ForeignKeyConstraint(["router_id"], ["routers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_router_self_traffic_rollups_router_id", "router_self_traffic_rollups", ["router_id"])
    op.create_index("ix_router_self_traffic_rollups_record_date", "router_self_traffic_rollups", ["record_date"])

    op.create_table(
        "speed_test_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("router_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("download_mbps", sa.Float(), nullable=True),
        sa.Column("upload_mbps", sa.Float(), nullable=True),
        sa.Column("ping_ms", sa.Float(), nullable=True),
        sa.Column("jitter_ms", sa.Float(), nullable=True),
        sa.Column("packet_loss_pct", sa.Float(), nullable=True),
        sa.Column("server_name", sa.String(length=200), nullable=True),
        sa.Column("isp", sa.String(length=200), nullable=True),
        sa.Column("result_url", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ok"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("raw_output", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["router_id"], ["routers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_speed_test_results_router_id", "speed_test_results", ["router_id"])
    op.create_index("ix_speed_test_results_created_at", "speed_test_results", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_speed_test_results_created_at", table_name="speed_test_results")
    op.drop_index("ix_speed_test_results_router_id", table_name="speed_test_results")
    op.drop_table("speed_test_results")

    op.drop_index("ix_router_self_traffic_rollups_record_date", table_name="router_self_traffic_rollups")
    op.drop_index("ix_router_self_traffic_rollups_router_id", table_name="router_self_traffic_rollups")
    op.drop_table("router_self_traffic_rollups")

    op.drop_column("devices", "is_container")
