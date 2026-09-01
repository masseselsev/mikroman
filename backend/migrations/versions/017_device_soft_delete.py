"""device soft delete

Revision ID: 017_device_soft_delete
Revises: 016_add_router_id_to_users
Create Date: 2026-09-02 01:35:00.000000

A deleted device keeps its row and its daily traffic rollups so the bytes
it moved stay attributed to the profile that owned it, shown collectively
as "Old devices". The row is hidden everywhere a live device would appear
and its router-side accounting rule is pruned on the next sync.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "017_device_soft_delete"
down_revision: Union[str, None] = "016_add_router_id_to_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_deleted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.create_index("ix_devices_is_deleted", ["is_deleted"])


def downgrade() -> None:
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.drop_index("ix_devices_is_deleted")
        batch_op.drop_column("is_deleted")
