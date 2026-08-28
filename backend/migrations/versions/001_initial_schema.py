"""initial_schema

Revision ID: 001_initial
Revises:
Create Date: 2026-08-28 22:45:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('avatar_icon', sa.String(length=50), nullable=False, server_default='user'),
        sa.Column('speed_limit', sa.String(length=50), nullable=False, server_default='unlimited'),
        sa.Column('is_paused', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_name'), 'users', ['name'], unique=True)

    op.create_table(
        'devices',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('mac_address', sa.String(length=17), nullable=False),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('hostname', sa.String(length=255), nullable=True),
        sa.Column('custom_name', sa.String(length=255), nullable=True),
        sa.Column('vendor', sa.String(length=255), nullable=True),
        sa.Column('last_interface', sa.String(length=100), nullable=True),
        sa.Column('last_wifi_signal', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('last_seen', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_devices_ip_address'), 'devices', ['ip_address'], unique=False)
    op.create_index(op.f('ix_devices_mac_address'), 'devices', ['mac_address'], unique=True)
    op.create_index(op.f('ix_devices_user_id'), 'devices', ['user_id'], unique=False)

    op.create_table(
        'traffic_rollups',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('record_date', sa.Date(), nullable=False),
        sa.Column('bytes_in', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('bytes_out', sa.BigInteger(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_traffic_rollups_record_date'), 'traffic_rollups', ['record_date'], unique=False)
    op.create_index(op.f('ix_traffic_rollups_user_id'), 'traffic_rollups', ['user_id'], unique=False)

    op.create_table(
        'app_settings',
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('value', sa.String(length=1000), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('key')
    )

    op.create_table(
        'alert_logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('alert_type', sa.String(length=50), nullable=False),
        sa.Column('message', sa.String(length=1000), nullable=False),
        sa.Column('metadata_payload', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_alert_logs_alert_type'), 'alert_logs', ['alert_type'], unique=False)


def downgrade() -> None:
    op.drop_table('alert_logs')
    op.drop_table('app_settings')
    op.drop_table('traffic_rollups')
    op.drop_table('devices')
    op.drop_table('users')
