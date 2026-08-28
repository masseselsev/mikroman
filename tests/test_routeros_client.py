import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.core.config import Settings
from backend.app.db.models import Base
from backend.app.services.device_manager import DeviceManager
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
async def test_routeros_system_resource(mock_settings):
    client = RouterOSClient(mock_settings)

    with respx.mock(base_url="https://192.168.88.1:443/rest") as respx_mock:
        respx_mock.get("/system/resource").respond(
            200,
            json={
                "board-name": "hAP ax3",
                "version": "7.24",
                "cpu-load": "8",
                "free-memory": "536870912",
                "total-memory": "1073741824",
                "uptime": "5d 12:30:00",
                "cpu-count": "4",
                "architecture-name": "arm64"
            }
        )

        res = await client.get_system_resource()
        assert res.board_name == "hAP ax3"
        assert res.version == "7.24"
        assert res.cpu_load == 8
        assert res.free_memory == 536870912
        assert res.cpu_count == 4


@pytest.mark.asyncio
async def test_routeros_health_and_reboot(mock_settings):
    client = RouterOSClient(mock_settings)

    with respx.mock(base_url="https://192.168.88.1:443/rest") as respx_mock:
        respx_mock.get("/system/health").respond(
            200,
            json=[
                {"name": "board-temperature1", "value": "45"},
                {"name": "voltage", "value": "24.2"}
            ]
        )
        respx_mock.post("/system/reboot").respond(200, json={})

        health = await client.get_system_health()
        assert health.temperature == 45.0
        assert health.voltage == 24.2

        reboot_ok = await client.reboot_system()
        assert reboot_ok is True


@pytest.mark.asyncio
async def test_device_manager_sync(mock_settings):
    client = RouterOSClient(mock_settings)
    dev_mgr = DeviceManager(client)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    with respx.mock(base_url="https://192.168.88.1:443/rest") as respx_mock:
        respx_mock.get("/ip/dhcp-server/lease").respond(
            200,
            json=[
                {
                    ".id": "*1",
                    "address": "192.168.88.10",
                    "mac-address": "AC:DE:48:11:22:33",
                    "host-name": "Alex-MacBook",
                    "status": "bound"
                },
                {
                    ".id": "*2",
                    "address": "192.168.88.20",
                    "mac-address": "70:9E:29:AA:BB:CC",
                    "host-name": "PlayStation-5",
                    "status": "bound"
                }
            ]
        )
        respx_mock.get("/ip/arp").respond(
            200,
            json=[
                {"address": "192.168.88.10", "mac-address": "AC:DE:48:11:22:33", "interface": "ether2"},
                {"address": "192.168.88.20", "mac-address": "70:9E:29:AA:BB:CC", "interface": "wifi1"}
            ]
        )
        respx_mock.get("/interface/wifi/registration-table").respond(
            200,
            json=[
                {"mac-address": "70:9E:29:AA:BB:CC", "interface": "wifi1", "ssid": "Home-5G", "signal-strength": "-54"}
            ]
        )

        async with session_factory() as session:
            all_devices, newly_discovered = await dev_mgr.sync_devices_from_router(session)
            assert len(all_devices) == 2
            assert len(newly_discovered) == 2

            dev1 = next(d for d in all_devices if d.mac_address == "AC:DE:48:11:22:33")
            assert dev1.hostname == "Alex-MacBook"
            assert dev1.vendor == "Apple"
            assert dev1.last_interface == "ether2"

            dev2 = next(d for d in all_devices if d.mac_address == "70:9E:29:AA:BB:CC")
            assert dev2.last_wifi_signal == -54
            assert dev2.vendor == "Sony Interactive (PlayStation)"

    await engine.dispose()
