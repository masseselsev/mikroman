"""One way to sum a daily traffic rollup table over a date window.

The three rollup tables - per router, per user, per device - are the same shape:
an owner column, a ``record_date``, and ``bytes_in`` / ``bytes_out``. The
analytics engine needed six aggregations over them (each table totalled by owner
and again by day) and carried six near-identical hand-written queries to do it.
They drifted: the date window was inclusive in some and the router filter was
applied in two of the three router queries, which is the kind of difference that
produces a wrong number rather than an error.

``sum_by`` is the single query. Everything else here is a thin, named wrapper so
call sites read as what they mean rather than as SQL.
"""
from datetime import date
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    DeviceTrafficRollup,
    RouterTrafficRollup,
    TrafficRollup,
)

# (bytes_in, bytes_out)
Volume = Tuple[int, int]


async def sum_by(
    session: AsyncSession,
    model: Any,
    key_column: Any,
    start_date: date,
    end_date: date,
    *,
    router_id: Optional[int] = None,
) -> Dict[Any, Volume]:
    """``{key: (bytes_in, bytes_out)}`` over an inclusive date window.

    ``key_column`` is whatever the caller wants to group by - the owner column
    for a totals-per-owner view, ``record_date`` for a timeline. ``router_id``
    is only meaningful for :class:`RouterTrafficRollup`, which is the one table
    that records which router a row came from; it is ignored elsewhere because
    a user or a device belongs to a household, not to an interface.
    """
    stmt = (
        select(
            key_column.label("key"),
            func.sum(model.bytes_in).label("total_in"),
            func.sum(model.bytes_out).label("total_out"),
        )
        .where(model.record_date >= start_date)
        .where(model.record_date <= end_date)
        .group_by(key_column)
    )
    if router_id is not None and hasattr(model, "router_id"):
        stmt = stmt.where(model.router_id == router_id)

    result = await session.execute(stmt)
    return {row.key: (int(row.total_in or 0), int(row.total_out or 0)) for row in result}


async def total(
    session: AsyncSession,
    model: Any,
    start_date: date,
    end_date: date,
    *,
    router_id: Optional[int] = None,
) -> Volume:
    """A single ``(bytes_in, bytes_out)`` for the whole window.

    Summed from the per-day breakdown rather than with a second query, so a
    total can never disagree with the timeline it sits above.
    """
    per_day = await sum_by(
        session, model, model.record_date, start_date, end_date, router_id=router_id
    )
    return (
        sum(v[0] for v in per_day.values()),
        sum(v[1] for v in per_day.values()),
    )


async def daily_totals(
    session: AsyncSession,
    start_date: date,
    end_date: date,
    *,
    router_id: Optional[int] = None,
) -> Dict[str, Dict[date, Volume]]:
    """Every level's per-day volume in one call, keyed ``router``/``user``/``device``."""
    return {
        "router": await sum_by(
            session, RouterTrafficRollup, RouterTrafficRollup.record_date,
            start_date, end_date, router_id=router_id,
        ),
        "user": await sum_by(
            session, TrafficRollup, TrafficRollup.record_date, start_date, end_date
        ),
        "device": await sum_by(
            session, DeviceTrafficRollup, DeviceTrafficRollup.record_date,
            start_date, end_date,
        ),
    }


def sum_window(per_day: Dict[date, Volume], *, after: Optional[date] = None) -> int:
    """Combined bytes across a per-day map, optionally only after a given day.

    ``after`` is exclusive, which is what judging accounting coverage needs: the
    day per-device counters were switched on is a partial day and belongs with
    the period that predates them, not with the period they measure.
    """
    return sum(
        day_in + day_out
        for day, (day_in, day_out) in per_day.items()
        if after is None or day > after
    )
