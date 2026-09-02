"""Archive / restore / purge / hardware-swap of a managed router.

FK enforcement is off on the SQLite deployment, so purge must delete every
child row itself. Archive must leave all of it in place. These check both.
"""
from datetime import date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import (
    AppSetting,
    Base,
    Device,
    DeviceTrafficRollup,
    InterfaceMetric,
    Router,
    RouterTrafficRollup,
    SystemMetric,
    TrafficRollup,
    User,
    UserTrafficBucket,
)
from backend.app.services import router_lifecycle


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _seed_router_with_data(session, rid: int, *, is_default: bool, serial: str) -> None:
    session.add(Router(
        id=rid, name=f"R{rid}", host=f"10.0.0.{rid}", username="a", password="b",
        is_default=is_default, serial_number=serial,
    ))
    user = User(name=f"U{rid}", speed_limit="unlimited", router_id=rid)
    session.add(user)
    await session.flush()
    dev = Device(
        user_id=user.id, router_id=rid, mac_address=f"AA:BB:CC:00:00:0{rid}",
        ip_address=f"192.168.{rid}.10", is_active=True,
    )
    session.add(dev)
    await session.flush()
    today = date.today()
    session.add_all([
        TrafficRollup(user_id=user.id, record_date=today, bytes_in=10, bytes_out=1),
        DeviceTrafficRollup(device_id=dev.id, record_date=today, bytes_in=10, bytes_out=1),
        UserTrafficBucket(user_id=user.id, bucket_start=datetime(today.year, today.month, today.day),
                          bytes_in=5, bytes_out=1),
        RouterTrafficRollup(router_id=rid, record_date=today, bytes_in=100, bytes_out=10),
        SystemMetric(router_id=rid, cpu_load=5.0, memory_used_bytes=1, memory_total_bytes=2),
        InterfaceMetric(router_id=rid, interface_name="ether1", rx_rate_bps=1.0, tx_rate_bps=1.0,
                        rx_bytes_total=1, tx_bytes_total=1),
        AppSetting(key=f"monitored_interfaces_{rid}", value='["ether1"]'),
        AppSetting(key=f"quota_limit_bytes_{rid}", value="123"),
    ])
    await session.commit()


async def _count(session, model, whereclause=None) -> int:
    stmt = select(func.count()).select_from(model)
    if whereclause is not None:
        stmt = stmt.where(whereclause)
    return int((await session.execute(stmt)).scalar_one())


@pytest.mark.asyncio
async def test_archive_hides_the_router_but_keeps_every_row(session):
    await _seed_router_with_data(session, 1, is_default=True, serial="S-ONE")
    await _seed_router_with_data(session, 2, is_default=False, serial="S-TWO")

    r2 = await session.get(Router, 2)
    await router_lifecycle.archive_router(session, r2)
    await session.commit()

    assert r2.archived_at is not None
    assert r2.is_default is False and r2.is_active is False
    # Not a single attached row was touched.
    assert await _count(session, User, User.router_id == 2) == 1
    assert await _count(session, Device, Device.router_id == 2) == 1
    assert await _count(session, RouterTrafficRollup, RouterTrafficRollup.router_id == 2) == 1
    assert await _count(session, AppSetting, AppSetting.key.like("%_2")) == 2
    # It is findable by serial for a later re-add.
    found = await router_lifecycle.find_archived_by_serial(session, "S-TWO")
    assert found is not None and found.id == 2


@pytest.mark.asyncio
async def test_archiving_the_default_promotes_another_live_router(session):
    await _seed_router_with_data(session, 1, is_default=True, serial="S-ONE")
    await _seed_router_with_data(session, 2, is_default=False, serial="S-TWO")

    await router_lifecycle.archive_router(session, await session.get(Router, 1))
    await session.commit()

    r2 = await session.get(Router, 2)
    assert r2.is_default is True


@pytest.mark.asyncio
async def test_purge_removes_the_router_and_all_of_its_data(session):
    await _seed_router_with_data(session, 1, is_default=True, serial="S-ONE")
    await _seed_router_with_data(session, 2, is_default=False, serial="S-TWO")

    counts = await router_lifecycle.purge_router(session, await session.get(Router, 2))
    await session.commit()

    assert await session.get(Router, 2) is None
    for model, where in [
        (User, User.router_id == 2),
        (Device, Device.router_id == 2),
        (RouterTrafficRollup, RouterTrafficRollup.router_id == 2),
        (SystemMetric, SystemMetric.router_id == 2),
        (InterfaceMetric, InterfaceMetric.router_id == 2),
        (AppSetting, AppSetting.key.like("%\\_2", escape="\\")),
    ]:
        assert await _count(session, model, where) == 0, model.__tablename__
    # User/device-scoped rows went too, addressed via the now-gone ids.
    assert await _count(session, TrafficRollup) == 1          # only router 1's
    assert await _count(session, DeviceTrafficRollup) == 1
    assert await _count(session, UserTrafficBucket) == 1
    # Router 1 is completely untouched.
    assert await _count(session, User, User.router_id == 1) == 1
    assert await _count(session, AppSetting, AppSetting.key.like("%\\_1", escape="\\")) == 2
    assert counts["users"] == 1 and counts["devices"] == 1


@pytest.mark.asyncio
async def test_reset_hardware_history_drops_metrics_but_keeps_traffic(session):
    await _seed_router_with_data(session, 1, is_default=True, serial="S-ONE")

    await router_lifecycle.reset_hardware_history(session, 1)
    await session.commit()

    assert await _count(session, SystemMetric, SystemMetric.router_id == 1) == 0
    assert await _count(session, InterfaceMetric, InterfaceMetric.router_id == 1) == 0
    # The billing-relevant totals stay.
    assert await _count(session, RouterTrafficRollup, RouterTrafficRollup.router_id == 1) == 1
    assert await _count(session, TrafficRollup) == 1


@pytest.mark.asyncio
async def test_restore_reattaches_and_can_refresh_the_connection(session):
    await _seed_router_with_data(session, 1, is_default=True, serial="S-ONE")
    await _seed_router_with_data(session, 2, is_default=False, serial="S-TWO")
    r2 = await session.get(Router, 2)
    await router_lifecycle.archive_router(session, r2)
    await session.commit()

    await router_lifecycle.restore_router(session, r2, {
        "name": "R2-new-site", "host": "203.0.113.9", "port": 8443,
        "use_ssl": True, "username": "admin", "password": "fresh",
        "serial_number": "S-TWO",
    })
    await session.commit()

    assert r2.archived_at is None and r2.is_active is True
    assert r2.host == "203.0.113.9" and r2.name == "R2-new-site"
    assert await _count(session, User, User.router_id == 2) == 1
    assert await _count(session, RouterTrafficRollup, RouterTrafficRollup.router_id == 2) == 1
