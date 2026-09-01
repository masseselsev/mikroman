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
import json
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    AppSetting,
    DeviceTrafficRollup,
    InterfaceMetric,
    RouterSelfTrafficRollup,
    RouterTrafficRollup,
    TrafficRollup,
)
from backend.app.services.router_time import get_router_offset

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
    """Every level's per-day volume in one call.

    Keyed ``router`` (the WAN interface total), ``user``, ``device``, and
    ``self`` (what the router moved on its own behalf). All four are needed per
    day rather than only in total, because coverage is judged over a sub-window
    of the range and a total cannot be split back into days.
    """
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
        "self": await sum_by(
            session, RouterSelfTrafficRollup, RouterSelfTrafficRollup.record_date,
            start_date, end_date, router_id=router_id,
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


async def resolve_monitored_interfaces(
    session: AsyncSession, router_id: Optional[int]
) -> List[str]:
    """WAN interface names for a router, from the same setting the gateway
    rollups are measured on, so a slice and the daily total describe the same
    link. Defaults to ``["ether1"]`` when nothing is configured."""
    key = f"monitored_interfaces_{router_id}" if router_id else "monitored_interfaces_default"
    setting = await session.get(AppSetting, key)
    if setting and setting.value:
        try:
            names = json.loads(setting.value)
            if isinstance(names, list) and names:
                return [str(n) for n in names]
        except (json.JSONDecodeError, TypeError):
            pass
    return ["ether1"]


async def slice_of_day_bytes(
    session: AsyncSession,
    router_id: Optional[int],
    day: date,
    from_time: Optional[time],
    to_time: Optional[time],
    interfaces: List[str],
    *,
    offset_minutes: Optional[int] = None,
) -> Optional[Tuple[int, int]]:
    """Bytes transferred on ``day`` between two clock times, from the sampled
    WAN interface counters.

    ``day`` and ``from_time`` / ``to_time`` are interpreted **router-local** -
    they come straight from :func:`get_billing_cycle_bounds`. The samples in
    ``interface_metrics.timestamp`` are stored as naive **UTC**, so the window
    is converted into that UTC frame (``utc = router_local - offset``) before it
    is queried; on any router not at UTC+0 comparing the two frames directly
    hits the wrong rows. ``offset_minutes`` lets a caller that already knows the
    router offset pass it in rather than have it re-read here; ``None`` looks it
    up.

    ``interface_metrics`` records each interface's *cumulative* rx/tx byte
    counter about every 1.5 s. Walking every sample in the window and summing
    ``max(0, curr - prev)`` per interface means an intermediate router reboot
    shows up as one negative step that is dropped, rather than corrupting the
    whole slice. Bytes between a window edge and the nearest sample are
    unattributed - at ~1.5 s spacing that is a couple of seconds per edge, far
    below the rounding in every GB figure.

    Returns ``None`` when no interface has a sample in the window (the samples
    have been pruned - retention is 30 days), so the caller can fall back to the
    whole-day rollup.
    """
    if not interfaces:
        return None
    lo = datetime.combine(day, from_time or time(0, 0, 0))
    hi = datetime.combine(day, to_time or time(23, 59, 59, 999999))

    # Shift the router-local window back into the UTC frame the samples carry.
    offset = offset_minutes if offset_minutes is not None else (await get_router_offset(session) or 0)
    lo -= timedelta(minutes=offset)
    hi -= timedelta(minutes=offset)

    stmt = (
        select(InterfaceMetric)
        .where(InterfaceMetric.interface_name.in_(interfaces))
        .where(InterfaceMetric.timestamp >= lo)
        .where(InterfaceMetric.timestamp <= hi)
        .order_by(InterfaceMetric.interface_name, InterfaceMetric.timestamp)
    )
    if router_id is not None:
        # Single-router installs leave interface_metrics.router_id NULL, so those
        # rows must match too (the same IS NULL idiom as _add_rollup).
        stmt = stmt.where(
            (InterfaceMetric.router_id == router_id) | (InterfaceMetric.router_id.is_(None))
        )

    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        return None

    total_in = total_out = 0
    prev: Dict[str, Tuple[int, int]] = {}
    for row in rows:
        last = prev.get(row.interface_name)
        if last is not None:
            total_in += max(0, row.rx_bytes_total - last[0])
            total_out += max(0, row.tx_bytes_total - last[1])
        prev[row.interface_name] = (row.rx_bytes_total, row.tx_bytes_total)
    return (total_in, total_out)
