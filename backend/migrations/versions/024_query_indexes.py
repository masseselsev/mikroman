"""Query indexes for the history, rollup and metrics reads.

Adds composite indexes matching the shapes the aggregators actually issue, and
runs ANALYZE so the planner knows the single-column indexes are not the cheapest
route.

Measured on a copy of a live deployment's database - hundreds of thousands of
metric rows, and one device holding tens of thousands of history rows. Absolute
timings are omitted deliberately: they describe one machine, and only the ratios
transfer. Before -> after adding these indexes and running ANALYZE:

    interface metrics, 1 h window    ~40x faster
    interface metrics, 6 h window    ~45x faster
    background rollup recompute      ~2x faster
    EXPLAIN on the 1 h window: a walk of the router's whole index became a
    range scan over the window itself

The single-column indexes were already there; the planner used the wrong one and
then sorted in a temporary B-tree. ``ANALYZE`` alone fixes the choice, but the
composites are what make the range a scan instead of a filter, so both are
applied - and ``backend/app/db/session.py`` keeps running ``PRAGMA optimize`` at
start-up so a database that grows into a new plan gets re-examined.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "024_query_indexes"
down_revision: Union[str, None] = "023_router_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (index name, table, columns) - mirrored in db/session.py's `_ensure_indexes`,
# which is what applies them to an already-deployed database.
INDEXES = (
    ("ix_interface_metrics_router_time", "interface_metrics", ("router_id", "timestamp")),
    ("ix_interface_metrics_name_time", "interface_metrics", ("interface_name", "timestamp")),
    ("ix_system_metrics_router_time", "system_metrics", ("router_id", "timestamp")),
    ("ix_device_history_device_time", "device_history", ("device_id", "created_at")),
    ("ix_device_rollups_device_date", "device_traffic_rollups", ("device_id", "record_date")),
    ("ix_traffic_rollups_user_date", "traffic_rollups", ("user_id", "record_date")),
    ("ix_router_rollups_router_date", "router_traffic_rollups", ("router_id", "record_date")),
)


def upgrade() -> None:
    for name, table, columns in INDEXES:
        op.create_index(name, table, list(columns))
    # Statistics, so the planner stops choosing a router-id index for a query
    # that wants a time range. Idempotent and cheap; also re-run at start-up
    # whenever an index is created (backend/app/db/session.py).
    op.execute("ANALYZE")


def downgrade() -> None:
    for name, table, _columns in reversed(INDEXES):
        op.drop_index(name, table_name=table)
