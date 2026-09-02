import calendar
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    AppSetting,
    Device,
    DeviceTrafficRollup,
    InterfaceTrafficRollup,
    TrafficRollup,
    User,
    UserTrafficBucket,
)
from backend.app.schemas.analytics import (
    AccountingHealth,
    DailyTrafficPoint,
    DeviceTrafficSummary,
    EntityTrafficHistoryResponse,
    GatewayTrafficSummary,
    InterfaceTrafficSummary,
    RouterSelfTrafficSummary,
    TrafficAnalyticsResponse,
    UnassignedTrafficSummary,
    UserTrafficSummary,
)
from backend.app.services import rollups
from backend.app.services.interface_rollups import is_tunnel_interface, recompute_recent
from backend.app.services.rollups import ALLTIME_END as _ALLTIME_END
from backend.app.services.rollups import ALLTIME_START as _ALLTIME_START
from backend.app.services.router_time import router_local_now

logger = logging.getLogger("mikroman.analytics_engine")


def inclusive_end_date(end_dt: datetime) -> date:
    """Last calendar date a half-open cycle bound touches.

    Shared with the analytics endpoint, hence no leading underscore: turning the
    exclusive ``end_dt`` of ``get_billing_cycle_bounds`` into an inclusive
    ``date`` is done in several places and must be done the same way each time.
    """
    return (end_dt - timedelta(microseconds=1)).date()


def get_billing_cycle_bounds(
    anchor_day: int,
    anchor_hour: int,
    anchor_minute: int,
    ref_dt: datetime,
    previous: bool = False,
) -> Tuple[datetime, datetime]:
    """Router-local start (inclusive) and end (exclusive) of an ISP billing cycle.

    ``end_dt`` is the next cycle's reset instant, so the current cycle is the
    half-open interval ``[start_dt, end_dt)``. Unlike the date-only
    :func:`get_billing_cycle_dates`, this is time-aware: on the anchor day
    itself the cycle you are in depends on whether ``ref_dt`` has passed the
    reset time yet.
    """
    day = max(1, min(anchor_day, 31))
    hh = max(0, min(anchor_hour, 23))
    mm = max(0, min(anchor_minute, 59))

    def reset_on(year: int, month: int) -> datetime:
        last = calendar.monthrange(year, month)[1]
        return datetime(year, month, min(day, last), hh, mm)

    this_month = reset_on(ref_dt.year, ref_dt.month)
    if ref_dt >= this_month:
        start = this_month
    elif ref_dt.month == 1:
        start = reset_on(ref_dt.year - 1, 12)
    else:
        start = reset_on(ref_dt.year, ref_dt.month - 1)

    if start.month == 12:
        end = reset_on(start.year + 1, 1)
    else:
        end = reset_on(start.year, start.month + 1)

    if previous:
        prev_end = start
        if start.month == 1:
            prev_start = reset_on(start.year - 1, 12)
        else:
            prev_start = reset_on(start.year, start.month - 1)
        return (prev_start, prev_end)

    return (start, end)


def get_billing_cycle_dates(
    anchor_day: int, reference_date: Optional[date] = None, previous: bool = False
) -> Tuple[date, date]:
    """Inclusive first and last *calendar dates* an ISP billing cycle touches.

    A thin date-granular view of :func:`get_billing_cycle_bounds` at midnight.
    Retained purely as a convenience for the handful of callers (and tests) that
    only ever think in whole days. Everything that has to respect the reset time
    - the quota's "used" figure, its countdown, the range presets - now calls
    ``get_billing_cycle_bounds`` directly with the real anchor time.
    """
    ref = reference_date or date.today()
    start_dt, end_dt = get_billing_cycle_bounds(
        anchor_day, 0, 0, datetime.combine(ref, datetime.min.time()), previous
    )
    return (start_dt.date(), inclusive_end_date(end_dt))


def resolve_date_range(
    preset: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    anchor_day: int = 1,
    anchor_hour: int = 0,
    anchor_minute: int = 0,
    today: Optional[date] = None,
    now_dt: Optional[datetime] = None,
) -> Tuple[date, date, str]:
    """Resolve a date range preset or explicit custom dates into concrete dates.

    ``today`` is supplied by the caller as the router's date; the container is
    usually on UTC and would otherwise resolve "today" to a different day than
    the router's own clock shows.
    """
    today = today or date.today()
    preset = (preset or "7d").lower().strip()

    if preset in ("today", "day", "1d"):
        return (today, today, "today")
    elif preset == "yesterday":
        yest = today - timedelta(days=1)
        return (yest, yest, "yesterday")
    elif preset in ("7d", "week", "1w"):
        return (today - timedelta(days=6), today, "7d")
    elif preset in ("30d", "month", "1m"):
        return (today - timedelta(days=29), today, "30d")
    elif preset in ("1y", "year", "365d"):
        return (today - timedelta(days=364), today, "1y")
    elif preset in ("all_time", "all", "alltime"):
        return (date(2000, 1, 1), today, "all_time")
    elif preset == "billing_current":
        ref = now_dt or datetime.combine(today, datetime.min.time())
        s_dt, e_dt = get_billing_cycle_bounds(anchor_day, anchor_hour, anchor_minute, ref, previous=False)
        e_date = inclusive_end_date(e_dt)
        # Cap current cycle view to today for live measurement
        return (s_dt.date(), min(e_date, today), "billing_current")
    elif preset == "billing_previous":
        ref = now_dt or datetime.combine(today, datetime.min.time())
        s_dt, e_dt = get_billing_cycle_bounds(anchor_day, anchor_hour, anchor_minute, ref, previous=True)
        return (s_dt.date(), inclusive_end_date(e_dt), "billing_previous")
    elif preset == "custom" and start_date and end_date:
        return (min(start_date, end_date), max(start_date, end_date), "custom")
    elif start_date and end_date:
        return (min(start_date, end_date), max(start_date, end_date), "custom")
    else:
        # Default fallback: 7 days
        return (today - timedelta(days=6), today, "7d")


class AnalyticsEngine:
    """Historical traffic accounting and aggregation engine."""

    @staticmethod
    async def get_billing_anchor_day(session: AsyncSession, router_id: Optional[int] = None) -> int:
        """Fetch the configured ISP billing cycle anchor day from app settings."""
        key = f"billing_cycle_anchor_day_{router_id}" if router_id is not None else "billing_cycle_anchor_day"
        setting = await session.get(AppSetting, key)
        if not setting and router_id in (None, 1):
            setting = await session.get(AppSetting, "billing_cycle_anchor_day")
        if setting and setting.value:
            try:
                return max(1, min(int(setting.value), 31))
            except ValueError:
                return 1
        return 1

    @staticmethod
    async def set_billing_anchor_day(session: AsyncSession, anchor_day: int, router_id: Optional[int] = None) -> int:
        """Save the ISP billing cycle anchor day to app settings."""
        day = max(1, min(anchor_day, 31))
        key = f"billing_cycle_anchor_day_{router_id}" if router_id is not None else "billing_cycle_anchor_day"
        setting = await session.get(AppSetting, key)
        if setting:
            setting.value = str(day)
        else:
            setting = AppSetting(key=key, value=str(day), description="ISP billing cycle monthly anchor day (1-31)")
            session.add(setting)
        await session.commit()
        return day

    @staticmethod
    async def get_billing_anchor_time(session: AsyncSession, router_id: Optional[int] = None) -> Tuple[int, int]:
        """The configured reset time of day as ``(hour, minute)``.

        Defaults to midnight, which reproduces the pre-existing date-only
        behaviour exactly, so an install that never set a time is unaffected.
        A stored value that is not a valid integer falls back to 0 rather than
        raising, matching how ``get_billing_anchor_day`` handles corruption.
        """
        hour = 0
        minute = 0
        h_key = f"billing_cycle_anchor_hour_{router_id}" if router_id is not None else "billing_cycle_anchor_hour"
        m_key = f"billing_cycle_anchor_minute_{router_id}" if router_id is not None else "billing_cycle_anchor_minute"
        h_setting = await session.get(AppSetting, h_key)
        m_setting = await session.get(AppSetting, m_key)
        if not h_setting and router_id in (None, 1):
            h_setting = await session.get(AppSetting, "billing_cycle_anchor_hour")
        if not m_setting and router_id in (None, 1):
            m_setting = await session.get(AppSetting, "billing_cycle_anchor_minute")
        if h_setting and h_setting.value:
            try:
                hour = max(0, min(int(h_setting.value), 23))
            except ValueError:
                hour = 0
        if m_setting and m_setting.value:
            try:
                minute = max(0, min(int(m_setting.value), 59))
            except ValueError:
                minute = 0
        return (hour, minute)

    @staticmethod
    async def set_billing_anchor_time(session: AsyncSession, hour: int, minute: int, router_id: Optional[int] = None) -> Tuple[int, int]:
        """Persist the reset time of day as two app settings.

        ``hour`` is clamped to 0-23 and ``minute`` to 0-59 rather than rejected,
        matching ``get_billing_anchor_time`` and ``set_billing_anchor_day``. The
        hour and minute rows are written and committed together, so a reader can
        never observe a half-updated time. Returns the clamped ``(hour, minute)``
        actually stored.
        """
        hh = max(0, min(hour, 23))
        mm = max(0, min(minute, 59))
        h_key = f"billing_cycle_anchor_hour_{router_id}" if router_id is not None else "billing_cycle_anchor_hour"
        m_key = f"billing_cycle_anchor_minute_{router_id}" if router_id is not None else "billing_cycle_anchor_minute"
        for key, value, desc in (
            (h_key, hh, "ISP billing cycle reset hour (0-23), router-local"),
            (m_key, mm, "ISP billing cycle reset minute (0-59)"),
        ):
            setting = await session.get(AppSetting, key)
            if setting:
                setting.value = str(value)
            else:
                session.add(AppSetting(key=key, value=str(value), description=desc))
        await session.commit()
        return (hh, mm)

    @classmethod
    async def get_historical_traffic(
        cls,
        session: AsyncSession,
        start_date: date,
        end_date: date,
        router_id: Optional[int] = None,
        range_preset: str = "7d",
        anchor_day: int = 1,
        include_breakdown_extras: bool = True,
    ) -> TrafficAnalyticsResponse:
        """Query and aggregate traffic metrics across Gateway, Users, Devices, and Timeline.

        ``include_breakdown_extras`` adds the per-entity last-seen, current-cycle
        and all-time columns plus the per-interface breakdown. The quota
        endpoint calls this only for the gateway total and the timeline, so it
        turns the extras off to skip four aggregate queries per call.
        """

        # 1. Fetch User Profiles and Devices, scoped to the router being viewed.
        # A NULL router_id predates multi-router support and belongs to every
        # router; a row that names a *different* router must not leak in - that
        # is what put router 1's profiles into router 2's "By Users" table with
        # a row of zeros each.
        users_query = select(User)
        devices_query = select(Device)
        if router_id:
            users_query = users_query.where(
                (User.router_id == router_id) | (User.router_id.is_(None))
            )
            devices_query = devices_query.where(
                (Device.router_id == router_id) | (Device.router_id.is_(None))
            )
        users_res = await session.execute(users_query)
        all_users = users_res.scalars().all()
        user_map = {u.id: u for u in all_users}
        dev_res = await session.execute(devices_query)
        all_devices = dev_res.scalars().all()

        # 2/3. Totals per owner, and 4. the same tables broken down per day.
        # All six go through rollups.sum_by so the date window and the router
        # filter cannot differ between them - they used to, silently.
        dev_totals = await rollups.sum_by(
            session, DeviceTrafficRollup, DeviceTrafficRollup.device_id, start_date, end_date, router_id=router_id,
        )

        def by_owner(per_device: dict) -> dict:
            """Fold a ``{device_id: volume}`` map up to ``{user_id: volume}``.

            Per-user volume is derived from the devices a profile owns *now*,
            not read from the parallel ``traffic_rollups`` ledger. Both are
            written from the same deltas, but they are keyed differently: the
            device ledger follows the device, while the user ledger records
            whoever happened to own it at the moment of each poll. Any change
            of owner therefore makes the two disagree permanently, and nothing
            ever reconciles them - on the developer's own install a laptop
            reassigned mid-day left 1.2 GB booked to its previous owner and
            0.4 GB (earned before anyone claimed it) booked to nobody, so the
            "by user" and "by device" breakdowns of the same range differed by
            25%. Deriving from devices makes a profile exactly the sum of the
            devices it owns, by construction, and a correction to a wrong
            assignment retroactively moves that device's history with it.
            """
            folded: dict = {}
            for dev in all_devices:
                if dev.user_id is None:
                    continue
                b_in, b_out = per_device.get(dev.id, (0, 0))
                acc = folded.setdefault(dev.user_id, [0, 0])
                acc[0] += b_in
                acc[1] += b_out
            return {uid: (v[0], v[1]) for uid, v in folded.items()}

        user_totals = by_owner(dev_totals)
        # Extra columns: current-cycle and all-time volume per owner, and the
        # per-interface breakdown. Skipped entirely when the caller only wants
        # the gateway total and the timeline (the quota endpoint).
        user_cycle: dict = {}
        dev_cycle: dict = {}
        user_alltime: dict = {}
        dev_alltime: dict = {}
        iface_range: dict = {}
        iface_cycle: dict = {}
        iface_alltime: dict = {}
        if include_breakdown_extras:
            now_local = await router_local_now(session, router_id=router_id)
            cyc_start, cyc_end = get_billing_cycle_dates(anchor_day, now_local.date())
            dev_cycle = await rollups.sum_by(
                session, DeviceTrafficRollup, DeviceTrafficRollup.device_id, cyc_start, cyc_end, router_id=router_id,
            )
            dev_alltime = await rollups.sum_by(
                session, DeviceTrafficRollup, DeviceTrafficRollup.device_id, _ALLTIME_START, _ALLTIME_END, router_id=router_id,
            )
            # Same derivation as the range figure, for the same reason.
            user_cycle = by_owner(dev_cycle)
            user_alltime = by_owner(dev_alltime)
            iface_range = await rollups.sum_by(
                session, InterfaceTrafficRollup, InterfaceTrafficRollup.interface_name,
                start_date, end_date, router_id=router_id,
            )
            iface_cycle = await rollups.sum_by(
                session, InterfaceTrafficRollup, InterfaceTrafficRollup.interface_name,
                cyc_start, cyc_end, router_id=router_id,
            )
            iface_alltime = await rollups.sum_by(
                session, InterfaceTrafficRollup, InterfaceTrafficRollup.interface_name,
                _ALLTIME_START, _ALLTIME_END, router_id=router_id,
            )

        daily = await rollups.daily_totals(session, start_date, end_date, router_id=router_id)
        r_daily_map = daily["router"]
        d_daily_map = daily["device"]
        s_daily_map = daily["self"]

        # Summed from the per-day map rather than queried again, so the range
        # total and the coverage split can never describe different volumes.
        self_in = sum(v[0] for v in s_daily_map.values())
        self_out = sum(v[1] for v in s_daily_map.values())

        # The gateway total is the timeline summed, so the headline figure and
        # the chart under it are the same number by construction.
        r_gw_in = sum(v[0] for v in r_daily_map.values())
        r_gw_out = sum(v[1] for v in r_daily_map.values())

        # Build the full date timeline, filling missing dates with 0. Each day
        # takes whichever of the gateway and the per-device sum saw more: on a
        # day before per-device accounting existed only the gateway has a
        # figure, and on a day the router was unreachable only the device
        # counters do. The per-user ledger is deliberately not in this max -
        # it is a second, differently-keyed record of the same bytes (see
        # ``by_owner``) and including it let a stale ledger inflate a day.
        timeline: List[DailyTrafficPoint] = []
        cur_d = start_date
        while cur_d <= end_date:
            r_in, r_out = r_daily_map.get(cur_d, (0, 0))
            d_in, d_out = d_daily_map.get(cur_d, (0, 0))
            day_in = max(r_in, d_in)
            day_out = max(r_out, d_out)
            timeline.append(DailyTrafficPoint(
                record_date=cur_d,
                bytes_in=day_in,
                bytes_out=day_out,
                total_bytes=day_in + day_out
            ))
            cur_d += timedelta(days=1)

        # 5. Calculate Total Gateway Consumption
        #
        # The gateway total is measured at the WAN interface and is authoritative.
        # It is deliberately NOT max()'d with the user/device aggregations: doing
        # so let a completely dead per-device accounting path masquerade as a
        # healthy dashboard. When no gateway sample exists at all, fall back to
        # the accounted sum so a fresh install still shows something, and report
        # that fact through accounting_health.
        sum_dev_in = sum(v[0] for v in dev_totals.values())
        sum_dev_out = sum(v[1] for v in dev_totals.values())
        # The router's own traffic is attributed volume like any other: it is
        # measured, and it belongs to something. Leaving it out of the numerator
        # would report a coverage gap that has in fact been closed.
        #
        # The per-device sum alone is the accounted figure - it used to be
        # max(devices, user ledger), which quietly papered over exactly the
        # divergence ``by_owner`` now removes.
        accounted_in = sum_dev_in + self_in
        accounted_out = sum_dev_out + self_out

        has_gateway_sample = (r_gw_in + r_gw_out) > 0
        if has_gateway_sample:
            gateway_in, gateway_out = r_gw_in, r_gw_out
        else:
            gateway_in, gateway_out = accounted_in, accounted_out
        gateway_total = gateway_in + gateway_out

        from backend.app.services.traffic_accounting import TrafficAccountingService
        started = await TrafficAccountingService.get_accounting_started(session)

        # Volume recorded before per-device accounting could possibly have seen
        # it. The start day itself counts as pre-accounting: gateway counters
        # ran all day, device counters only from the moment the mangle rules
        # were installed, so it is a partial day and would drag the ratio down
        # for a reason that has nothing to do with a fault.
        accounted_total = accounted_in + accounted_out
        pre_bytes = 0
        measured_gateway = 0
        measured_accounted = 0
        pre_accounted = 0
        if started is not None and has_gateway_sample:
            measured_gateway = rollups.sum_window(r_daily_map, after=started)
            pre_bytes = rollups.sum_window(r_daily_map) - measured_gateway
            # Per-device volume plus the router's own, matching the range
            # totals above.
            measured_accounted = (
                rollups.sum_window(d_daily_map, after=started)
                + rollups.sum_window(s_daily_map, after=started)
            )
            # The remainder of the range total, rather than an independent sum
            # of the earlier days. The two figures then always add back up to
            # the number the user and device tables show, which is what lets a
            # reader check the banner against the breakdown underneath it.
            pre_accounted = max(0, accounted_total - measured_accounted)

        accounting_health = cls._assess_accounting_health(
            gateway_bytes=gateway_total,
            accounted_bytes=accounted_total,
            has_gateway_sample=has_gateway_sample,
            accounting_started=started,
            pre_accounting_bytes=pre_bytes,
            measured_bytes=measured_gateway,
            measured_accounted_bytes=measured_accounted,
            pre_accounting_accounted_bytes=pre_accounted,
        )

        # 6. Build User Traffic Summaries
        user_summaries: List[UserTrafficSummary] = []
        for u in all_users:
            # All three windows come from the same per-device fold, so a
            # profile's figure is always exactly the sum of its devices' rows
            # in the table below it. No fall-through is needed any more: the
            # old "if the user ledger reads zero, sum the children instead"
            # branch existed only to paper over the two ledgers disagreeing.
            u_in, u_out = user_totals.get(u.id, (0, 0))
            u_total = u_in + u_out
            pct = round((u_total / gateway_total * 100), 2) if gateway_total > 0 else 0.0

            seens = [d.last_seen for d in u.devices if d.last_seen]
            uc_in, uc_out = user_cycle.get(u.id, (0, 0))
            ua_in, ua_out = user_alltime.get(u.id, (0, 0))

            user_summaries.append(UserTrafficSummary(
                user_id=u.id,
                user_name=u.name,
                avatar_icon=u.avatar_icon,
                bytes_in=u_in,
                bytes_out=u_out,
                total_bytes=u_total,
                pct_of_total=pct,
                device_count=len(u.devices),
                last_seen=max(seens) if seens else None,
                cycle_bytes=uc_in + uc_out,
                all_time_bytes=ua_in + ua_out,
            ))
        user_summaries.sort(key=lambda x: x.total_bytes, reverse=True)

        # 7. Build Device Traffic Summaries
        #
        # Deleted devices are not listed one by one - the operator removed them
        # on purpose - but their bytes are still part of their profile's total
        # (``by_owner`` folds every owned device, deleted included), so hiding
        # them outright would make the per-device rows fall short of the
        # per-user figure. Each profile's deleted devices are pooled into one
        # synthetic "Old devices" row instead.
        device_summaries: List[DeviceTrafficSummary] = []
        # owner_id -> [range_in, range_out, cycle, all_time, count]
        retired_pool: Dict[Optional[int], List[int]] = {}
        for d in all_devices:
            d_in, d_out = dev_totals.get(d.id, (0, 0))
            dc_in, dc_out = dev_cycle.get(d.id, (0, 0))
            da_in, da_out = dev_alltime.get(d.id, (0, 0))

            if getattr(d, "is_deleted", False):
                acc = retired_pool.setdefault(d.user_id, [0, 0, 0, 0, 0])
                acc[0] += d_in
                acc[1] += d_out
                acc[2] += dc_in + dc_out
                acc[3] += da_in + da_out
                acc[4] += 1
                continue

            d_total = d_in + d_out
            pct = round((d_total / gateway_total * 100), 2) if gateway_total > 0 else 0.0
            parent_user = user_map.get(d.user_id) if d.user_id else None

            device_summaries.append(DeviceTrafficSummary(
                device_id=d.id,
                mac_address=d.mac_address,
                hostname=d.hostname,
                custom_name=d.custom_name,
                ip_address=d.ip_address,
                vendor=d.vendor,
                user_id=d.user_id,
                user_name=parent_user.name if parent_user else None,
                bytes_in=d_in,
                bytes_out=d_out,
                total_bytes=d_total,
                pct_of_total=pct,
                speed_limit=d.speed_limit,
                is_paused=d.is_paused,
                is_hidden=d.is_hidden,
                last_seen=d.last_seen,
                cycle_bytes=dc_in + dc_out,
                all_time_bytes=da_in + da_out,
            ))

        for owner_id, (p_in, p_out, p_cycle, p_all, p_count) in retired_pool.items():
            if p_in + p_out + p_cycle + p_all == 0:
                continue
            p_total = p_in + p_out
            parent_user = user_map.get(owner_id) if owner_id else None
            device_summaries.append(DeviceTrafficSummary(
                device_id=-(owner_id or 0),
                mac_address="",
                custom_name="Old devices",
                user_id=owner_id,
                user_name=parent_user.name if parent_user else None,
                bytes_in=p_in,
                bytes_out=p_out,
                total_bytes=p_total,
                pct_of_total=round((p_total / gateway_total * 100), 2) if gateway_total > 0 else 0.0,
                last_seen=None,
                cycle_bytes=p_cycle,
                all_time_bytes=p_all,
                is_retired_pool=True,
            ))
        device_summaries.sort(key=lambda x: x.total_bytes, reverse=True)

        # Read monitored interfaces for metadata
        monitored_setting = await session.get(AppSetting, f"monitored_interfaces_{router_id}" if router_id else "monitored_interfaces_default")
        monitored_ifaces = []
        if monitored_setting and monitored_setting.value:
            import json
            try:
                monitored_ifaces = json.loads(monitored_setting.value)
            except Exception:
                monitored_ifaces = []

        self_total = self_in + self_out
        router_self = RouterSelfTrafficSummary(
            bytes_in=self_in,
            bytes_out=self_out,
            total_bytes=self_total,
            pct_of_total=round((self_total / gateway_total * 100), 2) if gateway_total > 0 else 0.0,
        )

        # Volume on devices nobody has claimed yet. It is real, measured
        # traffic and it is part of the gateway total, but it belongs to no
        # profile - so without a figure of its own the per-user breakdown can
        # never add up to the range total, and the difference reads as a
        # counting fault. On a network with a busy unclaimed device it is the
        # single largest missing piece.
        unassigned_in = sum(
            dev_totals.get(d.id, (0, 0))[0] for d in all_devices if d.user_id is None
        )
        unassigned_out = sum(
            dev_totals.get(d.id, (0, 0))[1] for d in all_devices if d.user_id is None
        )
        unassigned_total = unassigned_in + unassigned_out
        unassigned = UnassignedTrafficSummary(
            device_count=sum(1 for d in all_devices if d.user_id is None),
            bytes_in=unassigned_in,
            bytes_out=unassigned_out,
            total_bytes=unassigned_total,
            pct_of_total=round((unassigned_total / gateway_total * 100), 2) if gateway_total > 0 else 0.0,
        )

        # What the WAN measured but no counter could attribute, and the reverse.
        # Only one of the two is ever non-zero. An over-count is not noise: the
        # per-device rules match the forward chain by address with no WAN
        # constraint, so traffic between two local subnets is counted for both
        # ends without ever crossing the gateway.
        residual = gateway_total - (accounted_total)
        unaccounted_bytes = max(0, residual)
        over_accounted_bytes = max(0, -residual)

        gateway_summary = GatewayTrafficSummary(
            total_bytes_in=gateway_in,
            total_bytes_out=gateway_out,
            total_bytes=gateway_total,
            monitored_interfaces=monitored_ifaces
        )

        # 8. Per-interface breakdown. Union the three windows' interface names
        # so a tunnel that carried traffic this cycle but nothing in the last
        # seven days still gets a row.
        monitored_set = set(monitored_ifaces)
        iface_names = set(iface_range) | set(iface_cycle) | set(iface_alltime)
        interface_summaries: List[InterfaceTrafficSummary] = []
        for name in iface_names:
            r_in, r_out = iface_range.get(name, (0, 0))
            r_total = r_in + r_out
            interface_summaries.append(InterfaceTrafficSummary(
                interface_name=name,
                is_tunnel=is_tunnel_interface(name),
                is_monitored=name in monitored_set,
                bytes_in=r_in,
                bytes_out=r_out,
                total_bytes=r_total,
                pct_of_total=round((r_total / gateway_total * 100), 2) if gateway_total > 0 else 0.0,
                cycle_bytes=sum(iface_cycle.get(name, (0, 0))),
                all_time_bytes=sum(iface_alltime.get(name, (0, 0))),
            ))
        # Tunnels first (the point of the tab), then heaviest in the range.
        interface_summaries.sort(key=lambda x: (0 if x.is_tunnel else 1, -x.total_bytes, x.interface_name))

        return TrafficAnalyticsResponse(
            start_date=start_date,
            end_date=end_date,
            range_preset=range_preset,
            billing_anchor_day=anchor_day,
            gateway=gateway_summary,
            router_self=router_self,
            unassigned=unassigned,
            users=user_summaries,
            devices=device_summaries,
            interfaces=interface_summaries,
            timeline=timeline,
            unaccounted_bytes=unaccounted_bytes,
            over_accounted_bytes=over_accounted_bytes,
            accounting_health=accounting_health
        )

    @staticmethod
    def _assess_accounting_health(
        gateway_bytes: int,
        accounted_bytes: int,
        has_gateway_sample: bool,
        accounting_started: Optional[date] = None,
        pre_accounting_bytes: int = 0,
        measured_bytes: int = 0,
        measured_accounted_bytes: int = 0,
        pre_accounting_accounted_bytes: int = 0,
    ) -> AccountingHealth:
        """Compare per-device accounted volume against measured gateway volume.

        Surfaces a broken accounting path instead of hiding it behind a plausible
        looking total. Thresholds are deliberately loose: LAN-to-LAN traffic and
        router-local traffic legitimately never appear in per-device counters.

        Coverage is judged over the *measured window* only - the days on which
        per-device accounting ran from midnight to midnight. An earlier version
        divided the whole range's accounted bytes by the whole range's gateway
        bytes, so a range reaching one day back past the day accounting was
        switched on reported ~50% coverage and read as "half your traffic was
        lost". Nothing was lost: those bytes were simply never attributable.
        The pre-accounting volume is now reported as its own figure instead of
        being smeared into the ratio.
        """
        if not has_gateway_sample and accounted_bytes == 0:
            return AccountingHealth(
                gateway_bytes=gateway_bytes,
                accounted_bytes=accounted_bytes,
                coverage_pct=0.0,
                status="no_data",
                message="No traffic samples recorded yet for this period.",
                accounting_started=accounting_started,
            )

        has_split = accounting_started is not None and (pre_accounting_bytes or measured_bytes)
        # Ratio denominator: the measured window when the split is known, else
        # the whole range (a fresh install with no marker yet).
        ratio_gateway = measured_bytes if has_split else gateway_bytes
        ratio_accounted = measured_accounted_bytes if has_split else accounted_bytes
        coverage = round((ratio_accounted / ratio_gateway * 100), 2) if ratio_gateway > 0 else 0.0

        common = dict(
            gateway_bytes=gateway_bytes,
            accounted_bytes=accounted_bytes,
            coverage_pct=coverage,
            accounting_started=accounting_started,
            pre_accounting_bytes=pre_accounting_bytes,
            measured_bytes=measured_bytes,
            measured_accounted_bytes=measured_accounted_bytes,
            pre_accounting_accounted_bytes=pre_accounting_accounted_bytes,
        )

        # An active accounting path attributing almost nothing is a real fault,
        # and takes precedence over the informational pre-accounting notice.
        if ratio_gateway > 0 and coverage < 50.0:
            return AccountingHealth(
                **common,
                status="degraded",
                message=(
                    f"Only {coverage:.1f}% of gateway traffic could be attributed to "
                    f"a device. The per-device accounting rules on RouterOS are "
                    f"likely missing or not matching; per-user and per-device "
                    f"figures below are incomplete."
                ),
            )

        if pre_accounting_bytes > 0 and accounting_started is not None:
            return AccountingHealth(
                **common,
                status="partial",
                message=(
                    f"Per-device accounting started on {accounting_started.isoformat()}, "
                    f"so coverage is measured from the day after: {coverage:.1f}% of "
                    f"gateway traffic there is attributed to a device. The earlier part "
                    f"of this range mostly predates per-device counters, which is why "
                    f"the breakdown below totals more than the measured figure."
                ),
            )

        return AccountingHealth(**common, status="ok")

    @staticmethod
    def _compute_delta(curr: int, prev: Optional[int], *, reset: bool = False) -> int:
        """Compute delta between current monotonic counter and previous reading.

        ``reset=True`` forces the post-reset path (credit ``curr`` alone) even
        when ``curr >= prev``: after a reboot a fast interface counter can climb
        past its stale baseline within one poll, and without an explicit signal
        that reads as a tiny delta and loses the traffic since the reboot.
        """
        if prev is None:
            return 0  # Initial baseline snapshot
        if reset or curr < prev:
            return curr
        return curr - prev

    @classmethod
    async def record_traffic_snapshot(
        cls,
        session: AsyncSession,
        router_id: int,
        client: Any = None,
        router_uptime_seconds: Optional[int] = None,
    ) -> None:
        """Refresh the recent gateway / per-interface rollups from the samples.

        The gateway figure used to be a live counter delta accumulated here on
        every poll. That misfiled a whole evening of traffic onto the next day
        whenever a poll resumed after an outage that ran past local midnight,
        and it lost the pre-reboot tail on a restart. It is now derived from
        ``interface_metrics`` instead - see
        :mod:`backend.app.services.interface_rollups` - which attributes each
        byte to the day it moved and is restart-proof.

        ``client`` and ``router_uptime_seconds`` are accepted for call-site
        compatibility and ignored: the samples already carry everything needed.
        Per-user / per-device volume is still owned by
        :mod:`backend.app.services.traffic_accounting`.
        """
        try:
            await recompute_recent(session, router_id)
        except Exception as e:
            logger.warning(f"Interface rollup refresh failed for router {router_id}: {e}")

    @classmethod
    async def get_user_traffic_history(
        cls,
        session: AsyncSession,
        user_id: int,
        start_date: date,
        end_date: date,
        range_preset: str = "7d",
        intraday_now: Optional[datetime] = None,
    ) -> EntityTrafficHistoryResponse:
        """Detailed historical traffic timeline and device split for a specific user.

        ``intraday_now`` (the router-local clock) switches the timeline to
        30-minute buckets for a single-day range - the history modal's 1D view.
        The device breakdown and every total are unchanged; only the shape of
        ``timeline`` differs, flagged by ``resolution`` on the response.
        """
        user = await session.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        timeline: List[DailyTrafficPoint] = []
        total_in = 0
        total_out = 0
        peak_date: Optional[date] = None
        peak_label: Optional[str] = None
        peak_bytes = 0
        intraday = intraday_now is not None and start_date == end_date
        resolution = "half_hour" if intraday else "day"

        if intraday:
            # 30-minute buckets for the day. Stop at the current window when the
            # day is today, so a live view is not padded with empty future bars.
            day_start = datetime.combine(start_date, datetime.min.time())
            if intraday_now.date() == start_date:
                last_start = intraday_now.replace(
                    minute=(intraday_now.minute // 30) * 30, second=0, microsecond=0
                )
            else:
                last_start = day_start + timedelta(hours=23, minutes=30)

            rows = (await session.execute(
                select(
                    UserTrafficBucket.bucket_start,
                    UserTrafficBucket.bytes_in,
                    UserTrafficBucket.bytes_out,
                ).where(
                    UserTrafficBucket.user_id == user_id,
                    UserTrafficBucket.bucket_start >= day_start,
                    UserTrafficBucket.bucket_start <= last_start,
                )
            )).all()
            bmap = {
                r[0].replace(second=0, microsecond=0): (int(r[1] or 0), int(r[2] or 0))
                for r in rows
            }
            cur = day_start
            while cur <= last_start:
                b_in, b_out = bmap.get(cur, (0, 0))
                slot_total = b_in + b_out
                total_in += b_in
                total_out += b_out
                if slot_total > peak_bytes:
                    peak_bytes = slot_total
                    peak_date = start_date
                    peak_label = cur.strftime("%H:%M")
                timeline.append(DailyTrafficPoint(
                    record_date=start_date,
                    label=cur.strftime("%H:%M"),
                    bytes_in=b_in,
                    bytes_out=b_out,
                    total_bytes=slot_total,
                ))
                cur += timedelta(minutes=30)
        else:
            # 1. Daily user rollups
            stmt = (
                select(
                    TrafficRollup.record_date,
                    TrafficRollup.bytes_in,
                    TrafficRollup.bytes_out,
                )
                .where(
                    TrafficRollup.user_id == user_id,
                    TrafficRollup.record_date >= start_date,
                    TrafficRollup.record_date <= end_date,
                )
                .order_by(TrafficRollup.record_date.asc())
            )
            res = await session.execute(stmt)
            user_daily_map = {row[0]: (int(row[1] or 0), int(row[2] or 0)) for row in res.all()}

            # 2. Build contiguous timeline
            cur_d = start_date
            while cur_d <= end_date:
                b_in, b_out = user_daily_map.get(cur_d, (0, 0))
                day_total = b_in + b_out
                total_in += b_in
                total_out += b_out
                if day_total > peak_bytes:
                    peak_bytes = day_total
                    peak_date = cur_d
                timeline.append(DailyTrafficPoint(
                    record_date=cur_d,
                    bytes_in=b_in,
                    bytes_out=b_out,
                    total_bytes=day_total,
                ))
                cur_d += timedelta(days=1)

        total_bytes = total_in + total_out
        num_days = max(1, (end_date - start_date).days + 1)
        daily_avg = total_bytes // num_days

        # 3. Query per-device consumption for devices owned by this user
        dev_stmt = (
            select(
                Device,
                func.coalesce(func.sum(DeviceTrafficRollup.bytes_in), 0).label("d_in"),
                func.coalesce(func.sum(DeviceTrafficRollup.bytes_out), 0).label("d_out"),
            )
            .outerjoin(
                DeviceTrafficRollup,
                (DeviceTrafficRollup.device_id == Device.id)
                & (DeviceTrafficRollup.record_date >= start_date)
                & (DeviceTrafficRollup.record_date <= end_date),
            )
            .where(Device.user_id == user_id)
            .group_by(Device.id)
        )
        dev_res = await session.execute(dev_stmt)
        device_summaries: List[DeviceTrafficSummary] = []
        # Deleted devices are pooled into one "Old devices" row, as in the
        # range breakdown, so this list still sums to the user's total.
        retired = [0, 0]
        for dev, d_in, d_out in dev_res.all():
            d_in_int = int(d_in or 0)
            d_out_int = int(d_out or 0)
            if getattr(dev, "is_deleted", False):
                retired[0] += d_in_int
                retired[1] += d_out_int
                continue
            d_total = d_in_int + d_out_int
            pct = round((d_total / total_bytes * 100), 1) if total_bytes > 0 else 0.0
            device_summaries.append(DeviceTrafficSummary(
                device_id=dev.id,
                hostname=dev.hostname or "",
                custom_name=dev.custom_name,
                ip_address=dev.ip_address or "",
                mac_address=dev.mac_address,
                vendor=dev.vendor,
                user_id=dev.user_id,
                user_name=user.name,
                bytes_in=d_in_int,
                bytes_out=d_out_int,
                total_bytes=d_total,
                percentage_of_total=pct,
                is_active=dev.is_active,
                last_active=dev.last_seen,
            ))
        if retired[0] + retired[1] > 0:
            r_total = retired[0] + retired[1]
            device_summaries.append(DeviceTrafficSummary(
                device_id=-(user.id or 0),
                mac_address="",
                custom_name="Old devices",
                user_id=user.id,
                user_name=user.name,
                bytes_in=retired[0],
                bytes_out=retired[1],
                total_bytes=r_total,
                percentage_of_total=round((r_total / total_bytes * 100), 1) if total_bytes > 0 else 0.0,
                is_active=False,
                is_retired_pool=True,
            ))
        device_summaries.sort(key=lambda d: d.total_bytes, reverse=True)

        return EntityTrafficHistoryResponse(
            entity_type="user",
            entity_id=user.id,
            entity_name=user.name,
            avatar_icon=user.avatar_icon,
            range_preset=range_preset,
            start_date=start_date,
            end_date=end_date,
            resolution=resolution,
            total_bytes_in=total_in,
            total_bytes_out=total_out,
            total_bytes=total_bytes,
            daily_average_bytes=daily_avg,
            peak_date=peak_date,
            peak_label=peak_label,
            peak_bytes=peak_bytes,
            timeline=timeline,
            devices=device_summaries,
        )

    @classmethod
    async def get_device_traffic_history(
        cls,
        session: AsyncSession,
        device_id: int,
        start_date: date,
        end_date: date,
        range_preset: str = "7d",
    ) -> EntityTrafficHistoryResponse:
        """Detailed historical traffic timeline for a specific network device."""
        device = await session.get(Device, device_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        user_name = None
        if device.user_id:
            user = await session.get(User, device.user_id)
            if user:
                user_name = user.name

        # 1. Daily device rollups
        stmt = (
            select(
                DeviceTrafficRollup.record_date,
                DeviceTrafficRollup.bytes_in,
                DeviceTrafficRollup.bytes_out,
            )
            .where(
                DeviceTrafficRollup.device_id == device_id,
                DeviceTrafficRollup.record_date >= start_date,
                DeviceTrafficRollup.record_date <= end_date,
            )
            .order_by(DeviceTrafficRollup.record_date.asc())
        )
        res = await session.execute(stmt)
        dev_daily_map = {row[0]: (int(row[1] or 0), int(row[2] or 0)) for row in res.all()}

        # 2. Build contiguous timeline
        timeline: List[DailyTrafficPoint] = []
        cur_d = start_date
        total_in = 0
        total_out = 0
        peak_date = None
        peak_bytes = 0
        while cur_d <= end_date:
            b_in, b_out = dev_daily_map.get(cur_d, (0, 0))
            day_total = b_in + b_out
            total_in += b_in
            total_out += b_out
            if day_total > peak_bytes:
                peak_bytes = day_total
                peak_date = cur_d
            timeline.append(DailyTrafficPoint(
                record_date=cur_d,
                bytes_in=b_in,
                bytes_out=b_out,
                total_bytes=day_total,
            ))
            cur_d += timedelta(days=1)

        total_bytes = total_in + total_out
        num_days = max(1, (end_date - start_date).days + 1)
        daily_avg = total_bytes // num_days

        display_name = device.custom_name or device.hostname or device.mac_address

        return EntityTrafficHistoryResponse(
            entity_type="device",
            entity_id=device.id,
            entity_name=display_name,
            mac_address=device.mac_address,
            ip_address=device.ip_address,
            user_id=device.user_id,
            user_name=user_name,
            range_preset=range_preset,
            start_date=start_date,
            end_date=end_date,
            total_bytes_in=total_in,
            total_bytes_out=total_out,
            total_bytes=total_bytes,
            daily_average_bytes=daily_avg,
            peak_date=peak_date,
            peak_bytes=peak_bytes,
            timeline=timeline,
        )
