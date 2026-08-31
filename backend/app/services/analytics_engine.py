import calendar
import logging
from datetime import date, timedelta
from typing import Any, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    AppSetting,
    Device,
    DeviceTrafficRollup,
    RouterTrafficRollup,
    TrafficRollup,
    User,
)
from backend.app.schemas.analytics import (
    AccountingHealth,
    DailyTrafficPoint,
    DeviceTrafficSummary,
    GatewayTrafficSummary,
    RouterSelfTrafficSummary,
    TrafficAnalyticsResponse,
    UserTrafficSummary,
)
from backend.app.services import rollups
from backend.app.services.router_time import router_local_date

logger = logging.getLogger("mikroman.analytics_engine")


def get_billing_cycle_dates(anchor_day: int, reference_date: Optional[date] = None, previous: bool = False) -> Tuple[date, date]:
    """Calculate the start and end dates of an ISP billing cycle based on an anchor renewal day.

    Args:
        anchor_day: Day of month when traffic resets (1-31).
        reference_date: Reference date (defaults to today).
        previous: If True, returns the previous billing cycle window.

    Returns:
        Tuple of (start_date, end_date).
    """
    ref = reference_date or date.today()
    # Bound anchor day to valid month range
    day = max(1, min(anchor_day, 31))

    if ref.day >= day:
        # We are currently in the cycle that started this month on anchor_day
        max_days = calendar.monthrange(ref.year, ref.month)[1]
        actual_start_day = min(day, max_days)
        start_date = date(ref.year, ref.month, actual_start_day)

        # Cycle ends on (anchor_day - 1) of next month
        if ref.month == 12:
            next_year, next_month = ref.year + 1, 1
        else:
            next_year, next_month = ref.year, ref.month + 1
        next_max_days = calendar.monthrange(next_year, next_month)[1]
        end_date = date(next_year, next_month, min(day - 1 if day > 1 else next_max_days, next_max_days))
    else:
        # We are in the cycle that started last month on anchor_day
        if ref.month == 1:
            prev_year, prev_month = ref.year - 1, 12
        else:
            prev_year, prev_month = ref.year, ref.month - 1
        prev_max_days = calendar.monthrange(prev_year, prev_month)[1]
        start_date = date(prev_year, prev_month, min(day, prev_max_days))
        end_date = date(ref.year, ref.month, min(day - 1 if day > 1 else calendar.monthrange(ref.year, ref.month)[1], calendar.monthrange(ref.year, ref.month)[1]))

    if previous:
        # Shift back by one full billing cycle
        if start_date.month == 1:
            prev_start_year, prev_start_month = start_date.year - 1, 12
        else:
            prev_start_year, prev_start_month = start_date.year, start_date.month - 1
        p_max_days = calendar.monthrange(prev_start_year, prev_start_month)[1]
        prev_start = date(prev_start_year, prev_start_month, min(day, p_max_days))
        prev_end = start_date - timedelta(days=1)
        return (prev_start, prev_end)

    return (start_date, end_date)


def resolve_date_range(
    preset: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    anchor_day: int = 1,
    today: Optional[date] = None
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
        s, e = get_billing_cycle_dates(anchor_day, today, previous=False)
        # Cap current cycle view to today for live measurement
        return (s, min(e, today), "billing_current")
    elif preset == "billing_previous":
        s, e = get_billing_cycle_dates(anchor_day, today, previous=True)
        return (s, e, "billing_previous")
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

    @classmethod
    async def get_historical_traffic(
        cls,
        session: AsyncSession,
        start_date: date,
        end_date: date,
        router_id: Optional[int] = None,
        range_preset: str = "7d",
        anchor_day: int = 1
    ) -> TrafficAnalyticsResponse:
        """Query and aggregate traffic metrics across Gateway, Users, Devices, and Timeline."""

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

            user_summaries.append(UserTrafficSummary(
                user_id=u.id,
                user_name=u.name,
                avatar_icon=u.avatar_icon,
                bytes_in=u_in,
                bytes_out=u_out,
                total_bytes=u_total,
                pct_of_total=pct,
                device_count=len(u.devices)
            ))
        user_summaries.sort(key=lambda x: x.total_bytes, reverse=True)

        # 7. Build Device Traffic Summaries
        device_summaries: List[DeviceTrafficSummary] = []
        for d in all_devices:
            d_in, d_out = dev_totals.get(d.id, (0, 0))
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
                is_hidden=d.is_hidden
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

        return TrafficAnalyticsResponse(
            start_date=start_date,
            end_date=end_date,
            range_preset=range_preset,
            billing_anchor_day=anchor_day,
            gateway=gateway_summary,
            router_self=router_self,
            users=user_summaries,
            devices=device_summaries,
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

    @classmethod
    async def _get_baselines(cls, session: AsyncSession) -> dict:
        """Fetch saved counter baselines from app settings."""
        setting = await session.get(AppSetting, "traffic_counter_baselines")
        if setting and setting.value:
            try:
                import json
                return json.loads(setting.value)
            except Exception:
                return {}
        return {}

    @classmethod
    async def _save_baselines(cls, session: AsyncSession, baselines: dict) -> None:
        """Persist counter baselines to app settings."""
        import json
        raw_val = json.dumps(baselines)
        setting = await session.get(AppSetting, "traffic_counter_baselines")
        if setting:
            setting.value = raw_val
        else:
            session.add(AppSetting(
                key="traffic_counter_baselines",
                value=raw_val,
                description="Live counter baselines for delta traffic accumulation"
            ))

    # A drop of more than this in router uptime between two polls is a reboot,
    # not clock jitter.
    _REBOOT_SLACK_SECONDS = 90

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
        client: Any,
        router_uptime_seconds: Optional[int] = None,
    ) -> None:
        """Accumulate daily gateway rollups from the monitored WAN interface counters.

        Per-user and per-device volume is NOT derived here. Simple Queue byte
        counters were measured to stay frozen at zero on RouterOS 7.25 while
        traffic flowed, so that accounting lives in
        ``backend.app.services.traffic_accounting`` which reads firewall mangle
        counters instead. This method owns the gateway level only.

        ``router_uptime_seconds`` running backwards between polls means the
        router rebooted and the interface counters reset; this tick then credits
        the bytes since the reboot instead of differencing against a baseline
        that no longer exists. A plain network outage is not a reboot and needs
        no special handling - ordinary differencing on reconnect covers the gap.
        """
        # Keyed to the router's date: a UTC container files the router's
        # evening traffic under the previous day.
        today = await router_local_date(session)
        today_str = str(today)

        try:
            baselines = await cls._get_baselines(session)
            r_baselines = baselines.setdefault("router", {})

            # 1. Router WAN Interface Totals
            monitored_setting = await session.get(AppSetting, f"monitored_interfaces_{router_id}" if router_id else "monitored_interfaces_default")
            monitored = []
            if monitored_setting and monitored_setting.value:
                import json
                try:
                    monitored = json.loads(monitored_setting.value)
                except Exception:
                    monitored = []
            if not monitored:
                monitored = ["ether1"]

            ifaces = await client.get_interfaces()
            r_rx = 0
            r_tx = 0
            for iface in ifaces:
                i_name = getattr(iface, "name", iface.get("name") if isinstance(iface, dict) else "")
                if i_name in monitored or (not monitored and "ether1" in i_name):
                    r_rx += getattr(iface, "rx_byte", iface.get("rx_byte", 0) if isinstance(iface, dict) else 0)
                    r_tx += getattr(iface, "tx_byte", iface.get("tx_byte", 0) if isinstance(iface, dict) else 0)

            r_key = str(router_id)
            prev_r = r_baselines.get(r_key)
            # Uptime is stored per router inside its own baseline entry, so a
            # reboot of one router in a multi-router setup does not disturb the
            # others' accounting.
            prev_uptime = prev_r.get("uptime_s") if isinstance(prev_r, dict) else None
            rebooted = (
                router_uptime_seconds is not None
                and isinstance(prev_uptime, int)
                and router_uptime_seconds + cls._REBOOT_SLACK_SECONDS < prev_uptime
            )
            if rebooted:
                logger.info(
                    f"Router {router_id} uptime dropped {prev_uptime}s -> "
                    f"{router_uptime_seconds}s: crediting gateway bytes since the reboot"
                )
            new_entry = {"rx": r_rx, "tx": r_tx, "last_date": today_str}
            if router_uptime_seconds is not None:
                new_entry["uptime_s"] = router_uptime_seconds

            if prev_r is None:
                r_baselines[r_key] = new_entry
                r_stmt = select(RouterTrafficRollup).where(
                    RouterTrafficRollup.router_id == router_id,
                    RouterTrafficRollup.record_date == today
                )
                r_res = await session.execute(r_stmt)
                if not r_res.scalar_one_or_none():
                    session.add(RouterTrafficRollup(
                        router_id=router_id,
                        record_date=today,
                        bytes_in=0,
                        bytes_out=0
                    ))
            else:
                d_rx = cls._compute_delta(r_rx, prev_r.get("rx"), reset=rebooted)
                d_tx = cls._compute_delta(r_tx, prev_r.get("tx"), reset=rebooted)
                r_baselines[r_key] = new_entry

                r_stmt = select(RouterTrafficRollup).where(
                    RouterTrafficRollup.router_id == router_id,
                    RouterTrafficRollup.record_date == today
                )
                r_res = await session.execute(r_stmt)
                r_rollup = r_res.scalar_one_or_none()
                if r_rollup:
                    r_rollup.bytes_in += d_rx
                    r_rollup.bytes_out += d_tx
                else:
                    session.add(RouterTrafficRollup(
                        router_id=router_id,
                        record_date=today,
                        bytes_in=d_rx,
                        bytes_out=d_tx
                    ))

            # Persist updated baselines
            await cls._save_baselines(session, baselines)
            await session.commit()
        except Exception as e:
            logger.debug(f"Failed to record traffic snapshot: {e}")
