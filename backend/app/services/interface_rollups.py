"""Rebuild the per-interface and gateway daily rollups from sampled counters.

``interface_metrics`` records every interface's cumulative rx/tx byte counter
about every poll interval and keeps 30 days of it. Walking those samples and
summing ``max(0, curr - prev)`` per interface - bucketed by the router-local
date of the *later* sample of each pair, and time-split when a pair straddles
midnight - yields a daily volume that:

* attributes each byte to the day it actually moved, so a poll that resumes
  after an outage spanning midnight no longer files the whole gap under the
  day it resumed on (the bug that made 08-30's gateway rollup read ~18 GB
  high on the developer's own install);
* is unaffected by a container or router restart: a counter that resets shows
  one negative step, which ``max(0, ...)`` drops, rather than corrupting the
  day.

:func:`recompute_interface_rollups` **replaces** the rows for the days it
covers - it never adds to them - so a day an earlier pass got wrong is
corrected on the next run. It also rewrites :class:`RouterTrafficRollup` for
those days as the sum of the monitored WAN interfaces, which is where the
gateway figure now comes from; the previous live-counter accumulator in
``AnalyticsEngine.record_traffic_snapshot`` is gone.

Cost: the collector calls this every tick for a short trailing window
(:data:`RECOMPUTE_TRAILING_DAYS`) and once for the full retention window
(:data:`BACKFILL_DAYS`) on startup. A trailing-window pass reads a few tens of
thousands of sample rows and rewrites a few dozen rollup rows.
"""
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    InterfaceMetric,
    InterfaceTrafficRollup,
    RouterTrafficRollup,
)
from backend.app.services.rollups import resolve_monitored_interfaces, split_bytes_by_day
from backend.app.services.router_time import get_router_offset

logger = logging.getLogger("mikroman.interface_rollups")

# How far back the per-tick recompute reaches. Two midnights of slack: enough
# to correct a day even after an outage that spanned a full calendar day.
RECOMPUTE_TRAILING_DAYS = 3

# The one-off startup pass. One past the 30-day sample retention, so the oldest
# day that still has any samples is always inside it.
BACKFILL_DAYS = 31

# Interface-name prefixes that mark a tunnel / overlay link rather than a
# physical port or a bridge. RouterOS names WireGuard interfaces ``wireguard1``
# and ZeroTier ones ``zt<hex>`` by default; the rest cover the classic tunnels.
TUNNEL_PREFIXES: Tuple[str, ...] = (
    "wg", "wireguard", "zt", "zerotier", "gre", "eoip", "l2tp", "pptp",
    "sstp", "ovpn", "tun", "tap", "ipip", "6to4", "vxlan", "vpn", "vlan-vpn",
)


def is_tunnel_interface(name: Optional[str]) -> bool:
    """True when ``name`` looks like a VPN / tunnel / overlay interface."""
    n = (name or "").strip().lstrip("<").lower()
    return any(n.startswith(p) for p in TUNNEL_PREFIXES)


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def recompute_interface_rollups(
    session: AsyncSession,
    router_id: int,
    *,
    since_date: Optional[date] = None,
    until_date: Optional[date] = None,
) -> int:
    """Rebuild ``interface_traffic_rollups`` (and the monitored-subset
    ``router_traffic_rollups``) from ``interface_metrics`` for every
    router-local date in ``[since_date, until_date]`` that still has samples.

    ``since_date`` defaults to :data:`BACKFILL_DAYS` ago, ``until_date`` to
    today on the router. Days with no surviving samples are left untouched, so
    calling this can only correct recent history, never erase pruned history.

    Returns the number of days rewritten. Commits its own work.
    """
    offset = await get_router_offset(session) or 0
    now_local = _naive_utc_now() + timedelta(minutes=offset)
    today = now_local.date()

    until_date = until_date or today
    since_date = since_date or (today - timedelta(days=BACKFILL_DAYS))
    if since_date > until_date:
        return 0

    # The sample timestamps are naive UTC; shift the router-local window back
    # into that frame before querying (utc = local - offset).
    lo_utc = datetime.combine(since_date, time()) - timedelta(minutes=offset)
    hi_utc = datetime.combine(until_date + timedelta(days=1), time()) - timedelta(minutes=offset)

    rows = (await session.execute(
        select(
            InterfaceMetric.interface_name,
            InterfaceMetric.timestamp,
            InterfaceMetric.rx_bytes_total,
            InterfaceMetric.tx_bytes_total,
        )
        .where(
            or_(InterfaceMetric.router_id == router_id, InterfaceMetric.router_id.is_(None)),
            InterfaceMetric.timestamp >= lo_utc,
            InterfaceMetric.timestamp < hi_utc,
        )
        .order_by(InterfaceMetric.interface_name, InterfaceMetric.timestamp)
    )).all()
    if not rows:
        return 0

    # (interface, day) -> [bytes_in, bytes_out]
    agg: Dict[Tuple[str, date], List[int]] = {}
    days_seen: set[date] = set()
    prev: Dict[str, Tuple[datetime, int, int]] = {}

    for name, ts, rx, tx in rows:
        local_dt = ts + timedelta(minutes=offset)
        days_seen.add(local_dt.date())
        last = prev.get(name)
        prev[name] = (local_dt, rx, tx)
        if last is None:
            continue
        last_dt, last_rx, last_tx = last
        d_in = max(0, int(rx) - int(last_rx))
        d_out = max(0, int(tx) - int(last_tx))
        if not (d_in or d_out):
            continue
        for day, part_in, part_out in split_bytes_by_day(last_dt, local_dt, d_in, d_out):
            slot = agg.setdefault((name, day), [0, 0])
            slot[0] += part_in
            slot[1] += part_out

    # Only rewrite days we actually have samples for and that sit inside the
    # requested window; a pruned day keeps whatever rollup it already had.
    target_days = sorted(d for d in days_seen if since_date <= d <= until_date)
    if not target_days:
        return 0

    monitored = set(await resolve_monitored_interfaces(session, router_id))

    for day in target_days:
        await session.execute(
            delete(InterfaceTrafficRollup).where(
                and_(
                    or_(
                        InterfaceTrafficRollup.router_id == router_id,
                        InterfaceTrafficRollup.router_id.is_(None),
                    ),
                    InterfaceTrafficRollup.record_date == day,
                )
            )
        )
        await session.execute(
            delete(RouterTrafficRollup).where(
                and_(
                    RouterTrafficRollup.router_id == router_id,
                    RouterTrafficRollup.record_date == day,
                )
            )
        )

        gw_in = gw_out = 0
        for (name, agg_day), (b_in, b_out) in agg.items():
            if agg_day != day:
                continue
            if b_in or b_out:
                session.add(InterfaceTrafficRollup(
                    router_id=router_id,
                    interface_name=name,
                    record_date=day,
                    bytes_in=b_in,
                    bytes_out=b_out,
                ))
            if name in monitored:
                gw_in += b_in
                gw_out += b_out

        session.add(RouterTrafficRollup(
            router_id=router_id,
            record_date=day,
            bytes_in=gw_in,
            bytes_out=gw_out,
        ))

    await session.commit()
    logger.debug(
        "Recomputed interface rollups for router %s: %d day(s) %s..%s",
        router_id, len(target_days), target_days[0], target_days[-1],
    )
    return len(target_days)


async def recompute_recent(session: AsyncSession, router_id: int) -> int:
    """The per-tick call: rebuild only the trailing :data:`RECOMPUTE_TRAILING_DAYS`."""
    offset = await get_router_offset(session) or 0
    today = (_naive_utc_now() + timedelta(minutes=offset)).date()
    return await recompute_interface_rollups(
        session, router_id, since_date=today - timedelta(days=RECOMPUTE_TRAILING_DAYS - 1)
    )
