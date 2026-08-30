
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.core.config import Settings
from backend.app.db.models import Base, InterfaceMetric, SystemMetric
from backend.app.services.metrics_collector import MetricsCollector
from backend.app.services.routeros import RouterOSClient


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
