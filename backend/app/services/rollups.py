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
    Device,
    DeviceTrafficRollup,
    InterfaceMetric,
    RouterSelfTrafficRollup,
    RouterTrafficRollup,
    TrafficRollup,
    User,
)
from backend.app.services.router_time import get_router_offset

# (bytes_in, bytes_out)
Volume = Tuple[int, int]

# A window wide enough to mean "every rollup row there is", for all-time
# totals. SQLite compares DATE values lexically, so these literal bounds sort
# correctly against any real ``record_date``.
ALLTIME_START = date(1970, 1, 1)
ALLTIME_END = date(2999, 12, 31)


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
    filters by router for :class:`RouterTrafficRollup`, :class:`InterfaceTrafficRollup`,
    :class:`RouterSelfTrafficRollup`, and joins the corresponding owner entity
    for :class:`DeviceTrafficRollup` and :class:`TrafficRollup`.
    """
    stmt = (
        select(
            key_column.label("key"),
            func.sum(model.bytes_in).label("total_in"),
            func.sum(model.bytes_out).label("total_out"),
        )
        .where(model.record_date >= start_date)
        .where(model.record_date <= end_date)
    )
    if router_id is not None:
        if hasattr(model, "router_id"):
            stmt = stmt.where(model.router_id == router_id)
        elif model == DeviceTrafficRollup:
            stmt = stmt.join(Device, DeviceTrafficRollup.device_id == Device.id).where(
                (Device.router_id == router_id) | (Device.router_id.is_(None))
            )
        elif model == TrafficRollup:
            stmt = stmt.join(User, TrafficRollup.user_id == User.id).where(
                (User.router_id == router_id) | (User.router_id.is_(None))
            )

    stmt = stmt.group_by(key_column)
    res = await session.execute(stmt)
    return {
        row.key: (int(row.total_in or 0), int(row.total_out or 0))
        for row in res.all()
    }


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
            session, TrafficRollup, TrafficRollup.record_date,
            start_date, end_date, router_id=router_id,
        ),
        "device": await sum_by(
            session, DeviceTrafficRollup, DeviceTrafficRollup.record_date,
            start_date, end_date, router_id=router_id,
        ),
        "self": await sum_by(
            session, RouterSelfTrafficRollup, RouterSelfTrafficRollup.record_date,
            start_date, end_date, router_id=router_id,
        ),
    }

def split_bytes_by_day(
    start_dt: datetime,
    end_dt: datetime,
    bytes_in: int,
    bytes_out: int,
) -> List[Tuple[date, int, int]]:
    """Apportion a volume measured over ``[start_dt, end_dt]`` across the
    calendar days it spans, in proportion to how much of the interval fell on
    each day.

    Both accounting paths accumulate a counter delta between two polls and
    credit the whole of it to one date. When the two polls sit on different
    router-local dates - a poll that resumed after an outage that ran past
    midnight, or simply the first poll after 00:00 - that dumps a full evening
    of traffic onto the wrong day. Splitting by clock time is an approximation
    (traffic is not uniform through the night) but it is a far smaller error
    than mis-filing the entire amount, and for the overwhelmingly common
    same-day case it returns a single unchanged entry.

    ``start_dt``/``end_dt`` are naive router-local datetimes. The returned
    per-day integers always sum back to the inputs - the rounding remainder is
    put on the last day.
    """
    if end_dt <= start_dt:
        return [(start_dt.date(), bytes_in, bytes_out)]
    if start_dt.date() == end_dt.date():
        return [(start_dt.date(), bytes_in, bytes_out)]

    total = (end_dt - start_dt).total_seconds()
    spans: List[Tuple[date, float]] = []
    cursor = start_dt
    while cursor < end_dt:
        next_midnight = datetime.combine(cursor.date() + timedelta(days=1), time())
        chunk_end = min(next_midnight, end_dt)
        spans.append((cursor.date(), (chunk_end - cursor).total_seconds()))
        cursor = chunk_end

    out: List[Tuple[date, int, int]] = []
    acc_in = acc_out = 0
    for idx, (day, seconds) in enumerate(spans):
        if idx == len(spans) - 1:
            day_in = bytes_in - acc_in
            day_out = bytes_out - acc_out
        else:
            frac = seconds / total
            day_in = int(bytes_in * frac)
            day_out = int(bytes_out * frac)
            acc_in += day_in
            acc_out += day_out
        out.append((day, day_in, day_out))
    return out


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
    link.

    Returns ``[]`` when the admin has not chosen a WAN in the selector. The
    monitored set is never guessed: an empty list means "measure nothing for
    this router yet", which is what every caller here does with it. Accounting
    for a router begins only once its WAN is picked.
    """
    key = f"monitored_interfaces_{router_id}" if router_id else "monitored_interfaces_default"
    setting = await session.get(AppSetting, key)
    if setting and setting.value:
        try:
            names = json.loads(setting.value)
            if isinstance(names, list) and names:
                return [str(n) for n in names]
        except (json.JSONDecodeError, TypeError):
            pass
    return []


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
    offset = offset_minutes if offset_minutes is not None else (await get_router_offset(session, router_id) or 0)
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
