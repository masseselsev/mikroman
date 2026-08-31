"""The quarantine limit must never outlive being unassigned.

The bug these tests pin was invisible in the UI and highly visible on the
router. Discovery copied the configured quarantine bandwidth onto
``Device.speed_limit``; assigning the device to a user only ever set
``user_id``, so the copy survived. The queue builder reads ``speed_limit`` as
"an override the operator chose for this device", so it faithfully built a
5M/5M child queue underneath an unlimited parent, for every device in the
household. The owner's limit never applied, and the queue tree looked as though
someone had throttled the family at random.

The correction is a modelling one rather than a patch: a stored limit means an
explicit override, and state that follows from a *relationship* - here, having
no owner - is derived where it is used instead of frozen into the row.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import AppSetting, Base, Device, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.schemas.routeros import ARPTableEntry, DHCPLeaseDTO
from backend.app.services.device_manager import DeviceManager
from backend.app.services.traffic_controller import (
    TrafficController,
    resolve_unassigned_limit,
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


@pytest.fixture
async def api_client():
    """The app with an in-memory database, for the endpoint-level assertions.

    No router is configured, so ``require_client`` hands back the offline
    stand-in and the queue syncs are no-ops - which is the point: assigning a
    device is a database operation, and it has to behave correctly whether or
    not the router is reachable.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_db():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.session_factory = factory
        yield client

    app.dependency_overrides.clear()
    await engine.dispose()


class StubRouter:
    """A router that accepts everything and remembers the queues it was asked for."""

    base_url = "stub"

    def __init__(self):
        self.created = []

    async def get_simple_queues(self):
        return []

    async def get_address_list(self, _name):
        return []

    async def add_to_address_list(self, **kwargs):
        pass

    async def get_firewall_filter_rules(self):
        return []

    async def create_simple_queue(self, **kwargs):
        self.created.append(kwargs)
        return f"*{len(self.created)}"

    async def get_dhcp_leases(self):
        return [DHCPLeaseDTO(
            address="192.168.88.241", mac_address="74:D4:DD:C6:51:73",
            host_name="mpcX", status="bound",
        )]

    async def get_arp_table(self):
        return [ARPTableEntry(
            address="192.168.88.241", mac_address="74:D4:DD:C6:51:73",
            interface="bridge", complete=True,
        )]

    async def get_wifi_registrations(self):
        return []


class TestDiscoveryDoesNotStampTheLimit:
    @pytest.mark.asyncio
    async def test_a_newly_discovered_device_stores_no_explicit_limit(self, session):
        """It is quarantined by having no owner, not by carrying a number."""
        session.add(AppSetting(key="unassigned_device_speed_limit", value="5M/5M"))
        await session.commit()

        await DeviceManager(StubRouter()).sync_devices_from_router(session)

        device = (await session.execute(select(Device))).scalars().one()
        assert device.user_id is None
        assert device.speed_limit == "default", (
            "storing the quarantine rate is what let it survive assignment"
        )

    @pytest.mark.asyncio
    async def test_it_is_still_shaped_at_the_quarantine_rate(self, session):
        # The behaviour must not change - only where the value comes from.
        session.add(AppSetting(key="unassigned_device_speed_limit", value="5M/5M"))
        await session.commit()

        router = StubRouter()
        manager = DeviceManager(router)
        await manager.sync_devices_from_router(session)
        device = (await session.execute(select(Device))).scalars().one()

        controller = TrafficController(router)
        await controller.sync_device_queue(device.id, session)

        assert router.created, "an unassigned device must still get a queue"
        assert router.created[-1]["max_limit"] == "5M/5M"


class TestReconciliation:
    @pytest.mark.asyncio
    async def test_a_quarantine_limit_on_an_owned_device_is_cleared(self, session):
        user = User(name="Kristina", speed_limit="unlimited")
        session.add(user)
        session.add(AppSetting(key="unassigned_device_speed_limit", value="5M/5M"))
        await session.commit()

        device = Device(
            mac_address="1A:FB:3A:9D:D2:2C", ip_address="192.168.88.240",
            custom_name="Pixel-9-Pro-XL", user_id=user.id,
            speed_limit="5M/5M", is_active=True,
        )
        session.add(device)
        await session.commit()

        reset = await TrafficController(StubRouter()).reconcile_device_limits(session)

        assert reset == [device.id]
        await session.refresh(device)
        assert device.speed_limit == "default"

    @pytest.mark.asyncio
    async def test_an_unassigned_device_is_left_alone(self, session):
        """For a device with no owner the value is still correct."""
        session.add(AppSetting(key="unassigned_device_speed_limit", value="5M/5M"))
        await session.commit()
        device = Device(
            mac_address="AA:BB:CC:DD:EE:FF", ip_address="192.168.88.244",
            user_id=None, speed_limit="5M/5M", is_active=True,
        )
        session.add(device)
        await session.commit()

        assert await TrafficController(StubRouter()).reconcile_device_limits(session) == []

    @pytest.mark.asyncio
    async def test_a_deliberate_limit_that_is_not_the_quarantine_rate_survives(self, session):
        # Only an exact match against the configured quarantine value is
        # cleared; anything else is the operator's decision.
        user = User(name="Mark", speed_limit="unlimited")
        session.add(user)
        session.add(AppSetting(key="unassigned_device_speed_limit", value="5M/5M"))
        await session.commit()

        device = Device(
            mac_address="50:2E:91:A8:B7:C6", ip_address="192.168.88.245",
            custom_name="Omen", user_id=user.id, speed_limit="25M/50M", is_active=True,
        )
        session.add(device)
        await session.commit()

        assert await TrafficController(StubRouter()).reconcile_device_limits(session) == []
        await session.refresh(device)
        assert device.speed_limit == "25M/50M"

    @pytest.mark.asyncio
    async def test_it_follows_the_configured_rate_rather_than_assuming_the_default(self, session):
        """An installation that raised the quarantine rate stamped that value."""
        user = User(name="M", speed_limit="unlimited")
        session.add(user)
        session.add(AppSetting(key="unassigned_device_speed_limit", value="20M/20M"))
        await session.commit()

        stamped = Device(
            mac_address="11:22:33:44:55:66", ip_address="192.168.88.246",
            user_id=user.id, speed_limit="20M/20M", is_active=True,
        )
        old_default = Device(
            mac_address="66:55:44:33:22:11", ip_address="192.168.88.247",
            user_id=user.id, speed_limit="5M/5M", is_active=True,
        )
        session.add_all([stamped, old_default])
        await session.commit()

        reset = await TrafficController(StubRouter()).reconcile_device_limits(session)

        assert reset == [stamped.id]
        await session.refresh(old_default)
        assert old_default.speed_limit == "5M/5M"


class TestAssignmentReleasesQuarantine:
    """Assigning a device is the moment the quarantine stops applying.

    Reconciliation would catch this on the next tick, but the user is looking at
    the screen now: they moved a device onto a profile and expect that profile's
    limit, not a leftover 5 Mbps cap that clears itself a minute later.
    """

    @pytest.mark.asyncio
    async def test_taking_a_device_out_of_quarantine_clears_the_limit(self, api_client):
        async with api_client.session_factory() as db:
            db.add(AppSetting(key="unassigned_device_speed_limit", value="5M/5M"))
            user = User(name="Kristina", speed_limit="unlimited")
            db.add(user)
            await db.commit()

            device = Device(
                mac_address="1A:FB:3A:9D:D2:2C", ip_address="192.168.88.240",
                custom_name="Pixel-9-Pro-XL", speed_limit="5M/5M", is_active=True,
            )
            db.add(device)
            await db.commit()
            device_id, user_id = device.id, user.id

        res = await api_client.patch(f"/api/v1/devices/{device_id}", json={"user_id": user_id})
        assert res.status_code == 200
        assert res.json()["data"]["speed_limit"] == "default"

    @pytest.mark.asyncio
    async def test_an_explicit_limit_sent_with_the_assignment_is_honoured(self, api_client):
        # The operator said what they wanted in the same request; that wins.
        async with api_client.session_factory() as db:
            db.add(AppSetting(key="unassigned_device_speed_limit", value="5M/5M"))
            user = User(name="Mark", speed_limit="unlimited")
            db.add(user)
            await db.commit()

            device = Device(
                mac_address="22:C7:79:40:B3:12", ip_address="192.168.88.243",
                speed_limit="5M/5M", is_active=True,
            )
            db.add(device)
            await db.commit()
            device_id, user_id = device.id, user.id

        res = await api_client.patch(
            f"/api/v1/devices/{device_id}",
            json={"user_id": user_id, "speed_limit": "10M/30M"},
        )
        assert res.status_code == 200
        assert res.json()["data"]["speed_limit"] == "10M/30M"

    @pytest.mark.asyncio
    async def test_moving_between_users_does_not_disturb_a_chosen_limit(self, api_client):
        # Only the unassigned -> assigned transition releases quarantine.
        async with api_client.session_factory() as db:
            db.add(AppSetting(key="unassigned_device_speed_limit", value="5M/5M"))
            first = User(name="M", speed_limit="unlimited")
            second = User(name="Mark", speed_limit="unlimited")
            db.add_all([first, second])
            await db.commit()

            device = Device(
                mac_address="90:09:DF:8E:D2:8F", ip_address="192.168.88.242",
                user_id=first.id, speed_limit="25M/50M", is_active=True,
            )
            db.add(device)
            await db.commit()
            device_id, second_id = device.id, second.id

        res = await api_client.patch(f"/api/v1/devices/{device_id}", json={"user_id": second_id})
        assert res.status_code == 200
        assert res.json()["data"]["speed_limit"] == "25M/50M"


class TestResolveUnassignedLimit:
    @pytest.mark.asyncio
    async def test_falls_back_to_the_documented_default(self, session):
        assert await resolve_unassigned_limit(session) == "5M/5M"

    @pytest.mark.asyncio
    async def test_reads_the_configured_value(self, session):
        session.add(AppSetting(key="unassigned_device_speed_limit", value="1M/1M"))
        await session.commit()
        assert await resolve_unassigned_limit(session) == "1M/1M"
