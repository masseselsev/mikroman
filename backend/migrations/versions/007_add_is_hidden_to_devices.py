"""add_is_hidden_to_devices

Revision ID: 007_device_is_hidden
Revises: 006_device_limits_and_rollups
Create Date: 2026-08-30 15:10:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '007_device_is_hidden'
down_revision: Union[str, None] = '006_device_limits_and_rollups'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('devices', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_hidden', sa.Boolean(), nullable=False, server_default=sa.text('0')))


def downgrade() -> None:
    with op.batch_alter_table('devices', schema=None) as batch_op:
        batch_op.drop_column('is_hidden')
