"""record device co-presence to stop over-eager MAC-rotation merges

The consolidation pass collapses several rows that share a normalised hostname
and one owner into a single device, on the theory that they are one phone that
rotated its private MAC. That theory is wrong when the hostname genuinely names
more than one device - three people each with a bare "iPhone", or one person
who owns two of the same model.

The tell is co-presence: a rotating phone stops answering on its old address the
instant it uses a new one, because it has one radio. Two addresses seen active
in the *same* discovery sweep are therefore two physical devices. This table
remembers every such pair so the merge is refused from then on.

Keyed by address, not device id, so a pair survives its device being absorbed by
some other merge. Stored with ``mac_a <= mac_b`` so a pair has exactly one row.

Revision ID: 011_device_coexistence
Revises: 010_clear_quarantine_limits
Create Date: 2026-08-31 22:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '011_device_coexistence'
down_revision: Union[str, None] = '010_clear_quarantine_limits'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "device_coexistence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mac_a", sa.String(length=17), nullable=False),
        sa.Column("mac_b", sa.String(length=17), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("first_seen_together", sa.DateTime(), nullable=False),
        sa.Column("last_seen_together", sa.DateTime(), nullable=False),
        sa.Column("observations", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mac_a", "mac_b", name="uq_device_coexistence_pair"),
    )
    op.create_index("ix_device_coexistence_mac_a", "device_coexistence", ["mac_a"])
    op.create_index("ix_device_coexistence_mac_b", "device_coexistence", ["mac_b"])


def downgrade() -> None:
    op.drop_index("ix_device_coexistence_mac_b", table_name="device_coexistence")
    op.drop_index("ix_device_coexistence_mac_a", table_name="device_coexistence")
    op.drop_table("device_coexistence")
