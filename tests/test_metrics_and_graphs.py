
import logging
from datetime import datetime, timezone

import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.core.config import Settings
from backend.app.db.models import Base, InterfaceMetric, SystemMetric
from backend.app.services.metrics_collector import RANGE_CONFIG, MetricsCollector
from backend.app.services.routeros import RouterOSClient


def _naive_utc(epoch_seconds: float) -> datetime:
    """The stored form of a sample instant: naive UTC, no offset attached."""
    return datetime.fromtimestamp(epoch_seconds, timezone.utc).replace(tzinfo=None)


def _bucket_edge(range_key: str) -> int:
    """Start epoch of the bucket the clock is in right now.

    Aligning samples to a bucket edge means the assertions below cannot be broken
    by a sample happening to straddle one.
    """
    bucket_sec = RANGE_CONFIG[range_key]["bucket_seconds"]
    return int(datetime.now(timezone.utc).timestamp()) // bucket_sec * bucket_sec


@pytest.fixture
def mock_settings():
    return Settings(
        ROUTEROS_HOST="192.168.88.1",
        ROUTEROS_PORT=443,
        ROUTEROS_USE_SSL=True,
        ROUTEROS_SSL_VERIFY=False,
        ROUTEROS_USER="admin",
        ROUTEROS_PASSWORD="password"
    )


@pytest.mark.asyncio
async def test_metrics_collection_and_downsampling(mock_settings):
    client = RouterOSClient(mock_settings)
    collector = MetricsCollector()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    with respx.mock(base_url="https://192.168.88.1:443/rest") as respx_mock:
        # Mock /system/resource
        respx_mock.get("/system/resource").respond(
            200,
            json=[{
                "cpu-load": "15",
                "free-memory": "100000000",
                "total-memory": "256000000",
                "uptime": "2d",
                "cpu-count": "4"
            }]
        )
        # Mock /system/health
        respx_mock.get("/system/health").respond(
            200,
            json=[
                {"name": "cpu-temperature", "value": "48"},
                {"name": "voltage", "value": "24.2"}
            ]
        )
        # Mock /interface
        respx_mock.get("/interface").respond(
            200,
            json=[
                {
                    ".id": "*1",
                    "name": "ether1",
                    "type": "ether",
                    "running": "true",
                    "disabled": "false",
                    "rx-byte": "50000000",
                    "tx-byte": "10000000"
                },
                {
                    ".id": "*2",
                    "name": "ether2",
                    "type": "ether",
                    "running": "true",
                    "disabled": "false",
                    "rx-byte": "20000000",
                    "tx-byte": "5000000"
                }
            ]
        )
        # Mock /interface/monitor-traffic
        respx_mock.post("/interface/monitor-traffic").respond(
            200,
            json=[
                {
                    "name": "ether1",
                    "rx-bits-per-second": "15000000",  # 15 Mbps
                    "tx-bits-per-second": "5000000"    # 5 Mbps
                },
                {
                    "name": "ether2",
                    "rx-bits-per-second": "2500000",   # 2.5 Mbps
                    "tx-bits-per-second": "1000000"    # 1.0 Mbps
                }
            ]
        )

        async with session_factory() as session:
            # 1. Collect and store metrics
            await collector.collect_and_store(session, router_id=1, client=client)

            # Check SystemMetric saved
            sys_metrics = (await session.execute(select(SystemMetric))).scalars().all()
            assert len(sys_metrics) == 1
            assert sys_metrics[0].cpu_load == 15.0
            assert sys_metrics[0].temperature == 48.0
            assert sys_metrics[0].voltage == 24.2

            # Check InterfaceMetric saved
            iface_metrics = (await session.execute(select(InterfaceMetric))).scalars().all()
            assert len(iface_metrics) == 2

            # 2. Query System History
            sys_history = await collector.get_system_history(session, router_id=1, range_key="1h")
            assert len(sys_history.points) == 1
            assert sys_history.points[0].cpu_load == 15.0
            assert sys_history.current_temp == 48.0

            # 3. Query Interface History with Multi-Interface Sum
            # Summing ether1 (15M rx) + ether2 (2.5M rx) = 17.5M rx
            iface_history = await collector.get_interface_history(
                session,
                router_id=1,
                range_key="1h",
                selected_interfaces=["ether1", "ether2"]
            )
            assert len(iface_history.points) == 1
            assert iface_history.points[0].rx_rate_bps == 17500000.0  # 17.5 Mbps
            assert iface_history.points[0].tx_rate_bps == 6000000.0   # 6.0 Mbps

    await engine.dispose()


@pytest.mark.asyncio
async def test_short_burst_keeps_its_height_on_the_long_ranges():
    """A burst shorter than its bucket must not be averaged out of the chart.

    On the 30-day range four hours of samples collapse into one point, so a
    200 Mbps minute reached the graph as under a Mbps and the traffic the router
    really carried was simply not there. Each bucket now reports its peak next to
    its mean, and the mean keeps its old field so nothing downstream moves.
    """
    collector = MetricsCollector()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    edge = _bucket_edge("30d")
    # One bucket, three samples ten seconds apart: idle, then ether1 at 200 Mbps,
    # then ether2 at 50 Mbps.
    samples = [(0.0, 0.0), (200_000_000.0, 0.0), (0.0, 50_000_000.0)]

    async with session_factory() as session:
        for i, (ether1_rx, ether2_rx) in enumerate(samples):
            ts = _naive_utc(edge + i * 10)
            for name, rx in (("ether1", ether1_rx), ("ether2", ether2_rx)):
                session.add(InterfaceMetric(
                    router_id=1, interface_name=name,
                    rx_rate_bps=rx, tx_rate_bps=0.0,
                    rx_bytes_total=0, tx_bytes_total=0, timestamp=ts,
                ))
        await session.commit()

        history = await collector.get_interface_history(
            session, router_id=1, range_key="30d", selected_interfaces=["ether1", "ether2"]
        )
    await engine.dispose()

    assert len(history.points) == 1
    point = history.points[0]
    # The graph needs the nominal bucket width to tell an empty bucket apart from
    # an outage, otherwise it draws a ramp across the hours nobody measured.
    assert history.bucket_seconds == RANGE_CONFIG["30d"]["bucket_seconds"]

    # The mean of the summed series: (0 + 200M + 50M) / 3.
    assert point.rx_rate_bps == pytest.approx(83_333_333.3, rel=1e-4)
    # And the burst itself, which is what the old chart threw away.
    assert point.rx_peak_bps == 200_000_000.0
    assert point.rx_peak_formatted == "200.00 Mbps"
    # The two bursts happened at different instants. Summing the raw samples
    # before taking the peak keeps the graph honest: the group never ran at
    # 250 Mbps, so it must not be drawn cresting there.
    assert point.rx_peak_bps < 250_000_000.0

    # The live figure beside the chart is the newest sample, not a four-hour
    # average wearing a "current" label.
    assert history.current_rx_bps == 50_000_000.0


@pytest.mark.asyncio
async def test_system_buckets_carry_cpu_and_temperature_peaks():
    """CPU stalls and heat spikes are erased by bucket averaging just as badly."""
    collector = MetricsCollector()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    edge = _bucket_edge("30d")
    async with session_factory() as session:
        # Previous bucket, no sensors reading at all: hardware without a thermal
        # probe, or a tick where /system/health failed.
        session.add(SystemMetric(
            router_id=1, cpu_load=0.0,
            memory_used_bytes=0, memory_total_bytes=1, memory_usage_pct=0.0,
            temperature=None, voltage=None, timestamp=_naive_utc(edge - 60),
        ))
        for i, (cpu, temp, volt) in enumerate(
            [(2.0, 40.0, 24.0), (100.0, 70.0, 23.1), (3.0, 40.0, 24.2)]
        ):
            session.add(SystemMetric(
                router_id=1, cpu_load=cpu,
                memory_used_bytes=100 * 1024 * 1024,
                memory_total_bytes=256 * 1024 * 1024,
                memory_usage_pct=39.1,
                temperature=temp, voltage=volt, timestamp=_naive_utc(edge + i * 10),
            ))
        await session.commit()

        history = await collector.get_system_history(session, router_id=1, range_key="30d")
    await engine.dispose()

    assert len(history.points) == 2
    silent, busy = history.points

    # Averaging nothing stays nothing rather than becoming a reading.
    assert silent.temperature is None and silent.temperature_peak is None
    assert silent.voltage is None and silent.voltage_min is None and silent.voltage_max is None

    assert busy.cpu_load == pytest.approx(35.0)   # (2 + 100 + 3) / 3
    assert busy.cpu_peak == 100.0                 # the stall the mean hid
    assert busy.temperature == pytest.approx(50.0)
    assert busy.temperature_peak == 70.0
    assert busy.voltage == pytest.approx(23.8)    # (24.0 + 23.1 + 24.2) / 3
    assert busy.voltage_min == 23.1               # a brownout is the dip, not the mean
    assert busy.voltage_max == 24.2
    assert busy.memory_usage_pct == pytest.approx(39.1)
    assert busy.memory_used_mb == pytest.approx(100.0)
    assert busy.memory_total_mb == pytest.approx(256.0)

    assert history.current_cpu == 3.0
    assert history.current_temp == 40.0


def test_a_router_that_stops_reporting_leaves_a_trace(caplog):
    """Collection faults must surface once each - not silently, and not per tick.

    The collector runs every few seconds per router. At debug level a seventeen
    hour outage produced no log line at all; at plain warning it would produce
    thousands. Both are useless, so the state is tracked and only changes are
    reported.
    """
    collector = MetricsCollector()

    with caplog.at_level(logging.INFO, logger="mikroman.metrics_collector"):
        collector._note_collect_failure(7, RuntimeError("connect timeout"))
        collector._note_collect_failure(7, RuntimeError("connect timeout"))
        collector._note_collect_failure(7, RuntimeError("404: no such menu"))
        collector._note_collect_success(7)
        collector._note_collect_success(7)  # nothing to close a second time

    lines = [(r.levelname, r.getMessage()) for r in caplog.records if r.name == "mikroman.metrics_collector"]
    assert len(lines) == 3, f"expected two faults plus one recovery, got {lines}"
    assert lines[0][0] == "WARNING" and "connect timeout" in lines[0][1]
    assert lines[1][1].endswith("404: no such menu")
    assert lines[2] == ("INFO", "Metrics collection recovered for router 7")
