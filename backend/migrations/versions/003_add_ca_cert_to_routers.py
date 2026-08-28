"""add_ca_cert_to_routers

Revision ID: 003_ca_cert
Revises: 002_multi_router
Create Date: 2026-08-29 01:45:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '003_ca_cert'
down_revision: Union[str, None] = '002_multi_router'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('routers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ca_cert', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('routers', schema=None) as batch_op:
        batch_op.drop_column('ca_cert')
