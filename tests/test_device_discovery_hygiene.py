"""Discovery hygiene: only real LAN clients may become managed devices.

The ARP table also contains the upstream ISP gateway, which sits on the WAN
interface. It was being ingested as an ordinary client, given a quarantine
Simple Queue and later an accounting rule, and shown in the dashboard as a
device belonging to the network.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import AppSetting, Base, Device
from backend.app.schemas.routeros import ARPTableEntry, DHCPLeaseDTO
from backend.app.services.device_manager import DeviceManager


class FakeRouter:
    def __init__(self, leases, arps):
        self._leases = leases
        self._arps = arps

    async def get_dhcp_leases(self):
        return self._leases

    async def get_arp_table(self):
        return self._arps

    async def get_wifi_registrations(self):
        return []


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_wan_side_arp_entries_are_not_ingested_as_devices(session):
    """The ISP gateway on the WAN interface must never become a managed device."""
    session.add(AppSetting(key="monitored_interfaces_1", value='["ether1"]'))
    await session.commit()

    router = FakeRouter(
        leases=[DHCPLeaseDTO(
            address="192.168.88.242", mac_address="74:4D:28:54:1C:5C",
            host_name="NamasT3k", status="bound",
        )],
        arps=[
            ARPTableEntry(address="192.168.88.242", mac_address="74:4D:28:54:1C:5C",
                          interface="bridge", complete=True),
            # upstream ISP gateway - lives on the WAN port
            ARPTableEntry(address="10.118.211.152", mac_address="B8:D4:BC:95:89:E1",
                          interface="ether1", complete=True),
        ],
    )

    mgr = DeviceManager(router, router_id=1)
    await mgr.sync_devices_from_router(session)

    macs = {d.mac_address for d in (await session.execute(Device.__table__.select())).fetchall()}
    assert "74:4D:28:54:1C:5C" in macs
    assert "B8:D4:BC:95:89:E1" not in macs, (
        "the WAN-side ISP gateway was ingested as a LAN device"
    )


@pytest.mark.asyncio
async def test_incomplete_arp_entry_does_not_mark_a_device_active(session):
    """A stale ARP entry is not evidence the device is online.

    RouterOS keeps unresolved entries with complete=false after a host leaves.
    Treating those as live showed offline phones as ACTIVE with a stale signal
    reading, and made their permanent 0 bps look like an accounting fault.
    """
    session.add(AppSetting(key="monitored_interfaces_1", value='["ether1"]'))
    await session.commit()

    router = FakeRouter(
        leases=[DHCPLeaseDTO(
            address="192.168.88.242", mac_address="74:4D:28:54:1C:5C",
            host_name="NamasT3k", status="bound",
        )],
        arps=[
            ARPTableEntry(address="192.168.88.242", mac_address="74:4D:28:54:1C:5C",
                          interface="bridge", complete=True),
            # phone that has left the network - entry lingers unresolved
            ARPTableEntry(address="192.168.88.241", mac_address="3E:A8:BC:29:5F:3B",
                          interface="bridge", complete=False),
        ],
    )

    mgr = DeviceManager(router, router_id=1)
    await mgr.sync_devices_from_router(session)

    rows = (await session.execute(Device.__table__.select())).fetchall()
    by_mac = {r.mac_address: r for r in rows}
    assert by_mac["74:4D:28:54:1C:5C"].is_active is True
    assert "3E:A8:BC:29:5F:3B" not in by_mac, (
        "an unresolved ARP entry must not create a phantom active device"
    )


@pytest.mark.asyncio
async def test_known_device_goes_inactive_when_its_arp_entry_goes_stale(session):
    """An existing device must flip to inactive once its ARP entry is unresolved."""
    session.add(AppSetting(key="monitored_interfaces_1", value='["ether1"]'))
    session.add(Device(
        mac_address="3E:A8:BC:29:5F:3B", ip_address="192.168.88.241",
        router_id=1, is_active=True, hostname="Pixel-7",
    ))
    await session.commit()

    router = FakeRouter(
        leases=[],
        arps=[ARPTableEntry(address="192.168.88.241", mac_address="3E:A8:BC:29:5F:3B",
                            interface="bridge", complete=False)],
    )
    mgr = DeviceManager(router, router_id=1)
    await mgr.sync_devices_from_router(session)

    rows = (await session.execute(Device.__table__.select())).fetchall()
    assert rows[0].is_active is False


@pytest.mark.asyncio
async def test_lan_clients_on_other_interfaces_are_still_ingested(session):
    """Filtering the WAN must not drop legitimate clients on other ports."""
    session.add(AppSetting(key="monitored_interfaces_1", value='["ether1"]'))
    await session.commit()

    router = FakeRouter(
        leases=[],
        arps=[
            ARPTableEntry(address="192.168.88.239", mac_address="74:D4:DD:C6:51:73",
                          interface="bridge", complete=True),
            ARPTableEntry(address="192.168.88.10", mac_address="00:11:22:33:44:55",
                          interface="ether3", complete=True),
        ],
    )

    mgr = DeviceManager(router, router_id=1)
    await mgr.sync_devices_from_router(session)

    rows = (await session.execute(Device.__table__.select())).fetchall()
    assert {r.mac_address for r in rows} == {"74:D4:DD:C6:51:73", "00:11:22:33:44:55"}
