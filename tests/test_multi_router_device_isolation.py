"""Per-router isolation of the device-identity heuristics.

A device discovered on one router must never be matched against - offered for
linking to, or silently merged into - a user or device on another router. The
hostname a phone broadcasts ("NamasT3k", "iPhone", ...) is not unique across
sites, so an unscoped match reaches straight across the boundary the rest of
the app maintains.

Regression for: adding a new router surfaced "Identical hostname 'NamasT3k' on
user 'Mark'" in its unassigned inbox, where 'Mark' lived on a different router.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base, Device, Router, User
from backend.app.services.device_linking import find_link_suggestions
from backend.app.services.device_manager import DeviceManager

# Locally-administered unicast addresses - what phones present when they
# randomise. Second nibble in {2,6,A,E}.
PRIVATE_MAC_R1 = "D6:3D:1B:54:03:2F"
PRIVATE_MAC_R2 = "F2:AA:BB:CC:DD:EE"


class _FakeRouter:
    """DeviceManager only touches the client for a live sweep; the suggestion
    and consolidation paths under test never call it."""


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _two_sites_sharing_a_hostname(session):
    """Router 1 has user 'Mark' with an assigned 'NamasT3k'. Router 2 has an
    unassigned 'NamasT3k' that just showed up. Same hostname, different sites."""
    session.add_all([
        Router(id=1, name="Home", host="10.0.0.1", username="a", password="b"),
        Router(id=2, name="Edge", host="10.0.0.2", username="a", password="b"),
    ])
    mark = User(name="Mark", speed_limit="unlimited", router_id=1)
    session.add(mark)
    await session.flush()

    session.add_all([
        Device(
            mac_address=PRIVATE_MAC_R1, router_id=1, user_id=mark.id,
            ip_address="192.168.1.50", hostname="NamasT3k",
            custom_name="Mark's laptop", vendor="Apple", is_active=True,
        ),
        Device(
            mac_address=PRIVATE_MAC_R2, router_id=2, user_id=None,
            ip_address="192.168.222.222", hostname="NamasT3k",
            vendor="Apple", is_active=True,
        ),
    ])
    await session.commit()
    return mark


@pytest.mark.asyncio
async def test_merge_suggestions_do_not_cross_routers(session):
    await _two_sites_sharing_a_hostname(session)

    # Viewed from the new router, nothing on router 1 is a candidate.
    dev_mgr = DeviceManager(_FakeRouter(), router_id=2)
    suggestions = await dev_mgr.find_merge_suggestions(session)
    assert suggestions == [], (
        "a device on router 2 was matched against a user on router 1"
    )


@pytest.mark.asyncio
async def test_link_suggestions_do_not_cross_routers(session):
    await _two_sites_sharing_a_hostname(session)

    assert await find_link_suggestions(session, router_id=2) == []
    assert await find_link_suggestions(session, router_id=1) == []


@pytest.mark.asyncio
async def test_rotation_consolidation_does_not_fold_across_routers(session):
    mark = await _two_sites_sharing_a_hostname(session)

    dev_mgr = DeviceManager(_FakeRouter(), router_id=2)
    removed = await dev_mgr.consolidate_rotated_devices(session, settle_hours=0)

    assert removed == 0
    r2_dev = (
        await session.execute(
            Device.__table__.select().where(Device.mac_address == PRIVATE_MAC_R2)
        )
    ).first()
    assert r2_dev is not None, "router 2's device was absorbed by a cross-router merge"
    assert r2_dev.user_id is None, "router 2's device was adopted onto a router 1 user"
    assert r2_dev.router_id == 2

    # Router 1's device and its owner are untouched.
    r1_dev = (
        await session.execute(
            Device.__table__.select().where(Device.mac_address == PRIVATE_MAC_R1)
        )
    ).first()
    assert r1_dev.user_id == mark.id
