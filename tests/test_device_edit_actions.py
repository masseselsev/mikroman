"""Editing devices from inside a profile: clear a stale IP, delete a device
for good, move one back to unassigned, and split a wrongly-merged MAC.

The traffic rules are the point:
  * delete  -> the device is gone but its bytes stay counted for the profile
  * unassign -> the device's recorded share is taken back out of the profile
  * split   -> past (coalesced) traffic cannot be divided; only future traffic
               on the split-off address is tracked separately
"""
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import (
    Base,
    Device,
    DeviceCoexistence,
    DeviceHistory,
    DeviceTrafficRollup,
    TrafficRollup,
    User,
)
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services.device_manager import detach_device_traffic_from_user


@pytest.fixture
async def api_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.session_factory = session_factory
        yield client
    app.dependency_overrides.clear()
    await engine.dispose()


PRIVATE_A = "1A:FB:3A:9D:D2:2C"
PRIVATE_B = "C6:DA:93:39:1E:C5"


async def _seed_user_device(session, *, mac=PRIVATE_A, ip="192.168.88.40", with_traffic=True):
    user = User(name="Mark", speed_limit="unlimited")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    dev = Device(user_id=user.id, mac_address=mac, ip_address=ip,
                 custom_name="Pixel-9-Pro-XL", hostname="Pixel-9-Pro-XL", is_active=True)
    session.add(dev)
    await session.commit()
    await session.refresh(dev)
    if with_traffic:
        d1, d2 = date(2026, 8, 20), date(2026, 8, 21)
        session.add_all([
            DeviceTrafficRollup(device_id=dev.id, record_date=d1, bytes_in=1_000, bytes_out=200),
            DeviceTrafficRollup(device_id=dev.id, record_date=d2, bytes_in=4_000, bytes_out=800),
            TrafficRollup(user_id=user.id, record_date=d1, bytes_in=1_500, bytes_out=300),
            TrafficRollup(user_id=user.id, record_date=d2, bytes_in=4_000, bytes_out=800),
        ])
        await session.commit()
    return user, dev


# --- the detach helper in isolation ------------------------------------------

@pytest.mark.asyncio
async def test_detach_subtracts_the_device_share_and_clamps_at_zero(api_client):
    async with api_client.session_factory() as s:
        user, dev = await _seed_user_device(s)
        moved = await detach_device_traffic_from_user(s, dev, user.id)
        await s.commit()
        assert moved == 2

        rolls = {r.record_date: r for r in (await s.execute(
            select(TrafficRollup).where(TrafficRollup.user_id == user.id)
        )).scalars().all()}
        # 2026-08-20: user had 1500/300, device 1000/200 -> 500/100
        assert (rolls[date(2026, 8, 20)].bytes_in, rolls[date(2026, 8, 20)].bytes_out) == (500, 100)
        # 2026-08-21: user 4000/800, device 4000/800 -> exactly zero, not negative
        assert (rolls[date(2026, 8, 21)].bytes_in, rolls[date(2026, 8, 21)].bytes_out) == (0, 0)


# --- DELETE keeps the traffic on the profile --------------------------------

@pytest.mark.asyncio
async def test_deleting_a_device_keeps_its_traffic_on_the_profile(api_client):
    async with api_client.session_factory() as s:
        user, dev = await _seed_user_device(s)
        user_id, dev_id = user.id, dev.id

    res = await api_client.delete(f"/api/v1/devices/{dev_id}")
    assert res.status_code == 200

    async with api_client.session_factory() as s:
        assert await s.get(Device, dev_id) is None
        # device rollups gone with it...
        dev_rolls = (await s.execute(
            select(DeviceTrafficRollup).where(DeviceTrafficRollup.device_id == dev_id)
        )).scalars().all()
        assert dev_rolls == []
        # ...but the profile's own totals are untouched.
        user_rolls = {r.record_date: (r.bytes_in, r.bytes_out) for r in (await s.execute(
            select(TrafficRollup).where(TrafficRollup.user_id == user_id)
        )).scalars().all()}
        assert user_rolls[date(2026, 8, 20)] == (1_500, 300)
        assert user_rolls[date(2026, 8, 21)] == (4_000, 800)


@pytest.mark.asyncio
async def test_deleting_a_device_detaches_a_linked_adapter(api_client):
    async with api_client.session_factory() as s:
        user, primary = await _seed_user_device(s, with_traffic=False)
        adapter = Device(user_id=user.id, mac_address=PRIVATE_B, ip_address="192.168.88.41",
                         custom_name="Pixel-usb", linked_to_device_id=primary.id, is_active=True)
        s.add(adapter)
        await s.commit()
        primary_id, adapter_id = primary.id, adapter.id

    assert (await api_client.delete(f"/api/v1/devices/{primary_id}")).status_code == 200

    async with api_client.session_factory() as s:
        moved = await s.get(Device, adapter_id)
        assert moved is not None
        assert moved.linked_to_device_id is None


# --- PATCH: unassign detaches, ip clear works -------------------------------

@pytest.mark.asyncio
async def test_unassigning_via_patch_detaches_the_traffic(api_client):
    async with api_client.session_factory() as s:
        user, dev = await _seed_user_device(s)
        user_id, dev_id = user.id, dev.id

    res = await api_client.patch(f"/api/v1/devices/{dev_id}", json={"user_id": None})
    assert res.status_code == 200

    async with api_client.session_factory() as s:
        assert (await s.get(Device, dev_id)).user_id is None
        rolls = {r.record_date: (r.bytes_in, r.bytes_out) for r in (await s.execute(
            select(TrafficRollup).where(TrafficRollup.user_id == user_id)
        )).scalars().all()}
        assert rolls[date(2026, 8, 20)] == (500, 100)
        assert rolls[date(2026, 8, 21)] == (0, 0)


@pytest.mark.asyncio
async def test_unassigning_can_opt_out_of_detaching(api_client):
    async with api_client.session_factory() as s:
        user, dev = await _seed_user_device(s)
        user_id, dev_id = user.id, dev.id

    res = await api_client.patch(
        f"/api/v1/devices/{dev_id}", json={"user_id": None, "detach_traffic": False}
    )
    assert res.status_code == 200
    async with api_client.session_factory() as s:
        rolls = {r.record_date: (r.bytes_in, r.bytes_out) for r in (await s.execute(
            select(TrafficRollup).where(TrafficRollup.user_id == user_id)
        )).scalars().all()}
        assert rolls[date(2026, 8, 20)] == (1_500, 300)  # left as-is


@pytest.mark.asyncio
async def test_patch_clears_a_stale_ip_with_explicit_null(api_client):
    async with api_client.session_factory() as s:
        _, dev = await _seed_user_device(s, with_traffic=False)
        dev_id = dev.id

    res = await api_client.patch(f"/api/v1/devices/{dev_id}", json={"ip_address": None})
    assert res.status_code == 200
    async with api_client.session_factory() as s:
        assert (await s.get(Device, dev_id)).ip_address is None


# --- split ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_split_creates_an_unassigned_device_and_blocks_re_merge(api_client):
    async with api_client.session_factory() as s:
        user, dev = await _seed_user_device(s, with_traffic=False)
        dev_id = dev.id
        # A history row recording that PRIVATE_B was once folded into this device.
        s.add(DeviceHistory(device_id=dev.id, mac_address=PRIVATE_B,
                            event_type="mac_rotated", details="merged earlier"))
        await s.commit()

    res = await api_client.post(f"/api/v1/devices/{dev_id}/split", json={"mac_address": PRIVATE_B})
    assert res.status_code == 200
    new_id = res.json()["data"]["id"]
    assert new_id != dev_id

    async with api_client.session_factory() as s:
        new_dev = await s.get(Device, new_id)
        assert new_dev.mac_address == PRIVATE_B
        assert new_dev.user_id is None
        assert new_dev.is_active is False

        lo, hi = sorted([PRIVATE_A, PRIVATE_B])
        pair = (await s.execute(select(DeviceCoexistence).where(
            DeviceCoexistence.mac_a == lo, DeviceCoexistence.mac_b == hi
        ))).scalar_one_or_none()
        assert pair is not None, "the pair must be recorded so consolidation never re-merges them"

        events = (await s.execute(
            select(DeviceHistory).where(DeviceHistory.event_type == "split")
        )).scalars().all()
        assert len(events) == 2  # one on each device


@pytest.mark.asyncio
async def test_split_rejects_an_address_not_in_history(api_client):
    async with api_client.session_factory() as s:
        _, dev = await _seed_user_device(s, with_traffic=False)
        dev_id = dev.id

    res = await api_client.post(f"/api/v1/devices/{dev_id}/split", json={"mac_address": PRIVATE_B})
    assert res.status_code == 400
    assert "history" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_split_rejects_the_current_address(api_client):
    async with api_client.session_factory() as s:
        _, dev = await _seed_user_device(s, with_traffic=False)
        dev_id, mac = dev.id, dev.mac_address

    res = await api_client.post(f"/api/v1/devices/{dev_id}/split", json={"mac_address": mac})
    assert res.status_code == 400
