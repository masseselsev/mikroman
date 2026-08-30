"""add_system_and_interface_metrics

Revision ID: 005_metrics
Revises: 004_device_history
Create Date: 2026-08-29 02:35:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '005_metrics'
down_revision: Union[str, None] = '004_device_history'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # System Metrics
    op.create_table(
        'system_metrics',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('router_id', sa.Integer(), nullable=True),
        sa.Column('cpu_load', sa.Float(), nullable=False),
        sa.Column('memory_used_bytes', sa.BigInteger(), nullable=False),
        sa.Column('memory_total_bytes', sa.BigInteger(), nullable=False),
        sa.Column('memory_usage_pct', sa.Float(), nullable=False),
        sa.Column('temperature', sa.Float(), nullable=True),
        sa.Column('voltage', sa.Float(), nullable=True),
        sa.Column('timestamp', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['router_id'], ['routers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_system_metrics_router_id'), 'system_metrics', ['router_id'], unique=False)
    op.create_index(op.f('ix_system_metrics_timestamp'), 'system_metrics', ['timestamp'], unique=False)

    # Interface Metrics
    op.create_table(
        'interface_metrics',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('router_id', sa.Integer(), nullable=True),
        sa.Column('interface_name', sa.String(length=100), nullable=False),
        sa.Column('rx_rate_bps', sa.Float(), nullable=False),
        sa.Column('tx_rate_bps', sa.Float(), nullable=False),
        sa.Column('rx_bytes_total', sa.BigInteger(), nullable=False),
        sa.Column('tx_bytes_total', sa.BigInteger(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['router_id'], ['routers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_interface_metrics_router_id'), 'interface_metrics', ['router_id'], unique=False)
    op.create_index(op.f('ix_interface_metrics_interface_name'), 'interface_metrics', ['interface_name'], unique=False)
    op.create_index(op.f('ix_interface_metrics_timestamp'), 'interface_metrics', ['timestamp'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_interface_metrics_timestamp'), table_name='interface_metrics')
    op.drop_index(op.f('ix_interface_metrics_interface_name'), table_name='interface_metrics')
    op.drop_index(op.f('ix_interface_metrics_router_id'), table_name='interface_metrics')
    op.drop_table('interface_metrics')

    op.drop_index(op.f('ix_system_metrics_timestamp'), table_name='system_metrics')
    op.drop_index(op.f('ix_system_metrics_router_id'), table_name='system_metrics')
    op.drop_table('system_metrics')
