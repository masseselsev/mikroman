"""add_user_sort_order

Lets the dashboard cards be arranged by hand. Without an explicit order the
cards fell back to insertion order, which cannot be changed once profiles exist.

Revision ID: 009_user_sort_order
Revises: 008_device_linking
Create Date: 2026-08-31 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '009_user_sort_order'
down_revision: Union[str, None] = '008_device_linking'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sort_order', sa.Integer(), nullable=False, server_default=sa.text('0')))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('sort_order')
