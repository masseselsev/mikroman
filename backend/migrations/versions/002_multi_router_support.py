"""multi_router_support

Revision ID: 002_multi_router
Revises: 001_initial
Create Date: 2026-08-28 23:15:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '002_multi_router'
down_revision: Union[str, None] = '001_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create routers table
    op.create_table(
        'routers',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('host', sa.String(length=255), nullable=False),
        sa.Column('port', sa.Integer(), nullable=False, server_default='443'),
        sa.Column('use_ssl', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('ssl_verify', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('username', sa.String(length=100), nullable=False, server_default='admin'),
        sa.Column('password', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_routers_name'), 'routers', ['name'], unique=True)

    # 2. Add router_id to devices and alert_logs
    with op.batch_alter_table('devices', schema=None) as batch_op:
        batch_op.add_column(sa.Column('router_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_devices_router_id', 'routers', ['router_id'], ['id'], ondelete='SET NULL')
        batch_op.create_index(batch_op.f('ix_devices_router_id'), ['router_id'], unique=False)

    with op.batch_alter_table('alert_logs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('router_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_alert_logs_router_id', 'routers', ['router_id'], ['id'], ondelete='SET NULL')
        batch_op.create_index(batch_op.f('ix_alert_logs_router_id'), ['router_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('alert_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_alert_logs_router_id'))
        batch_op.drop_constraint('fk_alert_logs_router_id', type_='foreignkey')
        batch_op.drop_column('router_id')

    with op.batch_alter_table('devices', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_devices_router_id'))
        batch_op.drop_constraint('fk_devices_router_id', type_='foreignkey')
        batch_op.drop_column('router_id')

    op.drop_index(op.f('ix_routers_name'), table_name='routers')
    op.drop_table('routers')
