"""Merging one device record into a specific other one, chosen by hand.

Assigning a device to a person creates a record of its own. That is the wrong
outcome when the "new" device is the same phone returning on a fresh randomised
MAC and the rotation heuristics were not confident enough to say so - two rows,
one physical device, traffic split across both. Merging by hand is the escape
hatch, and it is the same operation the automatic suggestion performs.

Also covers the byte-loss hole that merging opened: the merged-away record's
mangle rules keep counting on the router until the next sync prunes them, and
those final bytes used to be read, matched against a device id that no longer
resolved, and dropped.
"""
from datetime import date, datetime, timezone

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
    User,
)
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services.device_manager import DeviceManager
from backend.app.services.router_manager import router_manager
from backend.app.services.traffic_accounting import TrafficAccountingService

PRIVATE_A = "1A:FB:3A:9D:D2:2C"
PRIVATE_B = "C6:DA:93:39:1E:C5"


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


class FakeRouter:
    """Only what DeviceManager touches during a merge."""

    async def get_dhcp_leases(self):
        return []

    async def get_arp_table(self):
        return []


async def _two_devices(session):
    user = User(name="Mark", speed_limit="unlimited")
    session.add(user)
    await session.commit()
    await session.refresh(user)

    target = Device(user_id=user.id, mac_address=PRIVATE_B, ip_address="192.168.88.40",
                    custom_name="Pixel-9-Pro-XL", hostname="Pixel-9-Pro-XL", is_active=False,
                    speed_limit="unlimited")
    source = Device(user_id=None, mac_address=PRIVATE_A, ip_address="192.168.88.55",
                    hostname="Pixel-9-Pro-XL", is_active=True,
                    last_seen=datetime.now(timezone.utc).replace(tzinfo=None))
    session.add_all([target, source])
    await session.commit()
    await session.refresh(target)
    await session.refresh(source)
    return user, target, source


@pytest.mark.asyncio
async def test_merging_carries_traffic_and_keeps_the_targets_identity(session):
    user, target, source = await _two_devices(session)
    day = date(2026, 8, 30)
    session.add_all([
        DeviceTrafficRollup(device_id=target.id, record_date=day, bytes_in=1_000, bytes_out=100),
        DeviceTrafficRollup(device_id=source.id, record_date=day, bytes_in=4_000, bytes_out=400),
    ])
    await session.commit()

    merged = await DeviceManager(FakeRouter()).merge_devices(
        session, source_device_id=source.id, target_device_id=target.id
    )

    assert merged.id == target.id
    # The target keeps who it is; the source supplies where it now lives.
    assert merged.custom_name == "Pixel-9-Pro-XL"
    assert merged.user_id == user.id
    assert merged.mac_address == PRIVATE_A
    assert merged.ip_address == "192.168.88.55"
    assert merged.is_active is True

    rolls = (await session.execute(
        select(DeviceTrafficRollup).where(DeviceTrafficRollup.record_date == day)
    )).scalars().all()
    assert len(rolls) == 1
    assert (rolls[0].bytes_in, rolls[0].bytes_out) == (5_000, 500)
    assert await session.get(Device, source.id) is None


@pytest.mark.asyncio
async def test_a_device_cannot_be_merged_into_itself(session):
    _, target, _ = await _two_devices(session)
    with pytest.raises(ValueError, match="itself"):
        await DeviceManager(FakeRouter()).merge_devices(
            session, source_device_id=target.id, target_device_id=target.id
        )


@pytest.mark.asyncio
async def test_a_manual_merge_overrules_the_co_presence_evidence(session):
    """Two records seen online at once are normally kept apart forever.

    That guard exists for three people with identical Pixels. When an operator
    merges them anyway they have supplied better information than the heuristic
    had, so the recorded pair is dropped - otherwise the next discovery sweep
    would keep them apart again and the merge would silently undo itself.
    """
    _, target, source = await _two_devices(session)
    session.add(DeviceCoexistence(
        mac_a=min(PRIVATE_A, PRIVATE_B), mac_b=max(PRIVATE_A, PRIVATE_B),
        hostname="pixel-9-pro-xl",
        first_seen_together=datetime.now(timezone.utc).replace(tzinfo=None),
        last_seen_together=datetime.now(timezone.utc).replace(tzinfo=None),
    ))
    await session.commit()

    await DeviceManager(FakeRouter()).merge_devices(
        session, source_device_id=source.id, target_device_id=target.id
    )

    remaining = (await session.execute(select(DeviceCoexistence))).scalars().all()
    assert remaining == []


@pytest.mark.asyncio
async def test_the_merge_is_written_into_the_surviving_devices_history(session):
    _, target, source = await _two_devices(session)
    await DeviceManager(FakeRouter()).merge_devices(
        session, source_device_id=source.id, target_device_id=target.id,
        note="merged by hand",
    )
    events = (await session.execute(
        select(DeviceHistory).where(DeviceHistory.device_id == target.id)
    )).scalars().all()
    assert any(e.details == "merged by hand" for e in events)


# --- the byte-loss hole merging used to open ----------------------------------

@pytest.mark.asyncio
async def test_counters_of_the_merged_away_record_are_credited_to_the_survivor(session):
    """The source's mangle rules outlive its database row by one sync tick.

    Before the successor map, `_flush_deltas` looked the dead device id up,
    found nothing, and threw those bytes away.
    """
    _, target, source = await _two_devices(session)
    dead_id = source.id

    await DeviceManager(FakeRouter()).merge_devices(
        session, source_device_id=source.id, target_device_id=target.id
    )

    svc = TrafficAccountingService(FakeRouter(), router_id=1)
    day = date(2026, 8, 31)
    total_in, total_out = await svc._flush_deltas(session, day, {dead_id: (2_048, 512)})
    await session.commit()

    assert (total_in, total_out) == (2_048, 512)
    roll = (await session.execute(
        select(DeviceTrafficRollup).where(DeviceTrafficRollup.record_date == day)
    )).scalars().all()
    assert len(roll) == 1
    assert roll[0].device_id == target.id
    assert (roll[0].bytes_in, roll[0].bytes_out) == (2_048, 512)


@pytest.mark.asyncio
async def test_a_chain_of_merges_still_lands_on_the_last_survivor(session):
    """A -> B, then B -> C. A's late bytes must reach C, not the deleted B."""
    user = User(name="Mark", speed_limit="unlimited")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    a = Device(mac_address="02:00:00:00:00:0A", ip_address="10.0.0.10", is_active=True)
    b = Device(mac_address="02:00:00:00:00:0B", ip_address="10.0.0.11", is_active=True)
    c = Device(user_id=user.id, mac_address="02:00:00:00:00:0C", ip_address="10.0.0.12",
               custom_name="Laptop", is_active=True)
    session.add_all([a, b, c])
    await session.commit()
    for d in (a, b, c):
        await session.refresh(d)
    a_id = a.id

    mgr = DeviceManager(FakeRouter())
    await mgr.merge_devices(session, source_device_id=a.id, target_device_id=b.id)
    await mgr.merge_devices(session, source_device_id=b.id, target_device_id=c.id)

    svc = TrafficAccountingService(FakeRouter(), router_id=1)
    day = date(2026, 8, 31)
    await svc._flush_deltas(session, day, {a_id: (777, 0)})
    await session.commit()

    roll = (await session.execute(select(DeviceTrafficRollup))).scalars().all()
    assert len(roll) == 1
    assert roll[0].device_id == c.id
    assert roll[0].bytes_in == 777


@pytest.mark.asyncio
async def test_bytes_for_a_genuinely_deleted_device_are_dropped_not_misfiled(session):
    """No successor means nowhere legitimate to put them - discard, never guess."""
    _, target, source = await _two_devices(session)
    ghost_id = 99_999

    svc = TrafficAccountingService(FakeRouter(), router_id=1)
    total_in, total_out = await svc._flush_deltas(
        session, date(2026, 8, 31), {ghost_id: (5_000, 0)}
    )
    await session.commit()

    assert (total_in, total_out) == (0, 0)
    assert (await session.execute(select(DeviceTrafficRollup))).scalars().all() == []


# --- through the API ----------------------------------------------------------

@pytest.fixture
async def api_client():
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


@pytest.mark.asyncio
async def test_merging_a_device_into_itself_is_a_bad_request_not_a_404(api_client, monkeypatch):
    """A nonsensical request must not be reported as a missing record."""
    async with api_client.session_factory() as s:
        _, target, _ = await _two_devices(s)
        target_id = target.id

    # The endpoint builds a DeviceManager before validating, so stand a router in.
    async def fake_require_client(**kwargs):
        return FakeRouter()

    monkeypatch.setattr(router_manager, "require_client", fake_require_client)

    res = await api_client.post(f"/api/v1/devices/{target_id}/merge",
                                json={"target_device_id": target_id})
    assert res.status_code == 400
    assert "itself" in res.json()["detail"]
