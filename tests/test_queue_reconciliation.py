"""Managed Simple Queues must not outlive the user or device they belong to.

Nothing previously removed a managed queue once its owner disappeared: deleting
a device, or switching one from a custom limit back to "inherit user", left its
queue behind on RouterOS forever. A stranded queue still carries its old
``max-limit`` and target, so if that address is later handed to another host -
or to the router's own uplink - the leftover queue silently throttles it.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base, Device, User
from backend.app.schemas.traffic import SimpleQueueItem
from backend.app.services.traffic_controller import TrafficController


class FakeRouter:
    def __init__(self, queues):
        self.queues = list(queues)
        self.deleted = []

    async def get_simple_queues(self):
        return list(self.queues)

    async def delete_simple_queue(self, queue_id):
        self.deleted.append(queue_id)
        self.queues = [q for q in self.queues if q.id != queue_id]


def q(qid, name, comment, max_limit="0/0"):
    return SimpleQueueItem(
        id=qid, name=name, target="192.168.88.10/32",
        max_limit=max_limit, comment=comment,
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


async def _user_with_device(session, name="Mark", ip="192.168.88.10", speed_limit="default"):
    user = User(name=name, speed_limit="unlimited")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    device = Device(
        user_id=user.id, mac_address=f"AA:BB:CC:00:00:{user.id:02X}",
        ip_address=ip, is_active=True, speed_limit=speed_limit,
    )
    session.add(device)
    await session.commit()
    await session.refresh(device)
    return user, device


@pytest.mark.asyncio
async def test_queue_for_deleted_device_is_removed(session):
    _, device = await _user_with_device(session, speed_limit="5M/5M")
    router = FakeRouter([q("*4", "mikroman-unassigned-dev99", "mikroman:managed:dev_99", "5M/5M")])
    ctrl = TrafficController(router)

    removed = await ctrl.reconcile_managed_queues(session)

    assert removed == 1
    assert router.deleted == ["*4"], "queue for a device that no longer exists must be deleted"


@pytest.mark.asyncio
async def test_queue_for_deleted_user_is_removed(session):
    await _user_with_device(session)
    router = FakeRouter([q("*7", "mikroman-Ghost", "mikroman:managed:user_999")])
    ctrl = TrafficController(router)

    assert await ctrl.reconcile_managed_queues(session) == 1
    assert router.deleted == ["*7"]


@pytest.mark.asyncio
async def test_live_queues_are_kept(session):
    user, device = await _user_with_device(session, speed_limit="5M/5M")
    router = FakeRouter([
        q("*1", "mikroman-Mark", f"mikroman:managed:user_{user.id}"),
        q("*2", "mikroman-Mark-phone", f"mikroman:managed:dev_{device.id}", "5M/5M"),
    ])
    ctrl = TrafficController(router)

    assert await ctrl.reconcile_managed_queues(session) == 0
    assert router.deleted == []


@pytest.mark.asyncio
async def test_child_queue_removed_when_device_reverts_to_inherit(session):
    """Assigning a device to a user and clearing its custom limit must drop the child queue."""
    user, device = await _user_with_device(session, speed_limit="default")
    router = FakeRouter([
        q("*1", "mikroman-Mark", f"mikroman:managed:user_{user.id}"),
        # leftover quarantine queue from when the device was unassigned
        q("*2", "mikroman-unassigned-dev1", f"mikroman:managed:dev_{device.id}", "5M/5M"),
    ])
    ctrl = TrafficController(router)

    assert await ctrl.reconcile_managed_queues(session) == 1
    assert router.deleted == ["*2"]


@pytest.mark.asyncio
async def test_unmanaged_queues_are_never_touched(session):
    """Hand-made queues belonging to the operator must survive untouched."""
    await _user_with_device(session)
    router = FakeRouter([
        q("*9", "office-backup", "nightly backup shaping"),
        q("*10", "no-comment-queue", None),
    ])
    ctrl = TrafficController(router)

    assert await ctrl.reconcile_managed_queues(session) == 0
    assert router.deleted == []


@pytest.mark.asyncio
async def test_legacy_name_tagged_queue_for_live_user_is_kept(session):
    """Queues written by older versions use a name-based tag; do not delete them."""
    await _user_with_device(session, name="Mark")
    router = FakeRouter([q("*1", "mikroman-Mark", "mikroman:managed:Mark")])
    ctrl = TrafficController(router)

    assert await ctrl.reconcile_managed_queues(session) == 0
    assert router.deleted == []
