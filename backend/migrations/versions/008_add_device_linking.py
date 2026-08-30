"""add_device_linking

Links several network interfaces of the same physical machine into one logical
device. A laptop that connects over Ethernet at a desk and over Wi-Fi elsewhere
has a different MAC address per adapter, so it was discovered and displayed as
two unrelated devices with their traffic split between them.

A linked device points at the primary device it belongs to; the primary itself
has NULL. The group is therefore "the primary plus everything pointing at it".

Revision ID: 008_device_linking
Revises: 007_device_is_hidden
Create Date: 2026-08-31 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '008_device_linking'
down_revision: Union[str, None] = '007_device_is_hidden'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('devices', schema=None) as batch_op:
        batch_op.add_column(sa.Column('linked_to_device_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('connection_kind', sa.String(length=20), nullable=True))
        # Radio links of the current wireless association; a WiFi 7 multi-link
        # client is bonded over several radios, each with its own signal.
        batch_op.add_column(sa.Column('wifi_links', sa.JSON(), nullable=True))
        batch_op.create_index('ix_devices_linked_to_device_id', ['linked_to_device_id'], unique=False)
        batch_op.create_foreign_key(
            'fk_devices_linked_to_device_id',
            'devices',
            ['linked_to_device_id'],
            ['id'],
            ondelete='SET NULL',
        )


def downgrade() -> None:
    with op.batch_alter_table('devices', schema=None) as batch_op:
        batch_op.drop_constraint('fk_devices_linked_to_device_id', type_='foreignkey')
        batch_op.drop_index('ix_devices_linked_to_device_id')
        batch_op.drop_column('wifi_links')
        batch_op.drop_column('connection_kind')
        batch_op.drop_column('linked_to_device_id')
