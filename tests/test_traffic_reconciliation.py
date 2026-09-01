"""The analytics range must add up.

    gateway = Σ users + unassigned + router_self + unaccounted - over_accounted

Per-user volume is derived from the devices a profile owns *now*, not read from
the parallel ``traffic_rollups`` ledger. The two are written from the same
deltas but keyed differently - the device ledger follows the device, the user
ledger records whoever owned it at the moment of each poll - so a device that
changes hands makes them disagree permanently.

Observed on a live install: a laptop reassigned mid-day left 1.2 GB booked to
its previous owner and 0.4 GB (earned before anyone claimed it) booked to
nobody, so the "by user" donut read 1.6 GB against a 2.0 GB gateway total with
no way to account for the difference.
"""
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import (
    AppSetting,
    Base,
    Device,
    DeviceTrafficRollup,
    Router,
    RouterSelfTrafficRollup,
    RouterTrafficRollup,
    TrafficRollup,
    User,
)
from backend.app.services.analytics_engine import AnalyticsEngine

MB = 1024 ** 2
DAY = date(2026, 9, 2)


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
    session.add(AppSetting(key="monitored_interfaces_1", value='["ether1"]'))
    alice = User(name="Alice", speed_limit="unlimited")
    bob = User(name="Bob", speed_limit="unlimited")
    session.add_all([alice, bob])
    await session.commit()
    await session.refresh(alice)
    await session.refresh(bob)
    return alice, bob


def _dev(**kw):
    kw.setdefault("router_id", 1)
    kw.setdefault("is_active", True)
    return Device(**kw)


async def _analyse(session, router_id=1):
    # The engine re-queries users; expire so the selectin-loaded `devices`
    # collection is rebuilt rather than served stale from the identity map.
    session.expire_all()
    return await AnalyticsEngine.get_historical_traffic(
        session=session, start_date=DAY, end_date=DAY, router_id=router_id,
    )


async def test_a_reassigned_device_takes_its_history_to_its_new_owner(session):
    """The exact live failure: the ledger still names the old owner."""
    alice, bob = await _seed(session)
    laptop = _dev(user_id=bob.id, mac_address="AA:BB:CC:00:00:01", ip_address="10.0.0.5",
                  custom_name="Laptop")
    session.add(laptop)
    await session.commit()
    await session.refresh(laptop)

    # The device ledger holds all of it...
    session.add(DeviceTrafficRollup(device_id=laptop.id, record_date=DAY,
                                    bytes_in=900 * MB, bytes_out=100 * MB))
    # ...while the user ledger still credits Alice, who owned it earlier today.
    session.add(TrafficRollup(user_id=alice.id, record_date=DAY,
                              bytes_in=900 * MB, bytes_out=100 * MB))
    session.add(RouterTrafficRollup(router_id=1, record_date=DAY,
                                    bytes_in=900 * MB, bytes_out=100 * MB))
    await session.commit()

    resp = await _analyse(session)
    by_name = {u.user_name: u for u in resp.users}

    assert by_name["Bob"].total_bytes == 1000 * MB, "current owner gets the history"
    assert by_name["Alice"].total_bytes == 0, "the stale ledger entry is ignored"


async def test_users_plus_unassigned_equals_the_device_total(session):
    alice, _bob = await _seed(session)
    owned = _dev(user_id=alice.id, mac_address="AA:BB:CC:00:00:02", ip_address="10.0.0.6")
    orphan = _dev(user_id=None, mac_address="AA:BB:CC:00:00:03", ip_address="10.0.0.7")
    session.add_all([owned, orphan])
    await session.commit()
    await session.refresh(owned)
    await session.refresh(orphan)

    session.add(DeviceTrafficRollup(device_id=owned.id, record_date=DAY,
                                    bytes_in=300 * MB, bytes_out=0))
    session.add(DeviceTrafficRollup(device_id=orphan.id, record_date=DAY,
                                    bytes_in=200 * MB, bytes_out=0))
    session.add(RouterTrafficRollup(router_id=1, record_date=DAY,
                                    bytes_in=500 * MB, bytes_out=0))
    await session.commit()

    resp = await _analyse(session)
    users_total = sum(u.total_bytes for u in resp.users)
    devices_total = sum(d.total_bytes for d in resp.devices)

    assert resp.unassigned.total_bytes == 200 * MB
    assert resp.unassigned.device_count == 1
    assert users_total + resp.unassigned.total_bytes == devices_total


async def test_the_range_reconciles_to_the_gateway_total(session):
    alice, _bob = await _seed(session)
    owned = _dev(user_id=alice.id, mac_address="AA:BB:CC:00:00:04", ip_address="10.0.0.8")
    session.add(owned)
    await session.commit()
    await session.refresh(owned)

    session.add(DeviceTrafficRollup(device_id=owned.id, record_date=DAY,
                                    bytes_in=600 * MB, bytes_out=0))
    session.add(RouterSelfTrafficRollup(router_id=1, record_date=DAY,
                                        bytes_in=100 * MB, bytes_out=0))
    # 800 measured at the WAN, 700 attributable -> 100 unaccounted.
    session.add(RouterTrafficRollup(router_id=1, record_date=DAY,
                                    bytes_in=800 * MB, bytes_out=0))
    await session.commit()

    resp = await _analyse(session)
    parts = (
        sum(u.total_bytes for u in resp.users)
        + resp.unassigned.total_bytes
        + resp.router_self.total_bytes
        + resp.unaccounted_bytes
        - resp.over_accounted_bytes
    )
    assert resp.unaccounted_bytes == 100 * MB
    assert resp.over_accounted_bytes == 0
    assert parts == resp.gateway.total_bytes


async def test_counting_more_than_the_wan_carried_is_reported_not_hidden(session):
    """Per-device rules match the forward chain by address with no WAN
    constraint, so LAN-to-LAN traffic is counted at both ends and the
    attributed sum can exceed the gateway. Measured at -8.4% on a live
    install; it must surface rather than quietly skew the shares."""
    alice, _bob = await _seed(session)
    owned = _dev(user_id=alice.id, mac_address="AA:BB:CC:00:00:05", ip_address="10.0.0.9")
    session.add(owned)
    await session.commit()
    await session.refresh(owned)

    session.add(DeviceTrafficRollup(device_id=owned.id, record_date=DAY,
                                    bytes_in=900 * MB, bytes_out=0))
    session.add(RouterTrafficRollup(router_id=1, record_date=DAY,
                                    bytes_in=800 * MB, bytes_out=0))
    await session.commit()

    resp = await _analyse(session)
    assert resp.over_accounted_bytes == 100 * MB
    assert resp.unaccounted_bytes == 0


async def test_another_routers_clients_stay_out_of_this_routers_breakdown(session):
    alice, _bob = await _seed(session)
    session.add(Router(id=2, name="Office", host="10.9.9.1", username="u", password="p"))
    await session.commit()

    here = _dev(user_id=alice.id, mac_address="AA:BB:CC:00:00:06", ip_address="10.0.0.10")
    there = _dev(router_id=2, user_id=None, mac_address="AA:BB:CC:00:00:07", ip_address="172.16.0.5")
    session.add_all([here, there])
    await session.commit()
    await session.refresh(here)
    await session.refresh(there)

    session.add(DeviceTrafficRollup(device_id=here.id, record_date=DAY,
                                    bytes_in=100 * MB, bytes_out=0))
    session.add(DeviceTrafficRollup(device_id=there.id, record_date=DAY,
                                    bytes_in=500 * MB, bytes_out=0))
    session.add(RouterTrafficRollup(router_id=1, record_date=DAY,
                                    bytes_in=100 * MB, bytes_out=0))
    await session.commit()

    resp = await _analyse(session, router_id=1)
    assert sum(d.total_bytes for d in resp.devices) == 100 * MB
    assert resp.unassigned.total_bytes == 0
