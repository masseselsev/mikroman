"""add_device_limits_and_traffic_rollups

Revision ID: 006_device_limits_and_rollups
Revises: 005_metrics
Create Date: 2026-08-29 17:50:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '006_device_limits_and_rollups'
down_revision: Union[str, None] = '005_metrics'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add columns to devices
    with op.batch_alter_table('devices', schema=None) as batch_op:
        batch_op.add_column(sa.Column('speed_limit', sa.String(length=50), nullable=False, server_default='default'))
        batch_op.add_column(sa.Column('is_paused', sa.Boolean(), nullable=False, server_default=sa.text('0')))
        batch_op.add_column(sa.Column('priority', sa.Integer(), nullable=False, server_default='1'))

    # 2. Create device_traffic_rollups table
    op.create_table(
        'device_traffic_rollups',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('device_id', sa.Integer(), nullable=False),
        sa.Column('record_date', sa.Date(), nullable=False),
        sa.Column('bytes_in', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('bytes_out', sa.BigInteger(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_device_traffic_rollups_device_id'), 'device_traffic_rollups', ['device_id'], unique=False)
    op.create_index(op.f('ix_device_traffic_rollups_record_date'), 'device_traffic_rollups', ['record_date'], unique=False)

    # 3. Create router_traffic_rollups table
    op.create_table(
        'router_traffic_rollups',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('router_id', sa.Integer(), nullable=False),
        sa.Column('record_date', sa.Date(), nullable=False),
        sa.Column('bytes_in', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('bytes_out', sa.BigInteger(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['router_id'], ['routers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_router_traffic_rollups_router_id'), 'router_traffic_rollups', ['router_id'], unique=False)
    op.create_index(op.f('ix_router_traffic_rollups_record_date'), 'router_traffic_rollups', ['record_date'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_router_traffic_rollups_record_date'), table_name='router_traffic_rollups')
    op.drop_index(op.f('ix_router_traffic_rollups_router_id'), table_name='router_traffic_rollups')
    op.drop_table('router_traffic_rollups')

    op.drop_index(op.f('ix_device_traffic_rollups_record_date'), table_name='device_traffic_rollups')
    op.drop_index(op.f('ix_device_traffic_rollups_device_id'), table_name='device_traffic_rollups')
    op.drop_table('device_traffic_rollups')

    with op.batch_alter_table('devices', schema=None) as batch_op:
        batch_op.drop_column('priority')
        batch_op.drop_column('is_paused')
        batch_op.drop_column('speed_limit')
