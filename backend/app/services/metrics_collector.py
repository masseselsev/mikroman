import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import InterfaceMetric, SystemMetric
from backend.app.schemas.metrics import (
    InterfaceHistoryResponse,
    InterfaceRatePoint,
    SystemMetricPoint,
    SystemMetricsResponse,
)
from backend.app.services.router_time import get_router_offset
from backend.app.services.routeros import RouterOSClient

logger = logging.getLogger("mikroman.metrics_collector")


def _utc_epoch(dt: datetime) -> float:
    """Seconds since the epoch for a naive-UTC timestamp.

    ``datetime.timestamp()`` on a naive datetime assumes the *system* zone, so
    on a non-UTC host the bucket index would drift; pin it to UTC explicitly.
    """
    return dt.replace(tzinfo=timezone.utc).timestamp()

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

    async def collect_and_store(self, session: AsyncSession, router_id: int, client: RouterOSClient) -> None:
        """Fetch live system resource and interface rates and append to SQLite time-series."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        try:
            # 1. Collect System Resource & Health
            res = await client.get_system_resource()
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

            # 3. Retention Cleanup (Prune records older than 30 days)
            cutoff = now - timedelta(days=30)
            await session.execute(delete(SystemMetric).where(SystemMetric.timestamp < cutoff))
            await session.execute(delete(InterfaceMetric).where(InterfaceMetric.timestamp < cutoff))

            await session.commit()
        except Exception as e:
            logger.debug(f"Metrics collection failed for router {router_id}: {e}")

    async def get_system_history(self, session: AsyncSession, router_id: Optional[int], range_key: str = "1h") -> SystemMetricsResponse:
        """Fetch aggregated system metrics history with downsampled bucket points."""
        cfg = RANGE_CONFIG.get(range_key, RANGE_CONFIG["1h"])
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        start_time = now - cfg["delta"]

        query = select(SystemMetric).where(SystemMetric.timestamp >= start_time)
        if router_id:
            query = query.where(SystemMetric.router_id == router_id)
        query = query.order_by(SystemMetric.timestamp.asc())

        result = await session.execute(query)
        rows = result.scalars().all()

        if not rows:
            return SystemMetricsResponse(range=range_key, points=[])

        # Samples are stored naive-UTC; the chart axis must read in the router's
        # local wall clock, like the header does. Shift each point out by the
        # router's offset before it leaves here.
        tz_shift = timedelta(minutes=await get_router_offset(session, router_id) or 0)

        # Bucket aggregation
        bucket_sec = cfg["bucket_seconds"]
        buckets: Dict[int, List[SystemMetric]] = {}
        for r in rows:
            bucket_idx = int(_utc_epoch(r.timestamp)) // bucket_sec
            buckets.setdefault(bucket_idx, []).append(r)

        points: List[SystemMetricPoint] = []
        for b_idx in sorted(buckets.keys()):
            items = buckets[b_idx]
            avg_cpu = sum(i.cpu_load for i in items) / len(items)
            avg_ram_pct = sum(i.memory_usage_pct for i in items) / len(items)
            avg_ram_used = sum(i.memory_used_bytes for i in items) / len(items) / (1024 * 1024)
            avg_ram_total = (items[-1].memory_total_bytes) / (1024 * 1024)
            temps = [i.temperature for i in items if i.temperature is not None]
            avg_temp = (sum(temps) / len(temps)) if temps else None
            volts = [i.voltage for i in items if i.voltage is not None]
            avg_volt = (sum(volts) / len(volts)) if volts else None

            points.append(SystemMetricPoint(
                timestamp=items[-1].timestamp + tz_shift,
                cpu_load=round(avg_cpu, 1),
                memory_usage_pct=round(avg_ram_pct, 1),
                memory_used_mb=round(avg_ram_used, 1),
                memory_total_mb=round(avg_ram_total, 1),
                temperature=round(avg_temp, 1) if avg_temp is not None else None,
                voltage=round(avg_volt, 1) if avg_volt is not None else None
            ))

        latest = rows[-1]
        return SystemMetricsResponse(
            range=range_key,
            points=points,
            current_cpu=latest.cpu_load,
            current_ram_pct=latest.memory_usage_pct,
            current_temp=latest.temperature,
            current_voltage=latest.voltage
        )

    async def get_interface_history(
        self,
        session: AsyncSession,
        router_id: Optional[int],
        range_key: str = "1h",
        selected_interfaces: Optional[List[str]] = None
    ) -> InterfaceHistoryResponse:
        """Fetch aggregated bandwidth history summed across selected interfaces."""
        if selected_interfaces is not None and len(selected_interfaces) == 0:
            return InterfaceHistoryResponse(range=range_key, interfaces=[], is_summed=True, points=[], current_rx_bps=0.0, current_tx_bps=0.0)

        cfg = RANGE_CONFIG.get(range_key, RANGE_CONFIG["1h"])
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        start_time = now - cfg["delta"]

        query = select(InterfaceMetric).where(InterfaceMetric.timestamp >= start_time)
        if router_id:
            query = query.where(InterfaceMetric.router_id == router_id)
        if selected_interfaces is not None:
            query = query.where(InterfaceMetric.interface_name.in_(selected_interfaces))
        query = query.order_by(InterfaceMetric.timestamp.asc())

        result = await session.execute(query)
        rows = result.scalars().all()

        if not rows:
            return InterfaceHistoryResponse(range=range_key, interfaces=selected_interfaces or [], is_summed=True, points=[], current_rx_bps=0.0, current_tx_bps=0.0)

        # Samples are naive-UTC; the axis reads in the router's local wall clock.
        tz_shift = timedelta(minutes=await get_router_offset(session, router_id) or 0)

        bucket_sec = cfg["bucket_seconds"]
        # Group by bucket index -> list of metrics across selected interfaces
        buckets: Dict[int, Dict[str, List[InterfaceMetric]]] = {}
        for r in rows:
            b_idx = int(_utc_epoch(r.timestamp)) // bucket_sec
            buckets.setdefault(b_idx, {}).setdefault(r.interface_name, []).append(r)

        points: List[InterfaceRatePoint] = []
        for b_idx in sorted(buckets.keys()):
            iface_dict = buckets[b_idx]
            total_rx_bps = 0.0
            total_tx_bps = 0.0
            latest_time = None

            for iface_name, iface_rows in iface_dict.items():
                avg_rx = sum(r.rx_rate_bps for r in iface_rows) / len(iface_rows)
                avg_tx = sum(r.tx_rate_bps for r in iface_rows) / len(iface_rows)
                total_rx_bps += avg_rx
                total_tx_bps += avg_tx
                latest_time = iface_rows[-1].timestamp

            points.append(InterfaceRatePoint(
                timestamp=(latest_time or now) + tz_shift,
                rx_rate_bps=round(total_rx_bps, 1),
                tx_rate_bps=round(total_tx_bps, 1),
                rx_rate_formatted=format_rate(total_rx_bps),
                tx_rate_formatted=format_rate(total_tx_bps)
            ))

        latest_point = points[-1] if points else None
        return InterfaceHistoryResponse(
            range=range_key,
            interfaces=selected_interfaces or list({r.interface_name for r in rows}),
            is_summed=True,
            points=points,
            current_rx_bps=latest_point.rx_rate_bps if latest_point else 0.0,
            current_tx_bps=latest_point.tx_rate_bps if latest_point else 0.0
        )


metrics_collector = MetricsCollector()
