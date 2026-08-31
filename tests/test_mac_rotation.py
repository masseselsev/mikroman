"""Recognising a rotated private MAC as the same device.

The behaviour under test is a judgement call made on incomplete evidence, so
these tests are split by what the code is being asked to do: adopt the clear
case, and refuse every case where adopting would be a guess. The refusals
matter more than the adoptions - a wrong adoption silently hands one person's
phone to another household member, along with their speed limit and their
traffic history.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import AlertLog, Base, Device, DeviceHistory, User
from backend.app.schemas.routeros import ARPTableEntry, DHCPLeaseDTO
from backend.app.services.device_manager import DeviceManager
from backend.app.services.mac_rotation import (
    collect_present_macs,
    find_rotation_candidate,
    is_generic_hostname,
    normalise_hostname,
)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


class RotatingRouter:
    """A router reporting exactly one client, on a freshly rotated address."""

    def __init__(self, new_mac: str, hostname: str):
        self._mac = new_mac
        self._hostname = hostname

    async def get_dhcp_leases(self):
        return [DHCPLeaseDTO(
            address="192.168.88.240", mac_address=self._mac,
            host_name=self._hostname, status="bound",
        )]

    async def get_arp_table(self):
        return [ARPTableEntry(
            address="192.168.88.240", mac_address=self._mac,
            interface="bridge", complete=True,
        )]

    async def get_wifi_registrations(self):
        return []

# Locally-administered bit set in the first octet - what every phone uses for a
# per-network private address.
PRIVATE_A = "1A:FB:3A:9D:D2:2C"
PRIVATE_B = "C6:DA:93:39:1E:C5"
PRIVATE_C = "4E:EF:DA:AB:06:76"
# Bit clear: a real burned-in address.
HARDWARE = "74:D4:DD:C6:51:73"


def device(mac, hostname=None, custom_name=None, device_id=1):
    return Device(
        id=device_id,
        mac_address=mac,
        hostname=hostname,
        custom_name=custom_name,
    )


class TestNormaliseHostname:
    def test_case_and_whitespace_do_not_distinguish_devices(self):
        assert normalise_hostname("  Pixel-9-Pro-XL ") == "pixel-9-pro-xl"

    def test_mdns_suffix_is_dropped(self):
        # macOS and iOS advertise "name.local"; the same device also appears as
        # a bare "name" in the DHCP table.
        assert normalise_hostname("Kristina-iPhone.local") == "kristina-iphone"

    def test_trailing_dot_is_dropped(self):
        assert normalise_hostname("host.") == "host"

    def test_empty_values_yield_nothing(self):
        assert normalise_hostname(None) is None
        assert normalise_hostname("   ") is None


class TestGenericHostname:
    def test_factory_default_names_are_generic(self):
        # A house can hold two of these, so the name identifies nothing.
        assert is_generic_hostname("iPhone")
        assert is_generic_hostname("iphone")
        assert is_generic_hostname("android")
        assert is_generic_hostname("MacBook-Pro")

    def test_personalised_names_are_distinctive(self):
        assert not is_generic_hostname("Kristina-iPhone")
        assert not is_generic_hostname("Pixel-9-Pro-XL")

    def test_too_short_to_be_evidence(self):
        assert is_generic_hostname("pc")
        assert is_generic_hostname("")
        assert is_generic_hostname(None)


class TestAdoptsTheClearCase:
    def test_recognises_a_rotated_address(self):
        """The signature: distinctive hostname returns on a new private MAC
        while the old private MAC has left the router entirely."""
        known = device(PRIVATE_A, hostname="Pixel-9-Pro-XL", device_id=3)

        match = find_rotation_candidate(
            new_mac=PRIVATE_B,
            new_hostname="Pixel-9-Pro-XL",
            known_devices=[known],
            present_macs={PRIVATE_B},
        )

        assert match is known

    def test_matches_against_a_renamed_devices_custom_name(self):
        # The operator renamed the device; its DHCP hostname is what returns.
        known = device(PRIVATE_A, hostname=None, custom_name="Pixel-9-Pro-XL", device_id=3)

        match = find_rotation_candidate(
            new_mac=PRIVATE_B,
            new_hostname="pixel-9-pro-xl",
            known_devices=[known],
            present_macs={PRIVATE_B},
        )

        assert match is known


class TestRefusesToGuess:
    def test_will_not_adopt_when_the_old_address_is_still_present(self):
        """Both addresses on the router at once is a second adapter, not a
        rotation - one radio cannot hold two addresses."""
        known = device(PRIVATE_A, hostname="Pixel-9-Pro-XL", device_id=3)

        match = find_rotation_candidate(
            new_mac=PRIVATE_B,
            new_hostname="Pixel-9-Pro-XL",
            known_devices=[known],
            present_macs={PRIVATE_A, PRIVATE_B},
        )

        assert match is None

    def test_will_not_adopt_on_a_generic_hostname(self):
        """Apple ships phones named just "iPhone". Two of them in one house
        would make this rule hand one person's phone to another."""
        known = device(PRIVATE_A, hostname="iPhone", device_id=2)

        match = find_rotation_candidate(
            new_mac=PRIVATE_C,
            new_hostname="iPhone",
            known_devices=[known],
            present_macs={PRIVATE_C},
        )

        assert match is None

    def test_will_not_adopt_when_several_devices_share_the_hostname(self):
        # Exactly the state the bug left behind: three "iPhone" records. With
        # no way to tell which is the real one, the honest answer is none.
        knowns = [
            device(PRIVATE_A, hostname="Kristina-iPhone", device_id=2),
            device(PRIVATE_B, hostname="Kristina-iPhone", device_id=11),
        ]

        match = find_rotation_candidate(
            new_mac=PRIVATE_C,
            new_hostname="Kristina-iPhone",
            known_devices=knowns,
            present_macs={PRIVATE_C},
        )

        assert match is None

    def test_a_hardware_address_is_a_genuinely_new_adapter(self):
        # Burned-in addresses do not rotate, so a new one is a new device.
        known = device(PRIVATE_A, hostname="Pixel-9-Pro-XL", device_id=3)

        match = find_rotation_candidate(
            new_mac=HARDWARE,
            new_hostname="Pixel-9-Pro-XL",
            known_devices=[known],
            present_macs={HARDWARE},
        )

        assert match is None

    def test_a_device_on_its_hardware_address_did_not_rotate_away(self):
        # The known device never used a private address, so it cannot be the
        # one that changed.
        known = device(HARDWARE, hostname="Workstation-01", device_id=4)

        match = find_rotation_candidate(
            new_mac=PRIVATE_B,
            new_hostname="Workstation-01",
            known_devices=[known],
            present_macs={PRIVATE_B},
        )

        assert match is None

    def test_no_hostname_is_no_evidence(self):
        known = device(PRIVATE_A, hostname="Pixel-9-Pro-XL", device_id=3)

        assert find_rotation_candidate(PRIVATE_B, None, [known], {PRIVATE_B}) is None

    def test_a_different_hostname_is_a_different_device(self):
        known = device(PRIVATE_A, hostname="Pixel-9-Pro-XL", device_id=3)

        match = find_rotation_candidate(
            new_mac=PRIVATE_B,
            new_hostname="Kristina-iPhone",
            known_devices=[known],
            present_macs={PRIVATE_B},
        )

        assert match is None


class TestCollectPresentMacs:
    def test_unions_every_discovery_table(self):
        """Each table has a blind spot, so presence is the union of all three:
        a lease outlives its client, an ARP entry expires while the radio link
        holds, and a wireless client may hold no lease of its own."""

        class Entry:
            def __init__(self, mac):
                self.mac_address = mac

        macs = collect_present_macs(
            [Entry(PRIVATE_A)],
            [Entry(PRIVATE_B), Entry(None)],
            [Entry(PRIVATE_C)],
        )

        assert macs == {PRIVATE_A, PRIVATE_B, PRIVATE_C}

    def test_tolerates_empty_and_missing_tables(self):
        assert collect_present_macs([], None) == set()


@pytest.mark.parametrize("mac", ["", "z", "not-a-mac"])
def test_malformed_addresses_never_raise(mac):
    # Discovery data comes off the wire; a malformed address must return "no
    # match" rather than take the whole scan down.
    assert find_rotation_candidate(mac, "Pixel-9-Pro-XL", [], set()) is None


class TestDiscoveryAdoptsInsteadOfDuplicating:
    """The end-to-end behaviour: one scan, one device, identity preserved."""

    @pytest.mark.asyncio
    async def test_a_rotated_phone_keeps_its_record_and_its_owner(self, session):
        user = User(name="Kristina", speed_limit="unlimited")
        session.add(user)
        await session.commit()

        known = Device(
            mac_address=PRIVATE_A,
            ip_address="192.168.88.240",
            hostname="Pixel-9-Pro-XL",
            custom_name="Pixel-9-Pro-XL",
            user_id=user.id,
            speed_limit="25M/50M",
            is_active=True,
        )
        session.add(known)
        await session.commit()
        original_id = known.id

        # The Wi-Fi was renamed; the phone comes back on a fresh private MAC and
        # its old address is nowhere on the router.
        router = RotatingRouter(new_mac=PRIVATE_B, hostname="Pixel-9-Pro-XL")
        await DeviceManager(router).sync_devices_from_router(session)

        devices = (await session.execute(select(Device))).scalars().all()
        assert len(devices) == 1, "a rotation must not create a second record"

        device = devices[0]
        assert device.id == original_id, "the existing row is re-keyed, not replaced"
        assert device.mac_address == PRIVATE_B
        # Everything attached to identity travels with the row - which is the
        # whole reason for re-keying rather than creating a new device.
        assert device.user_id == user.id
        assert device.custom_name == "Pixel-9-Pro-XL"
        assert device.speed_limit == "25M/50M"

    @pytest.mark.asyncio
    async def test_the_rotation_is_recorded_rather_than_happening_silently(self, session):
        known = Device(
            mac_address=PRIVATE_A, ip_address="192.168.88.240",
            hostname="Pixel-9-Pro-XL", custom_name="Pixel-9-Pro-XL", is_active=True,
        )
        session.add(known)
        await session.commit()

        router = RotatingRouter(new_mac=PRIVATE_B, hostname="Pixel-9-Pro-XL")
        await DeviceManager(router).sync_devices_from_router(session)

        history = (await session.execute(
            select(DeviceHistory).where(DeviceHistory.event_type == "mac_rotated")
        )).scalars().all()
        assert len(history) == 1
        assert PRIVATE_A in history[0].details and PRIVATE_B in history[0].details

        alerts = (await session.execute(
            select(AlertLog).where(AlertLog.alert_type == "mac_rotated")
        )).scalars().all()
        assert len(alerts) == 1, "rewriting a device's identity must be visible"

    @pytest.mark.asyncio
    async def test_a_genuinely_new_phone_still_becomes_a_new_device(self, session):
        """The guard must not swallow real arrivals."""
        known = Device(
            mac_address=PRIVATE_A, ip_address="192.168.88.240",
            hostname="Pixel-9-Pro-XL", custom_name="Pixel-9-Pro-XL", is_active=True,
        )
        session.add(known)
        await session.commit()

        # Different hostname: nothing links the two.
        router = RotatingRouter(new_mac=PRIVATE_B, hostname="Kristina-iPhone")
        await DeviceManager(router).sync_devices_from_router(session)

        devices = (await session.execute(select(Device))).scalars().all()
        assert len(devices) == 2


PRIVATE_D = "7A:11:22:33:44:55"
PRIVATE_E = "8E:66:77:88:99:AA"


class TestConsolidateRotatedDevices:
    """Cleaning up after adoption could not: several rows for one phone.

    Discovery-time adoption bails once two candidates share a hostname, so a
    burst of rotations - an access-point change is the usual trigger - leaves a
    pile of duplicate rows. This pass collapses them.
    """

    async def _mgr(self):
        return DeviceManager(RotatingRouter(new_mac="x", hostname="x"))

    @pytest.mark.asyncio
    async def test_collapses_same_user_duplicates_into_the_active_row(self, session):
        user = User(name="M", speed_limit="unlimited")
        session.add(user)
        await session.commit()

        from datetime import datetime, timedelta
        base = datetime(2026, 8, 31, 12, 0, 0)
        stale1 = Device(mac_address=PRIVATE_A, hostname="Pixel-9-Pro-XL",
                        custom_name="Pixel-9-Pro-XL", user_id=user.id,
                        is_active=False, last_seen=base)
        stale2 = Device(mac_address=PRIVATE_B, hostname="Pixel-9-Pro-XL",
                        custom_name="Pixel-9-Pro-XL", user_id=user.id,
                        is_active=False, last_seen=base + timedelta(hours=1))
        live = Device(mac_address=PRIVATE_C, hostname="Pixel-9-Pro-XL",
                      custom_name="Pixel-9-Pro-XL", user_id=user.id,
                      ip_address="192.168.88.240", is_active=True,
                      last_seen=base + timedelta(hours=5))
        session.add_all([stale1, stale2, live])
        await session.commit()
        live_id = live.id

        removed = await (await self._mgr()).consolidate_rotated_devices(session)

        assert removed == 2
        rows = (await session.execute(select(Device))).scalars().all()
        assert len(rows) == 1
        assert rows[0].id == live_id
        assert rows[0].mac_address == PRIVATE_C  # the survivor keeps its own live MAC

    @pytest.mark.asyncio
    async def test_absorbs_an_unassigned_duplicate_onto_the_owner(self, session):
        """The automatic merge that manual suggestions needed a click for."""
        user = User(name="Kristina", speed_limit="unlimited")
        session.add(user)
        await session.commit()

        from datetime import datetime
        owned = Device(mac_address=PRIVATE_A, hostname="Pixel-7", custom_name="Pixel-7",
                       user_id=user.id, is_active=False,
                       last_seen=datetime(2026, 8, 31, 10, 0, 0))
        orphan = Device(mac_address=PRIVATE_B, hostname="Pixel-7", custom_name="Pixel-7",
                        user_id=None, is_active=True,
                        last_seen=datetime(2026, 8, 31, 17, 0, 0))
        session.add_all([owned, orphan])
        await session.commit()

        removed = await (await self._mgr()).consolidate_rotated_devices(session)

        assert removed == 1
        rows = (await session.execute(select(Device))).scalars().all()
        assert len(rows) == 1
        assert rows[0].user_id == user.id
        assert rows[0].is_active is True

    @pytest.mark.asyncio
    async def test_moves_history_and_traffic_onto_the_survivor(self, session):
        from datetime import date, datetime

        from backend.app.db.models import DeviceTrafficRollup

        user = User(name="M", speed_limit="unlimited")
        session.add(user)
        await session.commit()

        stale = Device(mac_address=PRIVATE_A, hostname="Pixel-9-Pro-XL",
                       custom_name="Pixel-9-Pro-XL", user_id=user.id, is_active=False,
                       last_seen=datetime(2026, 8, 30, 9, 0, 0))
        live = Device(mac_address=PRIVATE_B, hostname="Pixel-9-Pro-XL",
                      custom_name="Pixel-9-Pro-XL", user_id=user.id, is_active=True,
                      last_seen=datetime(2026, 8, 31, 9, 0, 0))
        session.add_all([stale, live])
        await session.commit()

        session.add_all([
            DeviceHistory(device_id=stale.id, mac_address=PRIVATE_A,
                          event_type="discovered", details="first seen"),
            DeviceTrafficRollup(device_id=stale.id, record_date=date(2026, 8, 30),
                                bytes_in=1000, bytes_out=200),
            DeviceTrafficRollup(device_id=live.id, record_date=date(2026, 8, 30),
                                bytes_in=500, bytes_out=100),
            DeviceTrafficRollup(device_id=stale.id, record_date=date(2026, 8, 29),
                                bytes_in=7, bytes_out=3),
        ])
        await session.commit()
        live_id = live.id

        await (await self._mgr()).consolidate_rotated_devices(session)

        hist = (await session.execute(
            select(DeviceHistory).where(DeviceHistory.device_id == live_id)
        )).scalars().all()
        assert any(h.details == "first seen" for h in hist)

        rolls = {
            r.record_date: r for r in (await session.execute(
                select(DeviceTrafficRollup).where(DeviceTrafficRollup.device_id == live_id)
            )).scalars().all()
        }
        # Same-date rows are added together, not duplicated.
        assert rolls[date(2026, 8, 30)].bytes_in == 1500
        assert rolls[date(2026, 8, 30)].bytes_out == 300
        assert rolls[date(2026, 8, 29)].bytes_in == 7

    @pytest.mark.asyncio
    async def test_leaves_alone_a_hostname_split_across_two_users(self, session):
        """Two people who each own a phone of the same model."""
        from datetime import datetime
        a = User(name="Mark", speed_limit="unlimited")
        b = User(name="Kristina", speed_limit="unlimited")
        session.add_all([a, b])
        await session.commit()

        session.add_all([
            Device(mac_address=PRIVATE_A, hostname="Pixel-7", custom_name="Pixel-7",
                   user_id=a.id, is_active=True, last_seen=datetime(2026, 8, 31, 9, 0)),
            Device(mac_address=PRIVATE_B, hostname="Pixel-7", custom_name="Pixel-7",
                   user_id=b.id, is_active=True, last_seen=datetime(2026, 8, 31, 9, 0)),
        ])
        await session.commit()

        removed = await (await self._mgr()).consolidate_rotated_devices(session)

        assert removed == 0
        assert len((await session.execute(select(Device))).scalars().all()) == 2

    @pytest.mark.asyncio
    async def test_collapses_a_generic_hostname_only_when_it_is_safe(self, session):
        """"iPhone" x3 for one user with two stale rows is one rotating phone;
        held to a stricter bar than a specific hostname, but still collapsed."""
        from datetime import datetime
        user = User(name="Mark", speed_limit="unlimited")
        session.add(user)
        await session.commit()
        session.add_all([
            Device(mac_address=PRIVATE_A, hostname="iPhone", custom_name="iPhone",
                   vendor="Apple", user_id=user.id, is_active=True,
                   last_seen=datetime(2026, 8, 31, 9, 0)),
            Device(mac_address=PRIVATE_B, hostname="iPhone", custom_name="iPhone",
                   vendor="Apple", user_id=user.id, is_active=False,
                   last_seen=datetime(2026, 8, 30, 9, 0)),
            Device(mac_address=PRIVATE_C, hostname="iPhone", custom_name="iPhone",
                   vendor="Apple", user_id=user.id, is_active=False,
                   last_seen=datetime(2026, 8, 29, 9, 0)),
        ])
        await session.commit()

        removed = await (await self._mgr()).consolidate_rotated_devices(session)

        assert removed == 2

    @pytest.mark.asyncio
    async def test_leaves_a_generic_hostname_alone_when_two_are_online(self, session):
        """Two "iPhone" rows both online under one user could be two phones."""
        from datetime import datetime
        user = User(name="Mark", speed_limit="unlimited")
        session.add(user)
        await session.commit()
        session.add_all([
            Device(mac_address=PRIVATE_A, hostname="iPhone", custom_name="iPhone",
                   vendor="Apple", user_id=user.id, is_active=True,
                   last_seen=datetime(2026, 8, 31, 9, 0)),
            Device(mac_address=PRIVATE_B, hostname="iPhone", custom_name="iPhone",
                   vendor="Apple", user_id=user.id, is_active=True,
                   last_seen=datetime(2026, 8, 31, 9, 0)),
        ])
        await session.commit()

        removed = await (await self._mgr()).consolidate_rotated_devices(session)

        assert removed == 0

    @pytest.mark.asyncio
    async def test_a_hardware_mac_is_not_a_rotation_of_anything(self, session):
        from datetime import datetime
        user = User(name="M", speed_limit="unlimited")
        session.add(user)
        await session.commit()
        session.add_all([
            Device(mac_address=HARDWARE, hostname="Pixel-9-Pro-XL", custom_name="Pixel-9-Pro-XL",
                   user_id=user.id, is_active=True, last_seen=datetime(2026, 8, 31, 9, 0)),
            Device(mac_address=PRIVATE_B, hostname="Pixel-9-Pro-XL", custom_name="Pixel-9-Pro-XL",
                   user_id=user.id, is_active=False, last_seen=datetime(2026, 8, 30, 9, 0)),
        ])
        await session.commit()

        removed = await (await self._mgr()).consolidate_rotated_devices(session)

        # Only the two randomized rows would ever group; here one is a burned-in
        # address, so there is no group of 2+ to collapse.
        assert removed == 0
        assert len((await session.execute(select(Device))).scalars().all()) == 2

    @pytest.mark.asyncio
    async def test_repoints_a_link_that_targeted_a_removed_row(self, session):
        from datetime import datetime
        user = User(name="M", speed_limit="unlimited")
        session.add(user)
        await session.commit()

        stale = Device(mac_address=PRIVATE_A, hostname="Pixel-9-Pro-XL", custom_name="Pixel-9-Pro-XL",
                       user_id=user.id, is_active=False, last_seen=datetime(2026, 8, 30, 9, 0))
        live = Device(mac_address=PRIVATE_B, hostname="Pixel-9-Pro-XL", custom_name="Pixel-9-Pro-XL",
                      user_id=user.id, is_active=True, last_seen=datetime(2026, 8, 31, 9, 0))
        session.add_all([stale, live])
        await session.commit()

        adapter = Device(mac_address=PRIVATE_D, hostname="Pixel-9-Pro-XL-usb",
                         custom_name="Pixel-9-Pro-XL-usb", user_id=user.id,
                         linked_to_device_id=stale.id, is_active=True,
                         last_seen=datetime(2026, 8, 31, 9, 0))
        session.add(adapter)
        await session.commit()
        live_id, adapter_id = live.id, adapter.id

        await (await self._mgr()).consolidate_rotated_devices(session)

        moved = await session.get(Device, adapter_id)
        assert moved.linked_to_device_id == live_id
