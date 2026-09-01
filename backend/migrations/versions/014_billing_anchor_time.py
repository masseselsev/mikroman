"""billing cycle reset time (hours + minutes)

The reset time of day is stored as two ``app_settings`` rows
(``billing_cycle_anchor_hour`` / ``billing_cycle_anchor_minute``), read with a
midnight default. There is no schema change; this revision only keeps the
Alembic head chain linear and documents when the keys were introduced.

Revision ID: 014_billing_anchor_time
Revises: 013_containers_router_traffic
Create Date: 2026-09-01 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op  # noqa: F401  (kept for symmetry with the other revisions)

revision: str = "014_billing_anchor_time"
down_revision: Union[str, None] = "013_containers_router_traffic"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
