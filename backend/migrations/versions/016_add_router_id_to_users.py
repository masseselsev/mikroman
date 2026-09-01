"""add router_id to users

Revision ID: 016_add_router_id_to_users
Revises: 015_interface_traffic
Create Date: 2026-09-01 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "016_add_router_id_to_users"
down_revision: Union[str, None] = "015_interface_traffic"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("router_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_users_router_id", "routers", ["router_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.create_index("ix_users_router_id", ["router_id"])


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index("ix_users_router_id")
        batch_op.drop_constraint("fk_users_router_id", type_="foreignkey")
        batch_op.drop_column("router_id")
