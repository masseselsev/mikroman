"""add_device_history
Revision ID: 004_device_history
Revises: 003_ca_cert
Create Date: 2026-08-29 02:24:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '004_device_history'
down_revision: Union[str, None] = '003_ca_cert'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'device_history',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('device_id', sa.Integer(), nullable=False),
        sa.Column('mac_address', sa.String(length=17), nullable=False),
        sa.Column('hostname', sa.String(length=255), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('details', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_device_history_device_id'), 'device_history', ['device_id'], unique=False)
    op.create_index(op.f('ix_device_history_mac_address'), 'device_history', ['mac_address'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_device_history_mac_address'), table_name='device_history')
    op.drop_index(op.f('ix_device_history_device_id'), table_name='device_history')
    op.drop_table('device_history')
