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
async def test_routerboard_reports_the_soc_and_is_cached(mock_settings):
    """`/system/resource` only knows the instruction set on MikroTik hardware;
    the SoC/platform name comes from `/system/routerboard`."""
    client = RouterOSClient(mock_settings)

    with respx.mock(base_url="https://192.168.88.1:443/rest") as respx_mock:
        route = respx_mock.get("/system/routerboard").respond(
            200,
            json={
                "routerboard": "true",
                "model": "MA53UG+HbeH",
                "serial-number": "HMS0BD7KNDP",
                "firmware-type": "ipq5300",
                "current-firmware": "7.25_ab508",
                "upgrade-firmware": "7.25_ab508",
            },
        )

        info = await client.get_routerboard()
        assert info.is_routerboard is True
        assert info.firmware_type == "ipq5300"      # the SoC - the real CPU label
        assert info.model == "MA53UG+HbeH"
        assert info.serial_number == "HMS0BD7KNDP"

        # Static data: a second call must not hit the router again.
        again = await client.get_routerboard()
        assert again.firmware_type == "ipq5300"
        assert route.call_count == 1


@pytest.mark.asyncio
async def test_routerboard_absent_on_a_non_routerboard_install(mock_settings):
    """A CHR / x86 box has no RouterBOARD; the caller falls back to res.cpu."""
    client = RouterOSClient(mock_settings)

    with respx.mock(base_url="https://192.168.88.1:443/rest") as respx_mock:
        respx_mock.get("/system/routerboard").respond(200, json={"routerboard": "false"})

        info = await client.get_routerboard()
        assert info.is_routerboard is False
        assert info.firmware_type is None


@pytest.mark.asyncio
async def test_routerboard_failure_is_swallowed_and_not_retried_every_tick(mock_settings):
    client = RouterOSClient(mock_settings)

    with respx.mock(base_url="https://192.168.88.1:443/rest") as respx_mock:
        route = respx_mock.get("/system/routerboard").respond(500, text="boom")

        info = await client.get_routerboard()
        assert info.is_routerboard is False

        await client.get_routerboard()
        assert route.call_count == 1  # the empty result is cached too


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


@pytest.mark.asyncio
async def test_get_wan_interfaces_reads_the_default_route(mock_settings):
    """WAN is whatever carries 0.0.0.0/0, resolved from the next hop rather
    than guessed from the name: a '%iface' suffix for a routed link, a bare
    interface for PPPoE, and dual-WAN yields both. Non-default and inactive
    routes are ignored."""
    client = RouterOSClient(mock_settings)

    with respx.mock(base_url="https://192.168.88.1:443/rest") as respx_mock:
        respx_mock.get("/ip/route").respond(200, json=[
            {"dst-address": "0.0.0.0/0", "gateway": "10.0.0.1",
             "immediate-gw": "10.0.0.1%ether1", "active": "true"},
            {"dst-address": "0.0.0.0/0", "gateway": "pppoe-out1",
             "immediate-gw": "pppoe-out1", "active": "true"},
            {"dst-address": "0.0.0.0/0", "gateway": "10.9.9.1%ether5", "active": "false"},
            {"dst-address": "192.168.88.0/24", "gateway": "bridge", "active": "true"},
        ])
        wan = await client.get_wan_interfaces()

    assert wan == ["ether1", "pppoe-out1"]


@pytest.mark.asyncio
async def test_get_wan_interfaces_empty_when_no_default_route(mock_settings):
    client = RouterOSClient(mock_settings)
    with respx.mock(base_url="https://192.168.88.1:443/rest") as respx_mock:
        respx_mock.get("/ip/route").respond(200, json=[
            {"dst-address": "192.168.88.0/24", "gateway": "bridge", "active": "true"},
        ])
        assert await client.get_wan_interfaces() == []
