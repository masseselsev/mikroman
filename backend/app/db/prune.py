"""Retention deletes that leave the database open for everybody else.

SQLite in WAL mode allows exactly **one** writer. MikroMan's own
``busy_timeout`` is 5 seconds, so any single write transaction that outlives
that turns every other worker into an ``OperationalError: database is locked``.

Measured on the router-hosted container, exactly that happened: the retention
prune ran on every 10-second telemetry tick, scanning a 116 MB database of
~700k metric rows, and the log scraper inserted ~300 rows and deleted ~300 rows
every minute. The statements that failed were the ones innocent bystanders ran -
``DELETE FROM system_metrics WHERE timestamp < ?`` and the interface-rollup
delete - while the lock was held elsewhere.

``delete_matching_batches`` fixes the shape of the work rather than the
scheduling: each transaction removes at most ``batch`` rows and commits, so the
write lock is taken and released repeatedly and a competing writer only ever
waits for one batch. ``LIMIT`` inside the subquery is what bounds the batch -
a plain ``DELETE ... WHERE timestamp < cutoff`` has no bound at all.
"""
from __future__ import annotations

import logging

from sqlalchemy import Table, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("mikroman.db.prune")


async def delete_matching_batches(
    session: AsyncSession,
    table: Table,
    where,
    order_by,
    batch: int = 500,
    max_batches: int = 20,
) -> int:
    """Delete rows matching ``where`` in committed batches. Newest kept first.

    Returns the number of deleted rows. Stops at ``max_batches`` rather than
    looping until empty, so a first run after a long outage (or after a bug that
    let a table grow unbounded) cannot monopolise the writer: whatever is left is
    picked up on the next pass.

    ``order_by`` decides which rows go first within a batch - pass the oldest
    timestamp to prune oldest-first and keep the ordering stable, which also
    makes each batch a contiguous index range instead of a scatter.
    """
    total = 0
    for _ in range(max_batches):
        ids = (
            await session.execute(
                select(table.columns["id"])
                .where(where)
                .order_by(order_by)
                .limit(batch)
            )
        ).scalars().all()
        if not ids:
            break
        result = await session.execute(delete(table).where(table.columns["id"].in_(ids)))
        total += result.rowcount or 0
        await session.commit()
        if len(ids) < batch:
            break
    if total:
        logger.debug("Pruned %d row(s) from %s", total, table.name)
    return total


async def delete_older_than(
    session: AsyncSession,
    table: Table,
    cutoff,
    *,
    column: str = "timestamp",
    batch: int = 500,
    max_batches: int = 20,
) -> int:
    """Drop rows whose time column predates ``cutoff``, oldest first, in batches.

    `column` exists because the tables are not unanimous: the metric samples use
    ``timestamp``, the device event log uses ``created_at``, and reaching for a
    name the table does not have fails with a `KeyError` inside a background
    prune where nobody is looking.
    """
    time_column = table.columns[column]
    return await delete_matching_batches(
        session,
        table,
        time_column < cutoff,
        time_column.asc(),
        batch=batch,
        max_batches=max_batches,
    )
