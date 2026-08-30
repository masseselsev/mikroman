import calendar
import logging
from datetime import date, timedelta
from typing import Any, List, Optional, Tuple

from sqlalchemy import func, select
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
    DailyTrafficPoint,
    DeviceTrafficSummary,
    GatewayTrafficSummary,
    TrafficAnalyticsResponse,
    UserTrafficSummary,
)

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
    anchor_day: int = 1
) -> Tuple[date, date, str]:
    """Resolve a date range preset or explicit custom dates into concrete (start_date, end_date)."""
    today = date.today()

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
            devices_query = devices_query.where(Device.router_id == router_id)
        dev_res = await session.execute(devices_query)
        all_devices = dev_res.scalars().all()

        # 2. Query User Traffic Rollups in Date Window
        user_rollups_q = (
            select(
                TrafficRollup.user_id,
                func.sum(TrafficRollup.bytes_in).label("total_in"),
                func.sum(TrafficRollup.bytes_out).label("total_out")
            )
            .where(TrafficRollup.record_date >= start_date)
            .where(TrafficRollup.record_date <= end_date)
            .group_by(TrafficRollup.user_id)
        )
        user_rollups_res = await session.execute(user_rollups_q)
        user_totals = {row.user_id: (int(row.total_in or 0), int(row.total_out or 0)) for row in user_rollups_res}

        # 3. Query Device Traffic Rollups in Date Window
        dev_rollups_q = (
            select(
                DeviceTrafficRollup.device_id,
                func.sum(DeviceTrafficRollup.bytes_in).label("total_in"),
                func.sum(DeviceTrafficRollup.bytes_out).label("total_out")
            )
            .where(DeviceTrafficRollup.record_date >= start_date)
            .where(DeviceTrafficRollup.record_date <= end_date)
            .group_by(DeviceTrafficRollup.device_id)
        )
        dev_rollups_res = await session.execute(dev_rollups_q)
        dev_totals = {row.device_id: (int(row.total_in or 0), int(row.total_out or 0)) for row in dev_rollups_res}

        # 3b. Query Router Gateway Traffic Rollups in Date Window
        router_rollups_q = (
            select(
                func.sum(RouterTrafficRollup.bytes_in).label("total_in"),
                func.sum(RouterTrafficRollup.bytes_out).label("total_out")
            )
            .where(RouterTrafficRollup.record_date >= start_date)
            .where(RouterTrafficRollup.record_date <= end_date)
        )
        if router_id:
            router_rollups_q = router_rollups_q.where(RouterTrafficRollup.router_id == router_id)
        r_roll_res = await session.execute(router_rollups_q)
        r_row = r_roll_res.first()
        r_gw_in = int(r_row[0] or 0) if r_row else 0
        r_gw_out = int(r_row[1] or 0) if r_row else 0

        # 4. Query Daily Timeline Data across Router WAN Interfaces, Users, and Devices
        # a. Router daily rollups
        r_daily_q = (
            select(
                RouterTrafficRollup.record_date,
                func.sum(RouterTrafficRollup.bytes_in).label("day_in"),
                func.sum(RouterTrafficRollup.bytes_out).label("day_out")
            )
            .where(RouterTrafficRollup.record_date >= start_date)
            .where(RouterTrafficRollup.record_date <= end_date)
        )
        if router_id:
            r_daily_q = r_daily_q.where(RouterTrafficRollup.router_id == router_id)
        r_daily_q = r_daily_q.group_by(RouterTrafficRollup.record_date)
        r_daily_res = await session.execute(r_daily_q)
        r_daily_map = {row.record_date: (int(row.day_in or 0), int(row.day_out or 0)) for row in r_daily_res}

        # b. User daily rollups
        u_daily_q = (
            select(
                TrafficRollup.record_date,
                func.sum(TrafficRollup.bytes_in).label("day_in"),
                func.sum(TrafficRollup.bytes_out).label("day_out")
            )
            .where(TrafficRollup.record_date >= start_date)
            .where(TrafficRollup.record_date <= end_date)
            .group_by(TrafficRollup.record_date)
        )
        u_daily_res = await session.execute(u_daily_q)
        u_daily_map = {row.record_date: (int(row.day_in or 0), int(row.day_out or 0)) for row in u_daily_res}

        # c. Device daily rollups
        d_daily_q = (
            select(
                DeviceTrafficRollup.record_date,
                func.sum(DeviceTrafficRollup.bytes_in).label("day_in"),
                func.sum(DeviceTrafficRollup.bytes_out).label("day_out")
            )
            .where(DeviceTrafficRollup.record_date >= start_date)
            .where(DeviceTrafficRollup.record_date <= end_date)
            .group_by(DeviceTrafficRollup.record_date)
        )
        d_daily_res = await session.execute(d_daily_q)
        d_daily_map = {row.record_date: (int(row.day_in or 0), int(row.day_out or 0)) for row in d_daily_res}

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
        sum_dev_in = sum(v[0] for v in dev_totals.values())
        sum_dev_out = sum(v[1] for v in dev_totals.values())
        sum_user_in = sum(v[0] for v in user_totals.values())
        sum_user_out = sum(v[1] for v in user_totals.values())

        # Effective total gateway bandwidth is the maximum of interface / user / device aggregations
        gateway_in = max(r_gw_in, sum_dev_in, sum_user_in)
        gateway_out = max(r_gw_out, sum_dev_out, sum_user_out)
        gateway_total = gateway_in + gateway_out

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
            users=user_summaries,
            devices=device_summaries,
            timeline=timeline
        )

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

    @staticmethod
    def _compute_delta(curr: int, prev: Optional[int]) -> int:
        """Compute delta between current monotonic counter and previous reading, handling restarts."""
        if prev is None:
            return 0  # Initial baseline snapshot
        if curr >= prev:
            return curr - prev
        # Counter wrapped or router/queue rebooted
        return curr

    @classmethod
    async def record_traffic_snapshot(
        cls,
        session: AsyncSession,
        router_id: int,
        client: Any
    ) -> None:
        """Periodic background snapshot to accumulate daily traffic deltas from RouterOS Simple Queues and Interfaces."""
        today = date.today()
        today_str = str(today)

        try:
            baselines = await cls._get_baselines(session)
            r_baselines = baselines.setdefault("router", {})
            u_baselines = baselines.setdefault("users", {})
            d_baselines = baselines.setdefault("devices", {})

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
            if prev_r is None:
                r_baselines[r_key] = {"rx": r_rx, "tx": r_tx, "last_date": today_str}
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
                d_rx = cls._compute_delta(r_rx, prev_r.get("rx"))
                d_tx = cls._compute_delta(r_tx, prev_r.get("tx"))
                r_baselines[r_key] = {"rx": r_rx, "tx": r_tx, "last_date": today_str}

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

            # 2. Simple Queues for Users & Devices
            queues = await client.get_simple_queues()
            q_map = {q.name: q for q in queues if getattr(q, "name", None)}

            # Users
            users_res = await session.execute(select(User))
            users = users_res.scalars().all()
            for u in users:
                matched_q = q_map.get(f"mikroman-{u.name}") or q_map.get(f"mikroman-user-{u.id}")
                if not matched_q:
                    for q in queues:
                        comment = getattr(q, "comment", "") or ""
                        if f"user_{u.id}" in comment or f":managed:{u.name}" in comment:
                            matched_q = q
                            break

                if matched_q:
                    raw_bytes = getattr(matched_q, "bytes", "0/0") or "0/0"
                    if "/" in raw_bytes:
                        out_str, in_str = raw_bytes.split("/", 1)
                        b_out, b_in = int(out_str or 0), int(in_str or 0)
                    else:
                        b_out, b_in = 0, 0

                    u_key = str(u.id)
                    prev_u = u_baselines.get(u_key)
                    if prev_u is None:
                        u_baselines[u_key] = {"rx": b_in, "tx": b_out, "last_date": today_str}
                        u_stmt = select(TrafficRollup).where(
                            TrafficRollup.user_id == u.id,
                            TrafficRollup.record_date == today
                        )
                        u_res = await session.execute(u_stmt)
                        if not u_res.scalar_one_or_none():
                            session.add(TrafficRollup(
                                user_id=u.id,
                                record_date=today,
                                bytes_in=0,
                                bytes_out=0
                            ))
                    else:
                        d_u_in = cls._compute_delta(b_in, prev_u.get("rx"))
                        d_u_out = cls._compute_delta(b_out, prev_u.get("tx"))
                        u_baselines[u_key] = {"rx": b_in, "tx": b_out, "last_date": today_str}

                        u_stmt = select(TrafficRollup).where(
                            TrafficRollup.user_id == u.id,
                            TrafficRollup.record_date == today
                        )
                        u_res = await session.execute(u_stmt)
                        u_rollup = u_res.scalar_one_or_none()
                        if u_rollup:
                            u_rollup.bytes_in += d_u_in
                            u_rollup.bytes_out += d_u_out
                        else:
                            session.add(TrafficRollup(
                                user_id=u.id,
                                record_date=today,
                                bytes_in=d_u_in,
                                bytes_out=d_u_out
                            ))

            # Devices
            devices_q = select(Device)
            if router_id:
                devices_q = devices_q.where(Device.router_id == router_id)
            devices_res = await session.execute(devices_q)
            devices = devices_res.scalars().all()
            for d in devices:
                matched_dev_q = None
                for q in queues:
                    comment = getattr(q, "comment", "") or ""
                    if f"dev_{d.id}" in comment:
                        matched_dev_q = q
                        break
                if matched_dev_q:
                    raw_bytes = getattr(matched_dev_q, "bytes", "0/0") or "0/0"
                    if "/" in raw_bytes:
                        out_str, in_str = raw_bytes.split("/", 1)
                        d_out, d_in = int(out_str or 0), int(in_str or 0)
                    else:
                        d_out, d_in = 0, 0

                    d_key = str(d.id)
                    prev_d = d_baselines.get(d_key)
                    if prev_d is None:
                        d_baselines[d_key] = {"rx": d_in, "tx": d_out, "last_date": today_str}
                        d_stmt = select(DeviceTrafficRollup).where(
                            DeviceTrafficRollup.device_id == d.id,
                            DeviceTrafficRollup.record_date == today
                        )
                        d_res = await session.execute(d_stmt)
                        if not d_res.scalar_one_or_none():
                            session.add(DeviceTrafficRollup(
                                device_id=d.id,
                                record_date=today,
                                bytes_in=0,
                                bytes_out=0
                            ))
                    else:
                        d_d_in = cls._compute_delta(d_in, prev_d.get("rx"))
                        d_d_out = cls._compute_delta(d_out, prev_d.get("tx"))
                        d_baselines[d_key] = {"rx": d_in, "tx": d_out, "last_date": today_str}

                        d_stmt = select(DeviceTrafficRollup).where(
                            DeviceTrafficRollup.device_id == d.id,
                            DeviceTrafficRollup.record_date == today
                        )
                        d_res = await session.execute(d_stmt)
                        d_rollup = d_res.scalar_one_or_none()
                        if d_rollup:
                            d_rollup.bytes_in += d_d_in
                            d_rollup.bytes_out += d_d_out
                        else:
                            session.add(DeviceTrafficRollup(
                                device_id=d.id,
                                record_date=today,
                                bytes_in=d_d_in,
                                bytes_out=d_d_out
                            ))

            # Persist updated baselines
            await cls._save_baselines(session, baselines)
            await session.commit()
        except Exception as e:
            logger.debug(f"Failed to record traffic snapshot: {e}")
