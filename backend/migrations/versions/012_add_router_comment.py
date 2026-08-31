"""add a free-text comment to each router

A place for the operator to keep notes about a specific router - its physical
location, the ISP account it is on, quirks of its config, a maintenance window.
Shown in the header for the selected router, collapsed to the first few lines and
expandable for editing.

Revision ID: 012_router_comment
Revises: 011_device_coexistence
Create Date: 2026-09-01 09:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '012_router_comment'
down_revision: Union[str, None] = '011_device_coexistence'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("routers", sa.Column("comment", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("routers", "comment")
