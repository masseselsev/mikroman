import calendar
import logging
from datetime import date, datetime, timedelta
from typing import Any, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    AppSetting,
    Device,
    DeviceTrafficRollup,
    InterfaceTrafficRollup,
    TrafficRollup,
    User,
)
from backend.app.schemas.analytics import (
    AccountingHealth,
    DailyTrafficPoint,
    DeviceTrafficSummary,
    GatewayTrafficSummary,
    InterfaceTrafficSummary,
    RouterSelfTrafficSummary,
    TrafficAnalyticsResponse,
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

    if preset == "today":
        return (today, today, "today")
    elif preset == "yesterday":
        yest = today - timedelta(days=1)
        return (yest, yest, "yesterday")
    elif preset == "7d":
        return (today - timedelta(days=6), today, "7d")
    elif preset == "30d":
        return (today - timedelta(days=29), today, "30d")
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
    else:
        # Default fallback: 7 days
        return (today - timedelta(days=6), today, "7d")


class AnalyticsEngine:
    """Historical traffic accounting and aggregation engine."""

    @staticmethod
    async def get_billing_anchor_day(session: AsyncSession) -> int:
        """Fetch the configured ISP billing cycle anchor day from app settings."""
        setting = await session.get(AppSetting, "billing_cycle_anchor_day")
        if setting and setting.value:
            try:
                return max(1, min(int(setting.value), 31))
            except ValueError:
                return 1
        return 1

    @staticmethod
    async def set_billing_anchor_day(session: AsyncSession, anchor_day: int) -> int:
        """Save the ISP billing cycle anchor day to app settings."""
        day = max(1, min(anchor_day, 31))
        setting = await session.get(AppSetting, "billing_cycle_anchor_day")
        if setting:
            setting.value = str(day)
        else:
            setting = AppSetting(key="billing_cycle_anchor_day", value=str(day), description="ISP billing cycle monthly anchor day (1-31)")
            session.add(setting)
        await session.commit()
        return day

    @staticmethod
    async def get_billing_anchor_time(session: AsyncSession) -> Tuple[int, int]:
        """The configured reset time of day as ``(hour, minute)``.

        Defaults to midnight, which reproduces the pre-existing date-only
        behaviour exactly, so an install that never set a time is unaffected.
        A stored value that is not a valid integer falls back to 0 rather than
        raising, matching how ``get_billing_anchor_day`` handles corruption.
        """
        hour = 0
        minute = 0
        h_setting = await session.get(AppSetting, "billing_cycle_anchor_hour")
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
    async def set_billing_anchor_time(session: AsyncSession, hour: int, minute: int) -> Tuple[int, int]:
        """Persist the reset time of day as two app settings.

        ``hour`` is clamped to 0-23 and ``minute`` to 0-59 rather than rejected,
        matching ``get_billing_anchor_time`` and ``set_billing_anchor_day``. The
        hour and minute rows are written and committed together, so a reader can
        never observe a half-updated time. Returns the clamped ``(hour, minute)``
        actually stored.
        """
        hh = max(0, min(hour, 23))
        mm = max(0, min(minute, 59))
        for key, value, desc in (
            ("billing_cycle_anchor_hour", hh, "ISP billing cycle reset hour (0-23), router-local"),
            ("billing_cycle_anchor_minute", mm, "ISP billing cycle reset minute (0-59)"),
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

        # 1. Fetch User Profiles and Devices
        users_query = select(User)
        users_res = await session.execute(users_query)
        all_users = users_res.scalars().all()
        user_map = {u.id: u for u in all_users}

        devices_query = select(Device)
        if router_id:
            # Devices discovered before multi-router support have a NULL router_id.
            # Filtering them out silently removed real clients from every view.
            devices_query = devices_query.where(
                (Device.router_id == router_id) | (Device.router_id.is_(None))
            )
        dev_res = await session.execute(devices_query)
        all_devices = dev_res.scalars().all()

        # 2/3. Totals per owner, and 4. the same tables broken down per day.
        # All six go through rollups.sum_by so the date window and the router
        # filter cannot differ between them - they used to, silently.
        user_totals = await rollups.sum_by(
            session, TrafficRollup, TrafficRollup.user_id, start_date, end_date
        )
        dev_totals = await rollups.sum_by(
            session, DeviceTrafficRollup, DeviceTrafficRollup.device_id, start_date, end_date
        )

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
            now_local = await router_local_now(session)
            cyc_start, cyc_end = get_billing_cycle_dates(anchor_day, now_local.date())
            user_cycle = await rollups.sum_by(
                session, TrafficRollup, TrafficRollup.user_id, cyc_start, cyc_end
            )
            dev_cycle = await rollups.sum_by(
                session, DeviceTrafficRollup, DeviceTrafficRollup.device_id, cyc_start, cyc_end
            )
            user_alltime = await rollups.sum_by(
                session, TrafficRollup, TrafficRollup.user_id, _ALLTIME_START, _ALLTIME_END
            )
            dev_alltime = await rollups.sum_by(
                session, DeviceTrafficRollup, DeviceTrafficRollup.device_id, _ALLTIME_START, _ALLTIME_END
            )
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
        u_daily_map = daily["user"]
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

        # Build full date timeline (fill missing dates with 0, taking daily maximum across interfaces/users/devices)
        timeline: List[DailyTrafficPoint] = []
        cur_d = start_date
        while cur_d <= end_date:
            r_in, r_out = r_daily_map.get(cur_d, (0, 0))
            u_in, u_out = u_daily_map.get(cur_d, (0, 0))
            d_in, d_out = d_daily_map.get(cur_d, (0, 0))
            day_in = max(r_in, u_in, d_in)
            day_out = max(r_out, u_out, d_out)
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
        sum_user_in = sum(v[0] for v in user_totals.values())
        sum_user_out = sum(v[1] for v in user_totals.values())
        # The router's own traffic is attributed volume like any other: it is
        # measured, and it belongs to something. Leaving it out of the numerator
        # would report a coverage gap that has in fact been closed.
        accounted_in = max(sum_dev_in, sum_user_in) + self_in
        accounted_out = max(sum_dev_out, sum_user_out) + self_out

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
            # Users can hold volume devices no longer do (a deleted device keeps
            # its owner's totals), so take whichever level saw more, exactly as
            # the range totals above do.
            measured_accounted = max(
                rollups.sum_window(d_daily_map, after=started),
                rollups.sum_window(u_daily_map, after=started),
            ) + rollups.sum_window(s_daily_map, after=started)
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
            u_in, u_out = user_totals.get(u.id, (0, 0))
            # Also sum child device totals if user rollup is 0
            if u_in == 0 and u_out == 0:
                child_devs = [d.id for d in u.devices]
                u_in = sum(dev_totals.get(did, (0, 0))[0] for did in child_devs)
                u_out = sum(dev_totals.get(did, (0, 0))[1] for did in child_devs)

            u_total = u_in + u_out
            pct = round((u_total / gateway_total * 100), 2) if gateway_total > 0 else 0.0

            seens = [d.last_seen for d in u.devices if d.last_seen]
            uc_in, uc_out = user_cycle.get(u.id, (0, 0))
            ua_in, ua_out = user_alltime.get(u.id, (0, 0))
            # Same fall-through as the range figure: if the user rollup is empty
            # (older installs stored volume per device only) sum the children.
            if uc_in == 0 and uc_out == 0:
                uc_in = sum(dev_cycle.get(d.id, (0, 0))[0] for d in u.devices)
                uc_out = sum(dev_cycle.get(d.id, (0, 0))[1] for d in u.devices)
            if ua_in == 0 and ua_out == 0:
                ua_in = sum(dev_alltime.get(d.id, (0, 0))[0] for d in u.devices)
                ua_out = sum(dev_alltime.get(d.id, (0, 0))[1] for d in u.devices)

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
        device_summaries: List[DeviceTrafficSummary] = []
        for d in all_devices:
            d_in, d_out = dev_totals.get(d.id, (0, 0))
            d_total = d_in + d_out
            pct = round((d_total / gateway_total * 100), 2) if gateway_total > 0 else 0.0
            parent_user = user_map.get(d.user_id) if d.user_id else None

            dc_in, dc_out = dev_cycle.get(d.id, (0, 0))
            da_in, da_out = dev_alltime.get(d.id, (0, 0))

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
            users=user_summaries,
            devices=device_summaries,
            interfaces=interface_summaries,
            timeline=timeline,
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
