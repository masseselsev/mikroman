import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import InterfaceMetric, SystemMetric
from backend.app.db.prune import delete_older_than
from backend.app.schemas.metrics import (
    InterfaceHistoryResponse,
    InterfaceRatePoint,
    SystemMetricPoint,
    SystemMetricsResponse,
)
from backend.app.services.router_time import get_router_offset
from backend.app.services.routeros import RouterOSClient

logger = logging.getLogger("mikroman.metrics_collector")


def _epoch_seconds(column):
    """SQL expression: whole seconds since the Unix epoch for a stored sample.

    Samples are naive-UTC ``DateTime`` values and SQLite's ``strftime('%s', ...)``
    reads exactly that as UTC, so the bucket grid stays pinned to UTC whatever
    zone the host runs in - the same reason the Python-side helper this replaces
    stamped ``timezone.utc`` explicitly instead of trusting
    ``datetime.timestamp()``, which assumes the system zone on a naive value.
    """
    return cast(func.strftime("%s", column), Integer)


def _time_bucket(column, bucket_seconds: int):
    """SQL expression naming the fixed-width display bucket a sample falls in.

    Both operands are integers, so SQLite floors the division and no sample ever
    straddles two buckets.
    """
    return cast(_epoch_seconds(column) / bucket_seconds, Integer)


def _stamp_from_epoch(epoch: Optional[int], tz_shift: timedelta) -> datetime:
    """Render a bucket's epoch second as the timestamp the chart is drawn with.

    Back into naive UTC (which is how samples are stored) so the existing
    router-offset shift applies to it unchanged, rather than re-deriving the
    instant from a bucket index and drifting by the bucket's partial fill.
    """
    base = datetime.fromtimestamp(int(epoch), timezone.utc).replace(tzinfo=None)
    return base + tz_shift

# Range duration and bucket aggregation interval mapping
RANGE_CONFIG = {
    "1h": {"delta": timedelta(hours=1), "bucket_seconds": 60},      # 60 points
    "6h": {"delta": timedelta(hours=6), "bucket_seconds": 300},     # 72 points
    "24h": {"delta": timedelta(hours=24), "bucket_seconds": 900},   # 96 points
    "7d": {"delta": timedelta(days=7), "bucket_seconds": 3600},     # 168 points
    "30d": {"delta": timedelta(days=30), "bucket_seconds": 14400},  # 180 points
}


def format_rate(bps: float) -> str:
    """Format bits per second into human-readable string."""
    if bps >= 1_000_000_000:
        return f"{bps / 1_000_000_000:.2f} Gbps"
    elif bps >= 1_000_000:
        return f"{bps / 1_000_000:.2f} Mbps"
    elif bps >= 1_000:
        return f"{bps / 1_000:.1f} Kbps"
    return f"{bps:.0f} bps"


class MetricsCollector:
    """Collects, aggregates, and serves time-series performance and interface traffic metrics."""

    #: How much raw sample detail the graph ranges need. `30d` is the longest
    #: range offered, so nothing older than this can ever be displayed.
    RETENTION_DAYS = 30
    #: Wall-clock spacing between retention passes. The prune is a range delete
    #: over the two largest tables, and SQLite has one writer at a time.
    PRUNE_INTERVAL_SECONDS = 3600.0
    #: How long a hardware alert stays armed once fired, so a board that sits
    #: above the line is reported once per period rather than once per sample.
    ALERT_REARM_SECONDS = 1800.0

    def __init__(self) -> None:
        # Last failure text per router, so a fault that repeats every tick is
        # reported once instead of once per poll. See _note_collect_failure.
        self._failures: Dict[int, str] = {}
        # Monotonic instant of the last CPU / temperature alert per
        # (router, kind) - see check_hardware_alerts.
        self._hardware_alerts: Dict[tuple, float] = {}
        # Monotonic deadline for the next retention pass; 0 prunes on the first
        # tick after start, which is what catches up after the app was down.
        self._next_prune_at = 0.0

    def _note_collect_failure(self, router_id: int, error: Exception) -> None:
        """Report a collection fault on state change, not on every tick.

        This was a bare ``logger.debug``, which meant a router that stopped
        reporting metrics for seventeen hours left no trace at the default log
        level - the hole only ever showed up as a strange graph. Warning once per
        distinct fault keeps the incident in the log; the repetition is what made
        warning-level spam unacceptable here.
        """
        text = f"{type(error).__name__}: {error}"
        if self._failures.get(router_id) == text:
            return
        self._failures[router_id] = text
        logger.warning(f"Metrics collection failing for router {router_id}: {text}")

    def _note_collect_success(self, router_id: int) -> None:
        """Close the fault opened by the last _note_collect_failure, if any."""
        if self._failures.pop(router_id, None) is not None:
            logger.info(f"Metrics collection recovered for router {router_id}")

    async def check_hardware_alerts(
        self,
        session: AsyncSession,
        router_id: int,
        cpu_load: float,
        temperature: Optional[float],
    ) -> List[dict]:
        """Raise one alert per crossing of the CPU / temperature line, and return them.

        The thresholds were configurable for a long time without anything reading
        them: the Settings dialog had a temperature field, the model's own comment
        named a ``high_cpu`` alert type, and neither path ever compared a sample to
        a limit. They are read from ``app_settings`` now (see
        :mod:`backend.app.core.tunables`), with the environment as the fallback.

        Alerts are edge-triggered and re-arm after
        ``ALERT_REARM_SECONDS`` below the line, so a board that sits at 85 °C for
        six hours produces one entry, not two thousand, while a temperature that
        falls and climbs again is reported again.

        The returned rows are what the caller may push to Telegram: this service
        has no reference to the bot, and importing it would turn a leaf module
        into a cycle.
        """
        from backend.app.core.tunables import alert_cpu_threshold, alert_temp_threshold_celsius
        from backend.app.db.models import AlertLog

        fired: List[dict] = []
        cpu_limit = await alert_cpu_threshold(session)
        temp_limit = await alert_temp_threshold_celsius(session, router_id)
        now = time.monotonic()

        checks = []
        if cpu_load >= cpu_limit:
            checks.append(("high_cpu", f"CPU load {cpu_load:.0f}% reached the {cpu_limit}% alert level",
                           {"cpu_load": cpu_load, "threshold": cpu_limit}))
        if temperature is not None and temperature >= temp_limit:
            checks.append(("high_temperature",
                           f"Board temperature {temperature:.0f}°C reached the {temp_limit:.0f}°C alert level",
                           {"temperature": temperature, "threshold": temp_limit}))

        for kind, message, payload in checks:
            key = (router_id, kind)
            last = self._hardware_alerts.get(key)
            if last is not None and now - last < self.ALERT_REARM_SECONDS:
                continue
            self._hardware_alerts[key] = now
            session.add(AlertLog(router_id=router_id, alert_type=kind, message=message,
                                 metadata_payload=payload))
            fired.append({"alert_type": kind, "message": message})
        # Not committed here: the caller is already inside one transaction with
        # the sample that triggered the alert, and splitting them would let the
        # alert be recorded for a sample that then fails to store.
        return fired

    async def collect_and_store(
        self,
        session: AsyncSession,
        router_id: int,
        client: RouterOSClient,
        resource=None,
    ) -> None:
        """Fetch live system resource and interface rates and append to SQLite time-series.

        ``resource`` lets a caller that already read ``/system/resource`` - the
        background tick does, for the uptime that tells it whether the router
        rebooted - hand the answer over instead of asking the router twice.

        Returns any hardware alerts this sample crossed, so the caller can push
        them to Telegram; the collector stores them and stays free of the bot.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        fired: List[dict] = []

        try:
            # 1. Collect System Resource & Health
            res = resource if resource is not None else await client.get_system_resource()
            health = await client.get_system_health()

            mem_used = max(0, res.total_memory - res.free_memory)
            mem_pct = (mem_used / res.total_memory * 100.0) if res.total_memory > 0 else 0.0

            sys_metric = SystemMetric(
                router_id=router_id,
                cpu_load=float(res.cpu_load),
                memory_used_bytes=mem_used,
                memory_total_bytes=res.total_memory,
                memory_usage_pct=round(mem_pct, 1),
                temperature=health.temperature,
                voltage=health.voltage,
                timestamp=now
            )
            session.add(sys_metric)

            # 2. Collect Interface Counters and Live Traffic Rates
            ifaces = await client.get_interfaces()
            running_names = []
            for i in ifaces:
                i_name = getattr(i, "name", i.get("name") if isinstance(i, dict) else None)
                i_running = getattr(i, "running", i.get("running") if isinstance(i, dict) else False)
                i_disabled = getattr(i, "disabled", i.get("disabled") if isinstance(i, dict) else False)
                if i_name and i_running and not i_disabled:
                    running_names.append(i_name)

            rates_map = {}
            if running_names:
                rates_data = await client.monitor_interface_traffic(running_names)
                rates_map = {r.get("name"): r for r in rates_data if isinstance(r, dict) and "name" in r}

            for iface in ifaces:
                name = getattr(iface, "name", iface.get("name") if isinstance(iface, dict) else "")
                if not name:
                    continue
                rate_info = rates_map.get(name, {})
                rx_rate = rate_info.get("rx_bits_per_second", 0.0)
                tx_rate = rate_info.get("tx_bits_per_second", 0.0)
                rx_byte = getattr(iface, "rx_byte", iface.get("rx_byte", 0) if isinstance(iface, dict) else 0)
                tx_byte = getattr(iface, "tx_byte", iface.get("tx_byte", 0) if isinstance(iface, dict) else 0)

                iface_metric = InterfaceMetric(
                    router_id=router_id,
                    interface_name=name,
                    rx_rate_bps=rx_rate,
                    tx_rate_bps=tx_rate,
                    rx_bytes_total=rx_byte,
                    tx_bytes_total=tx_byte,
                    timestamp=now
                )
                session.add(iface_metric)

            # 3. Thresholds. Done here rather than in a second loop because the
            #    sample in front of us is the evidence: a CPU that spikes between
            #    two polls is gone by the time anything else looks at it.
            fired = await self.check_hardware_alerts(
                session, router_id, float(res.cpu_load), health.temperature
            )

            # 4. Retention Cleanup. Once an hour, not every tick: this is a
            # range delete over the two largest tables in the database, and on
            # the router's flash it is exactly the kind of statement that keeps
            # SQLite's single write lock - which every other worker then times
            # out on. Samples age in batches of 500 rows per transaction.
            await self._prune_expired(session, now)

            await session.commit()
            self._note_collect_success(router_id)
        except Exception as e:
            self._note_collect_failure(router_id, e)
        return fired

    async def _prune_expired(self, session: AsyncSession, now: datetime) -> int:
        """Drop samples past the retention window, at most once per interval.

        ``RETENTION_DAYS`` of detail is what the graph ranges need; anything
        older is gone regardless of how long the app has been down, because the
        cutoff is recomputed from the clock rather than from a stored cursor. The
        interval is tracked on the monotonic clock so an NTP step - which this
        device did once, moving the clock back by 98 minutes - cannot either skip
        a prune or make one run on every tick.

        Returns the number of deleted rows so a caller (or a test) can see that
        the throttle worked.
        """
        if time.monotonic() < self._next_prune_at:
            return 0
        self._next_prune_at = time.monotonic() + self.PRUNE_INTERVAL_SECONDS
        cutoff = now - timedelta(days=self.RETENTION_DAYS)
        deleted = await delete_older_than(session, InterfaceMetric.__table__, cutoff)
        deleted += await delete_older_than(session, SystemMetric.__table__, cutoff)
        if deleted:
            logger.info(f"Retention prune removed {deleted} sample(s) older than {cutoff:%Y-%m-%d %H:%M}")
        return deleted

    async def get_system_history(self, session: AsyncSession, router_id: Optional[int], range_key: str = "1h") -> SystemMetricsResponse:
        """Fetch system metric history: the bucket mean plus its worst case.

        Averaging a bucket on its own is what made the long ranges read as flat
        lines - a one-minute CPU stall inside a four-hour bucket lands on the
        chart as a couple of percent. So every bucket carries the peak alongside
        the mean and the graph draws both.

        The aggregation runs in SQL deliberately. Raw samples are one row per
        interface per poll, so a 30-day window is over a million rows; pulling
        them through the ORM to average them here made every refresh of the
        chart read the whole range. Grouping in the database returns one row per
        bucket instead.
        """
        cfg = RANGE_CONFIG.get(range_key, RANGE_CONFIG["1h"])
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        start_time = now - cfg["delta"]
        bucket_sec = cfg["bucket_seconds"]

        filters = [SystemMetric.timestamp >= start_time]
        if router_id:
            filters.append(SystemMetric.router_id == router_id)

        bucket = _time_bucket(SystemMetric.timestamp, bucket_sec).label("bucket")
        stmt = (
            select(
                bucket,
                func.max(_epoch_seconds(SystemMetric.timestamp)).label("ts_epoch"),
                func.avg(SystemMetric.cpu_load).label("cpu_avg"),
                func.max(SystemMetric.cpu_load).label("cpu_peak"),
                func.avg(SystemMetric.memory_usage_pct).label("ram_avg"),
                func.avg(SystemMetric.memory_used_bytes).label("ram_used_avg"),
                func.max(SystemMetric.memory_total_bytes).label("ram_total"),
                func.avg(SystemMetric.temperature).label("temp_avg"),
                func.max(SystemMetric.temperature).label("temp_peak"),
                func.avg(SystemMetric.voltage).label("volt_avg"),
                func.min(SystemMetric.voltage).label("volt_min"),
                func.max(SystemMetric.voltage).label("volt_peak"),
            )
            .where(*filters)
            .group_by(bucket)
            .order_by(bucket)
        )
        rows = (await session.execute(stmt)).all()

        if not rows:
            return SystemMetricsResponse(range=range_key, points=[], bucket_seconds=bucket_sec)

        # Samples are stored naive-UTC; the chart axis must read in the router's
        # local wall clock, like the header does. Shift each point out by the
        # router's offset before it leaves here.
        tz_shift = timedelta(minutes=await get_router_offset(session, router_id) or 0)

        points: List[SystemMetricPoint] = []
        for r in rows:
            points.append(SystemMetricPoint(
                timestamp=_stamp_from_epoch(r.ts_epoch, tz_shift),
                cpu_load=round(r.cpu_avg, 1),
                cpu_peak=round(r.cpu_peak, 1),
                memory_usage_pct=round(r.ram_avg, 1),
                memory_used_mb=round(r.ram_used_avg / (1024 * 1024), 1),
                memory_total_mb=round(r.ram_total / (1024 * 1024), 1),
                temperature=round(r.temp_avg, 1) if r.temp_avg is not None else None,
                temperature_peak=round(r.temp_peak, 1) if r.temp_peak is not None else None,
                voltage=round(r.volt_avg, 1) if r.volt_avg is not None else None,
                voltage_min=round(r.volt_min, 1) if r.volt_min is not None else None,
                voltage_max=round(r.volt_peak, 1) if r.volt_peak is not None else None,
            ))

        # "Current" is the newest single sample, not the last bucket: on the
        # 30-day range a bucket mean is a four-hour average and would be labelled
        # as live.
        latest = (await session.execute(
            select(SystemMetric)
            .where(*filters)
            .order_by(SystemMetric.timestamp.desc())
            .limit(1)
        )).scalar_one()

        return SystemMetricsResponse(
            range=range_key,
            points=points,
            current_cpu=latest.cpu_load,
            current_ram_pct=latest.memory_usage_pct,
            current_temp=latest.temperature,
            current_voltage=latest.voltage,
            bucket_seconds=bucket_sec
        )

    async def get_interface_history(
        self,
        session: AsyncSession,
        router_id: Optional[int],
        range_key: str = "1h",
        selected_interfaces: Optional[List[str]] = None
    ) -> InterfaceHistoryResponse:
        """Fetch bandwidth history summed across the selected interfaces.

        Each bucket carries the mean of the summed series *and* its peak. The sum
        is taken per sample first, so the peak is the busiest moment on the
        monitored group rather than the tallest spike of one interface stacked on
        the tallest spike of another at a different moment.
        """
        cfg = RANGE_CONFIG.get(range_key, RANGE_CONFIG["1h"])
        bucket_sec = cfg["bucket_seconds"]

        if selected_interfaces is not None and len(selected_interfaces) == 0:
            return InterfaceHistoryResponse(range=range_key, interfaces=[], is_summed=True, points=[], current_rx_bps=0.0, current_tx_bps=0.0, bucket_seconds=bucket_sec)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        start_time = now - cfg["delta"]

        filters = [InterfaceMetric.timestamp >= start_time]
        if router_id:
            filters.append(InterfaceMetric.router_id == router_id)
        if selected_interfaces is not None:
            filters.append(InterfaceMetric.interface_name.in_(selected_interfaces))

        # Which interfaces the caller ends up looking at: what was asked for, or
        # everything that reported traffic when nothing was specified.
        if selected_interfaces is None:
            interfaces_out = list((await session.execute(
                select(InterfaceMetric.interface_name).where(*filters).distinct()
            )).scalars().all())
        else:
            interfaces_out = list(selected_interfaces)

        # Inner level: one row per sample instant, holding the group's summed rate.
        # All of a tick's interface rows share a timestamp, so grouping by it
        # reconstructs the instantaneous total for the monitored set.
        per_sample = (
            select(
                func.max(_epoch_seconds(InterfaceMetric.timestamp)).label("ts_epoch"),
                func.sum(InterfaceMetric.rx_rate_bps).label("rx"),
                func.sum(InterfaceMetric.tx_rate_bps).label("tx"),
            )
            .where(*filters)
            .group_by(InterfaceMetric.timestamp)
            .subquery()
        )

        # Outer level: collapse those samples into display buckets.
        bucket = cast(per_sample.c.ts_epoch / bucket_sec, Integer).label("bucket")
        stmt = (
            select(
                bucket,
                func.max(per_sample.c.ts_epoch).label("ts_epoch"),
                func.avg(per_sample.c.rx).label("rx_avg"),
                func.max(per_sample.c.rx).label("rx_peak"),
                func.avg(per_sample.c.tx).label("tx_avg"),
                func.max(per_sample.c.tx).label("tx_peak"),
            )
            .group_by(bucket)
            .order_by(bucket)
        )
        rows = (await session.execute(stmt)).all()

        if not rows:
            return InterfaceHistoryResponse(range=range_key, interfaces=interfaces_out, is_summed=True, points=[], current_rx_bps=0.0, current_tx_bps=0.0, bucket_seconds=bucket_sec)

        # Samples are naive-UTC; the axis reads in the router's local wall clock.
        tz_shift = timedelta(minutes=await get_router_offset(session, router_id) or 0)

        points: List[InterfaceRatePoint] = []
        for r in rows:
            total_rx_bps = float(r.rx_avg)
            total_tx_bps = float(r.tx_avg)
            peak_rx_bps = float(r.rx_peak)
            peak_tx_bps = float(r.tx_peak)
            points.append(InterfaceRatePoint(
                timestamp=_stamp_from_epoch(r.ts_epoch, tz_shift),
                rx_rate_bps=round(total_rx_bps, 1),
                tx_rate_bps=round(total_tx_bps, 1),
                rx_rate_formatted=format_rate(total_rx_bps),
                tx_rate_formatted=format_rate(total_tx_bps),
                rx_peak_bps=round(peak_rx_bps, 1),
                tx_peak_bps=round(peak_tx_bps, 1),
                rx_peak_formatted=format_rate(peak_rx_bps),
                tx_peak_formatted=format_rate(peak_tx_bps),
            ))

        # "Current" is the newest sample, not the last bucket - on the 30-day
        # range a bucket mean is a four-hour average wearing a live label.
        current_rx_bps = current_tx_bps = 0.0
        latest_ts = (await session.execute(
            select(InterfaceMetric.timestamp)
            .where(*filters)
            .order_by(InterfaceMetric.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()
        if latest_ts is not None:
            summed = (await session.execute(
                select(
                    func.sum(InterfaceMetric.rx_rate_bps),
                    func.sum(InterfaceMetric.tx_rate_bps),
                )
                .where(*filters, InterfaceMetric.timestamp == latest_ts)
            )).one()
            current_rx_bps = float(summed[0] or 0.0)
            current_tx_bps = float(summed[1] or 0.0)

        return InterfaceHistoryResponse(
            range=range_key,
            interfaces=interfaces_out,
            is_summed=True,
            points=points,
            current_rx_bps=round(current_rx_bps, 1),
            current_tx_bps=round(current_tx_bps, 1),
            bucket_seconds=bucket_sec
        )


metrics_collector = MetricsCollector()
