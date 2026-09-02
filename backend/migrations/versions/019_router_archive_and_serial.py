"""router archive + serial identity

Revision ID: 019_router_archive_and_serial
Revises: 018_user_traffic_buckets
Create Date: 2026-09-02 18:40:00.000000

Two columns on ``routers``:

* ``serial_number`` - the RouterBoard serial, read from /system/routerboard.
  The stable hardware identity used to recognise a router that is added again
  after being archived, so its history and settings can be reattached.
* ``archived_at`` - set when the operator deletes a router but keeps its data.
  An archived router is hidden everywhere and touched by nothing until it is
  restored or purged. NULL means live.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "019_router_archive_and_serial"
down_revision: Union[str, None] = "018_user_traffic_buckets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("routers", sa.Column("serial_number", sa.String(length=120), nullable=True))
    op.add_column("routers", sa.Column("archived_at", sa.DateTime(), nullable=True))
    op.create_index("ix_routers_serial_number", "routers", ["serial_number"])


def downgrade() -> None:
    op.drop_index("ix_routers_serial_number", table_name="routers")
    op.drop_column("routers", "archived_at")
    op.drop_column("routers", "serial_number")
