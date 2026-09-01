"""Retroactively folding the LAN-to-LAN over-count out of the rollups.

The physical WAN counter never double-counts, so on a day where the summed
device volume exceeds what the WAN carried (minus the router's own traffic),
every device rollup for that day is scaled down to match and the per-user
rollups for that day are rebuilt from the corrected figures. Volume is only
ever removed; a day within tolerance, or with no WAN measurement, is left
alone.
"""
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import (
    Base,
    Device,
    DeviceTrafficRollup,
    Router,
    RouterSelfTrafficRollup,
    RouterTrafficRollup,
    TrafficRollup,
    User,
)
from backend.app.services.history_reconcile import reconcile_overcounted_history

MB = 1024 ** 2
OVER = date(2026, 8, 30)   # devices exceed the WAN
FINE = date(2026, 8, 31)   # devices under the WAN - genuine unaccounted


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _seed(session):
    session.add(Router(id=1, name="Main", host="10.0.0.1", username="u", password="p"))
    alice = User(name="Alice", speed_limit="unlimited")
    bob = User(name="Bob", speed_limit="unlimited")
    session.add_all([alice, bob])
    await session.commit()
    await session.refresh(alice)
    await session.refresh(bob)

    a_dev = Device(router_id=1, user_id=alice.id, mac_address="AA:AA:AA:AA:AA:01",
                   ip_address="10.0.0.5", is_active=True)
    b_dev = Device(router_id=1, user_id=bob.id, mac_address="BB:BB:BB:BB:BB:01",
                   ip_address="10.0.0.6", is_active=True)
    session.add_all([a_dev, b_dev])
    await session.commit()
    await session.refresh(a_dev)
    await session.refresh(b_dev)

    # 2026-08-30: WAN carried 1000, router used 100 for itself -> target 900.
    # Devices claim 1000 + 500 = 1500 -> 600 MB of LAN-to-LAN double count.
    session.add(RouterTrafficRollup(router_id=1, record_date=OVER, bytes_in=1000 * MB, bytes_out=0))
    session.add(RouterSelfTrafficRollup(router_id=1, record_date=OVER, bytes_in=100 * MB, bytes_out=0))
    session.add(DeviceTrafficRollup(device_id=a_dev.id, record_date=OVER, bytes_in=1000 * MB, bytes_out=0))
    session.add(DeviceTrafficRollup(device_id=b_dev.id, record_date=OVER, bytes_in=500 * MB, bytes_out=0))
    session.add(TrafficRollup(user_id=alice.id, record_date=OVER, bytes_in=1000 * MB, bytes_out=0))
    session.add(TrafficRollup(user_id=bob.id, record_date=OVER, bytes_in=500 * MB, bytes_out=0))

    # 2026-08-31: WAN carried 2000, devices only claim 800 - untouched.
    session.add(RouterTrafficRollup(router_id=1, record_date=FINE, bytes_in=2000 * MB, bytes_out=0))
    session.add(DeviceTrafficRollup(device_id=a_dev.id, record_date=FINE, bytes_in=800 * MB, bytes_out=0))
    session.add(TrafficRollup(user_id=alice.id, record_date=FINE, bytes_in=800 * MB, bytes_out=0))
    await session.commit()
    return alice, bob, a_dev, b_dev


async def test_dry_run_reports_without_writing(session):
    alice, bob, a_dev, b_dev = await _seed(session)
    a_id = a_dev.id

    summary = await reconcile_overcounted_history(session, dry_run=True)

    assert summary["dry_run"] is True
    assert summary["days_corrected"] == 1
    assert summary["bytes_removed"] == 600 * MB

    # Nothing changed on disk.
    session.expire_all()
    a_roll = (await session.execute(
        select(DeviceTrafficRollup).where(DeviceTrafficRollup.device_id == a_id,
                                          DeviceTrafficRollup.record_date == OVER)
    )).scalar_one()
    assert a_roll.bytes_in == 1000 * MB


async def test_apply_scales_the_overcounted_day_to_the_wan(session):
    alice, bob, a_dev, b_dev = await _seed(session)
    a_id, b_id, alice_id, bob_id = a_dev.id, b_dev.id, alice.id, bob.id

    summary = await reconcile_overcounted_history(session, dry_run=False)
    assert summary["days_corrected"] == 1

    session.expire_all()
    dev_rolls = {
        r.device_id: r for r in (await session.execute(
            select(DeviceTrafficRollup).where(DeviceTrafficRollup.record_date == OVER)
        )).scalars()
    }
    # factor = 900 / 1500 = 0.6
    assert dev_rolls[a_id].bytes_in == 600 * MB
    assert dev_rolls[b_id].bytes_in == 300 * MB

    # user rollups rebuilt from the corrected device rollups
    user_rolls = {
        r.user_id: r for r in (await session.execute(
            select(TrafficRollup).where(TrafficRollup.record_date == OVER)
        )).scalars()
    }
    assert user_rolls[alice_id].bytes_in == 600 * MB
    assert user_rolls[bob_id].bytes_in == 300 * MB


async def test_a_day_under_the_wan_is_left_alone(session):
    await _seed(session)
    await reconcile_overcounted_history(session, dry_run=False)

    session.expire_all()
    fine_roll = (await session.execute(
        select(DeviceTrafficRollup).where(DeviceTrafficRollup.record_date == FINE)
    )).scalar_one()
    assert fine_roll.bytes_in == 800 * MB  # unchanged


async def test_a_day_with_no_wan_measurement_is_skipped(session):
    _alice, _bob, a_dev, _b = await _seed(session)
    no_wan = date(2026, 8, 25)
    session.add(DeviceTrafficRollup(device_id=a_dev.id, record_date=no_wan,
                                    bytes_in=9999 * MB, bytes_out=0))
    await session.commit()

    await reconcile_overcounted_history(session, dry_run=False)

    session.expire_all()
    roll = (await session.execute(
        select(DeviceTrafficRollup).where(DeviceTrafficRollup.record_date == no_wan)
    )).scalar_one()
    assert roll.bytes_in == 9999 * MB  # no reference, so untouched
