import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.core.config import Settings
from backend.app.db.models import Base, Device, DeviceHistory, User
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
async def test_device_history_and_tracking(mock_settings):
    client = RouterOSClient(mock_settings)
    dev_mgr = DeviceManager(client)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    # 1. First Discovery Scan
    with respx.mock(base_url="https://192.168.88.1:443/rest") as respx_mock:
        respx_mock.get("/ip/dhcp-server/lease").respond(
            200,
            json=[
                {
                    ".id": "*1",
                    "address": "192.168.88.10",
                    "mac-address": "FC:6D:77:F8:5D:40",
                    "host-name": "mpcX",
                    "status": "bound"
                }
            ]
        )
        respx_mock.get("/ip/arp").respond(200, json=[])
        respx_mock.get("/interface/wifi/registration-table").respond(200, json=[])

        async with session_factory() as session:
            all_devs, newly_discovered = await dev_mgr.sync_devices_from_router(session)
            assert len(newly_discovered) == 1
            device = newly_discovered[0]
            assert device.hostname == "mpcX"
            assert device.vendor == "Intel"

            # Check history entry created
            hist = (await session.execute(select(DeviceHistory).where(DeviceHistory.device_id == device.id))).scalars().all()
            assert len(hist) == 1
            assert hist[0].event_type == "discovered"

    # 2. Hostname & IP Change Scan
    with respx.mock(base_url="https://192.168.88.1:443/rest") as respx_mock:
        respx_mock.get("/ip/dhcp-server/lease").respond(
            200,
            json=[
                {
                    ".id": "*1",
                    "address": "192.168.88.99",  # IP Changed
                    "mac-address": "FC:6D:77:F8:5D:40",
                    "host-name": "mpcX-Renamed",   # Hostname Changed
                    "status": "bound"
                }
            ]
        )
        respx_mock.get("/ip/arp").respond(200, json=[])
        respx_mock.get("/interface/wifi/registration-table").respond(200, json=[])

        async with session_factory() as session:
            await dev_mgr.sync_devices_from_router(session)
            hist = (await session.execute(select(DeviceHistory).order_by(DeviceHistory.created_at.asc()))).scalars().all()
            event_types = [h.event_type for h in hist]
            assert "discovered" in event_types
            assert "ip_changed" in event_types
            assert "hostname_changed" in event_types

    await engine.dispose()


@pytest.mark.asyncio
async def test_smart_merge_suggestions_and_execution(mock_settings):
    client = RouterOSClient(mock_settings)
    dev_mgr = DeviceManager(client)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        # Create User with an assigned offline iPhone (Old MAC)
        user = User(name="Alex", speed_limit="unlimited")
        session.add(user)
        await session.flush()

        old_target = Device(
            mac_address="3C:22:FB:11:22:33",
            user_id=user.id,
            ip_address="192.168.88.15",
            hostname="Alex-iPhone",
            custom_name="Alex's Phone",
            vendor="Apple",
            is_active=False
        )
        session.add(old_target)

        # Unassigned newly joined device with rotated private MAC and same hostname
        new_unassigned = Device(
            mac_address="D6:3D:1B:54:03:2F",  # Private MAC (starts with D6)
            user_id=None,
            ip_address="192.168.88.50",
            hostname="Alex-iPhone",
            vendor="Apple (Private MAC)",
            is_active=True
        )
        session.add(new_unassigned)
        await session.commit()
        await session.refresh(old_target)
        await session.refresh(new_unassigned)

        # 1. Test Merge Suggestions Finder
        suggestions = await dev_mgr.find_merge_suggestions(session)
        assert len(suggestions) >= 1
        sug = suggestions[0]
        assert sug.unassigned_device_id == new_unassigned.id
        assert sug.suggested_target_device_id == old_target.id
        assert sug.suggested_user_name == "Alex"
        assert sug.confidence >= 0.90

        # 2. Test Merge Execution
        merged = await dev_mgr.merge_devices(session, new_unassigned.id, old_target.id)
        assert merged.id == old_target.id
        assert merged.mac_address == "D6:3D:1B:54:03:2F"
        assert merged.ip_address == "192.168.88.50"
        assert merged.is_active is True
        assert merged.user_id == user.id

        # Source device should be deleted
        deleted = await session.get(Device, new_unassigned.id)
        assert deleted is None

        # History should reflect the rotation
        hist = (await session.execute(select(DeviceHistory).where(DeviceHistory.device_id == old_target.id))).scalars().all()
        assert any(h.event_type == "mac_rotated" for h in hist)

    await engine.dispose()
