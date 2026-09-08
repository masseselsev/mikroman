"""Tests for the duplicated-MAC churn: one lease per MAC, and history retention.

The failure was found in a copy of the live database, not guessed: one device row
carried **35 123** `device_history` rows after six days — 17 561 `ip_changed` and
17 561 `hostname_changed`, exactly 8 781 of each of four (address, hostname)
pairs. Two hosts answered with the same MAC from two subnets
(172.16.141.254 `WIN-R1I13RGSAUB`, 172.16.142.254 `WIN-41JDL2PAM9Q`), so every
sweep visited that MAC twice and each visit overwrote what the other had just
written, recording two "changes" per sweep, forever.

The cost was not storage. `Device.history` is eager by relationship, so every
device load materialised all of it — the reason an analytics request spent ~485 ms
loading a log no reader on that path consults.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base, Device, DeviceHistory, Router
from backend.app.schemas.routeros import DHCPLeaseDTO
from backend.app.services.device_manager import DeviceManager, prune_device_history


def _lease(address, mac, host, status="bound"):
    return DHCPLeaseDTO(address=address, mac_address=mac, host_name=host, status=status)


def _manager():
    # No client: the collapse is a pure function of the lease list.
    return DeviceManager(router_client=None, router_id=1)


TWO_HOSTS_ONE_MAC = "AA:BB:CC:DD:EE:01"
OTHER = "11:22:33:44:55:66"


def test_two_leases_on_one_mac_collapse_to_one_device_read():
    """The same MAC from two subnets must produce one record, not two writes.

    Order matters as much as the count: the loop below feeds the pairs in both
    orders and demands the same winner, because the sweep order comes from the
    router and a winner that follows it is the flip-flop being removed.
    """
    first = _lease("172.16.141.254", TWO_HOSTS_ONE_MAC, "WIN-R1I13RGSAUB")
    second = _lease("172.16.142.254", TWO_HOSTS_ONE_MAC, "WIN-41JDL2PAM9Q")

    unrelated = _lease("10.0.0.1", OTHER, "someone-else")
    forward = _manager()._one_lease_per_mac([first, second, unrelated])
    backward = _manager()._one_lease_per_mac([second, first, unrelated])

    assert len(forward) == 2, "three leases, two MACs -> two device records"
    kept = [lease for lease in forward if lease.mac_address == TWO_HOSTS_ONE_MAC]
    assert len(kept) == 1
    assert kept[0].address == "172.16.141.254", "the lower address is the stable pick"
    assert [lease.address for lease in backward
                if lease.mac_address == TWO_HOSTS_ONE_MAC] == [kept[0].address], "the pick must not depend on the order the router returned"


def test_a_bound_lease_wins_over_a_stale_one():
    """`bound` first: an expired row for the same MAC is not evidence."""
    stale = _lease("10.0.0.5", TWO_HOSTS_ONE_MAC, "OLD-HOST", status="waiting")
    live = _lease("10.0.0.9", TWO_HOSTS_ONE_MAC, "NEW-HOST", status="bound")
    kept = _manager()._one_lease_per_mac([stale, live])
    assert [lease.address for lease in kept] == ["10.0.0.9"]


def test_a_single_lease_per_mac_is_untouched():
    leases = [_lease("10.0.0.2", OTHER, "solo"),
              _lease("10.0.0.3", TWO_HOSTS_ONE_MAC, "other")]
    assert len(_manager()._one_lease_per_mac(leases)) == 2


def test_the_duplicate_is_reported_once_not_every_sweep(caplog):
    """One WARNING per MAC, not one per sweep.

    A permanent network condition logged every sweep is 1440 lines a day about
    two machines that were never going to stop sharing an address — the exact
    pattern this round removed from the router's log ring. The condition is still
    worth saying out loud once: it means two hosts answer as one device.
    """
    leases = [_lease("172.16.141.254", TWO_HOSTS_ONE_MAC, "A"),
              _lease("172.16.142.254", TWO_HOSTS_ONE_MAC, "B")]
    manager = _manager()
    with caplog.at_level("WARNING"):
        for _ in range(5):
            manager._one_lease_per_mac(leases)
    warnings = [r for r in caplog.records if "share MAC" in r.getMessage()]
    assert len(warnings) == 1
    assert TWO_HOSTS_ONE_MAC in warnings[0].getMessage()


async def _db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest.mark.asyncio
async def test_device_history_is_pruned_and_the_recent_rows_survive():
    """Retention existed for nothing here at all: rows only left on router purge."""
    engine, factory = await _db()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with factory() as session:
        router = Router(name="R", host="127.0.0.1", is_active=True, is_default=True)
        session.add(router)
        await session.flush()
        device = Device(mac_address=TWO_HOSTS_ONE_MAC, router_id=router.id, is_active=True)
        session.add(device)
        await session.flush()
        # Ages chosen well clear of the 90-day line on both sides, so the
        # assertion does not depend on the host timezone offsetting `now`.
        for i in range(100):
            session.add(DeviceHistory(
                device_id=device.id, mac_address=TWO_HOSTS_ONE_MAC,
                event_type="ip_changed", details=f"ancient {i}",
                created_at=now - timedelta(days=400 + i),
            ))
            session.add(DeviceHistory(
                device_id=device.id, mac_address=TWO_HOSTS_ONE_MAC,
                event_type="ip_changed", details=f"recent {i}",
                created_at=now - timedelta(days=1 + i // 20),
            ))
        await session.commit()

        removed = await prune_device_history(session, retention_days=90)
        left = (await session.execute(select(func.count(DeviceHistory.id)))).scalar()

    assert removed == 100, f"expected the 100 aged rows gone, removed {removed}"
    assert left == 100
    await engine.dispose()


@pytest.mark.asyncio
async def test_the_prune_is_bounded_per_transaction():
    """A 35 000-row delete is the lock-holder that starves every other writer."""
    engine, factory = await _db()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with factory() as session:
        device = Device(mac_address=TWO_HOSTS_ONE_MAC, router_id=None, is_active=True)
        session.add(device)
        await session.flush()
        session.add_all([
            DeviceHistory(device_id=device.id, mac_address=TWO_HOSTS_ONE_MAC,
                          event_type="ip_changed", details=str(i),
                          created_at=now - timedelta(days=200))
            for i in range(1300)
        ])
        await session.commit()

        statements = []
        original = session.execute

        async def counting(stmt, *a, **kw):
            head = str(stmt).split("\n")[0]
            if head.startswith("DELETE"):
                statements.append(head)
            return await original(stmt, *a, **kw)

        session.execute = counting  # type: ignore[method-assign]
        removed = await prune_device_history(session, retention_days=90)

    assert removed == 1300
    assert len(statements) >= 2, (
        "one delete over 1 300 rows is exactly the lock-holder that starves every "
        f"other writer; saw {len(statements)}"
    )
    assert all(" IN " in s for s in statements), "each delete must bound its own batch"
    await engine.dispose()


@pytest.mark.asyncio
async def test_the_per_device_cap_reclaims_rows_a_get_only_would_miss():
    """Age retention cannot fix this table: the churn rows are six days old.

    A 90-day rule leaves 35 123 rows in place until March, still loaded by every
    path that consults history. The cap is the pass that actually shrinks an
    installed database, so it keeps the newest events per device and drops the
    rest — without touching a second device's timeline.
    """
    from backend.app.services.device_manager import cap_device_history

    engine, factory = await _db()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with factory() as session:
        router = Router(name="R", host="127.0.0.1", is_active=True, is_default=True)
        session.add(router)
        await session.flush()
        noisy = Device(mac_address=TWO_HOSTS_ONE_MAC, router_id=router.id, is_active=True)
        quiet = Device(mac_address=OTHER, router_id=router.id, is_active=True)
        session.add_all([noisy, quiet])
        await session.flush()
        # 900 rows on one device (the churn), 3 on the other (a real timeline).
        session.add_all([
            DeviceHistory(device_id=noisy.id, mac_address=TWO_HOSTS_ONE_MAC,
                          event_type="ip_changed", details=f"c{i}", created_at=now)
            for i in range(900)
        ])
        session.add_all([
            DeviceHistory(device_id=quiet.id, mac_address=OTHER,
                          event_type="discovered", details=f"d{i}", created_at=now)
            for i in range(3)
        ])
        await session.commit()

        removed = await cap_device_history(session, keep=200)
        kept_noisy = (await session.execute(
            select(func.count(DeviceHistory.id)).where(DeviceHistory.device_id == noisy.id)
        )).scalar()
        kept_quiet = (await session.execute(
            select(func.count(DeviceHistory.id)).where(DeviceHistory.device_id == quiet.id)
        )).scalar()
        newest = (await session.execute(
            select(DeviceHistory.details).where(DeviceHistory.device_id == noisy.id)
            .order_by(DeviceHistory.id.desc()).limit(1)
        )).scalar_one()

    assert removed == 700
    assert kept_noisy == 200, "the newest window is kept"
    assert kept_quiet == 3, "a device under the cap is not trimmed"
    assert newest == "c899", "what is kept must be the newest rows, not any 200"
    await engine.dispose()
