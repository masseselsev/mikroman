"""Fold the LAN-to-LAN over-count out of historical rollups.

Per-device accounting matched the ``forward`` chain by address with no WAN
constraint, so traffic between two local subnets was counted at *both* ends
without ever crossing the gateway. Once the mangle rules are scoped to the WAN
interface list this stops happening; this routine repairs the days recorded
before that.

The physical WAN interface counter (``router_traffic_rollups``, sampled from
``interface_metrics``) never double-counts, so it is the reference. For any day
where a router's summed device volume exceeds what its WAN actually carried
(minus the router's own ``input``/``output`` traffic), every device rollup for
that day is scaled down by the same factor so the sum matches the WAN again,
and that day's per-user rollups are rebuilt from the corrected device rollups.

The split between devices is an approximation - the stored rollups do not say
which bytes were local - but it is bounded: volume is only ever removed, never
added, and no figure goes negative. Days where the WAN was never measured, or
where the excess is within a small tolerance, are left untouched.
"""
import logging
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    Device,
    DeviceTrafficRollup,
    Router,
    RouterSelfTrafficRollup,
    RouterTrafficRollup,
    TrafficRollup,
)

logger = logging.getLogger("mikroman.history_reconcile")

# Ignore an excess smaller than this - it is rounding and sampling jitter
# between two independent counters, not real double counting.
MIN_OVERCOUNT_BYTES = 20 * 1024 * 1024
MIN_OVERCOUNT_FRACTION = 0.01


async def _rebuild_user_rollups_for_date(
    session: AsyncSession, day: date, user_ids: set
) -> None:
    """Set each user's rollup for ``day`` to the sum of that user's devices.

    Runs after the device rollups for the day have been scaled, so the two
    levels agree again. Sums across *all* of the user's devices, not just one
    router's, so a profile with devices on two routers stays correct.
    """
    for uid in user_ids:
        if not uid:
            continue
        b_in, b_out = (
            await session.execute(
                select(
                    func.coalesce(func.sum(DeviceTrafficRollup.bytes_in), 0),
                    func.coalesce(func.sum(DeviceTrafficRollup.bytes_out), 0),
                )
                .join(Device, DeviceTrafficRollup.device_id == Device.id)
                .where(Device.user_id == uid, DeviceTrafficRollup.record_date == day)
            )
        ).one()
        b_in, b_out = int(b_in or 0), int(b_out or 0)
        existing = (
            await session.execute(
                select(TrafficRollup).where(
                    TrafficRollup.user_id == uid, TrafficRollup.record_date == day
                )
            )
        ).scalar_one_or_none()
        if existing:
            existing.bytes_in = b_in
            existing.bytes_out = b_out
        elif b_in or b_out:
            session.add(
                TrafficRollup(user_id=uid, record_date=day, bytes_in=b_in, bytes_out=b_out)
            )


async def reconcile_overcounted_history(
    session: AsyncSession,
    *,
    router_id: Optional[int] = None,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Scale down over-counted days so device volume matches the WAN again.

    With ``dry_run`` (the default) nothing is written - the returned summary
    says what *would* change. ``router_id`` limits the pass to one router.
    """
    if router_id is not None:
        routers = [await session.get(Router, router_id)]
        routers = [r for r in routers if r is not None]
    else:
        routers = list((await session.execute(select(Router))).scalars().all())

    summary: Dict[str, Any] = {
        "dry_run": dry_run,
        "days_examined": 0,
        "days_corrected": 0,
        "bytes_removed": 0,
        "per_router": [],
    }

    for r in routers:
        gateway = {
            row.record_date: row.bytes_in + row.bytes_out
            for row in (
                await session.execute(
                    select(RouterTrafficRollup).where(RouterTrafficRollup.router_id == r.id)
                )
            ).scalars()
        }
        self_traffic = {
            row.record_date: row.bytes_in + row.bytes_out
            for row in (
                await session.execute(
                    select(RouterSelfTrafficRollup).where(
                        RouterSelfTrafficRollup.router_id == r.id
                    )
                )
            ).scalars()
        }

        rows = (
            await session.execute(
                select(DeviceTrafficRollup, Device.user_id)
                .join(Device, DeviceTrafficRollup.device_id == Device.id)
                .where((Device.router_id == r.id) | (Device.router_id.is_(None)))
            )
        ).all()
        by_date: Dict[date, List] = defaultdict(list)
        for roll, uid in rows:
            by_date[roll.record_date].append((roll, uid))

        r_removed = 0
        r_days: List[Dict[str, Any]] = []

        for day, items in sorted(by_date.items()):
            summary["days_examined"] += 1
            device_total = sum(roll.bytes_in + roll.bytes_out for roll, _ in items)
            gw_total = gateway.get(day, 0)
            if gw_total <= 0:
                continue  # the WAN was never measured that day - no reference
            target = max(0, gw_total - self_traffic.get(day, 0))
            over = device_total - target
            if (
                target <= 0
                or device_total <= 0
                or over < MIN_OVERCOUNT_BYTES
                or over < gw_total * MIN_OVERCOUNT_FRACTION
            ):
                continue

            factor = target / device_total
            touched_users = set()
            day_removed = 0
            for roll, uid in items:
                new_in = int(round(roll.bytes_in * factor))
                new_out = int(round(roll.bytes_out * factor))
                day_removed += (roll.bytes_in - new_in) + (roll.bytes_out - new_out)
                if not dry_run:
                    roll.bytes_in = new_in
                    roll.bytes_out = new_out
                touched_users.add(uid)

            if not dry_run:
                await _rebuild_user_rollups_for_date(session, day, touched_users)

            r_removed += day_removed
            summary["days_corrected"] += 1
            r_days.append(
                {
                    "date": day.isoformat(),
                    "device_total": device_total,
                    "wan_target": target,
                    "bytes_removed": day_removed,
                    "factor": round(factor, 4),
                }
            )

        summary["bytes_removed"] += r_removed
        summary["per_router"].append(
            {
                "router_id": r.id,
                "name": r.name,
                "days_corrected": len(r_days),
                "bytes_removed": r_removed,
                "days": r_days,
            }
        )

    if not dry_run and summary["days_corrected"]:
        await session.commit()
        logger.info(
            "History reconcile applied: %d day(s), %d bytes removed",
            summary["days_corrected"],
            summary["bytes_removed"],
        )

    return summary
