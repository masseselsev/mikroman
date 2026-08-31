"""clear quarantine speed limits stranded on assigned devices

Device discovery used to copy the configured quarantine bandwidth
(``unassigned_device_speed_limit``, "5M/5M" by default) onto
``devices.speed_limit``. Assigning the device to a user only ever set
``user_id``; nothing cleared the copy.

The queue builder reads ``speed_limit`` as "an explicit override the operator
chose for this device", so it kept building a child Simple Queue at the
quarantine rate underneath the owner's parent queue. The visible result was a
household whose users were all "unlimited" while every one of their devices sat
in a 5M/5M child queue - the owner's limit never applied, and the queue tree
looked as if someone had throttled the family at random.

Discovery now stores "default" instead, and TrafficController.reconcile_device_limits
guards against the state returning. This migration cleans up the rows written
before that fix.

Scope of the update, and its one assumption: only devices that *have an owner*
and whose limit is *exactly* the currently configured quarantine value are
reset. A device the operator deliberately limited to precisely that value is
indistinguishable from one carrying the stamp, so it is cleared too. That
trade is intentional - the cost of a false positive is that the operator sets
the limit again and it sticks, while the cost of a false negative is a user
silently capped at 5 Mbps with no indication why.

Unassigned devices are untouched: for them the value is still correct, and it
is re-resolved from settings on every queue build regardless.

Revision ID: 010_clear_quarantine_limits
Revises: 009_user_sort_order
Create Date: 2026-08-31 21:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '010_clear_quarantine_limits'
down_revision: Union[str, None] = '009_user_sort_order'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_QUARANTINE_LIMIT = "5M/5M"


def upgrade() -> None:
    connection = op.get_bind()

    # The quarantine rate is operator-configurable, so read the value actually
    # in force rather than assuming the default. An installation that raised it
    # to 20M/20M stamped 20M/20M onto its devices.
    configured = connection.execute(
        sa.text("SELECT value FROM app_settings WHERE key = 'unassigned_device_speed_limit'")
    ).scalar()
    quarantine = configured or DEFAULT_QUARANTINE_LIMIT

    connection.execute(
        sa.text(
            "UPDATE devices SET speed_limit = 'default' "
            "WHERE user_id IS NOT NULL AND speed_limit = :quarantine"
        ),
        {"quarantine": quarantine},
    )


def downgrade() -> None:
    # Not reversible: which rows held the value because discovery stamped it and
    # which because an operator chose it was exactly the information this
    # migration collapsed. Re-stamping every assigned device would re-throttle
    # devices that were never limited in the first place, so the downgrade
    # deliberately does nothing rather than inventing that distinction.
    pass
