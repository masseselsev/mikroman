"""Live RX/TX rates must come from firewall counters, not Simple Queue rates.

The user cards read their live speedometers from the ``rate`` field of each
managed Simple Queue. On RouterOS 7.x that field can freeze permanently: one
user showed a constant 488 Kbps / 2.4 Mbps for hours while the entire WAN was
doing 10 Kbps, and every other user showed a flat 0 bps despite real traffic.
Rates are now derived by differentiating the per-device mangle byte counters.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base, Device, User
from backend.app.services.traffic_accounting import LiveRateTracker


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


def rules(device_id, up_bytes, down_bytes):
    return [
        {".id": "*1", "comment": f"mikroman:acct:dev_{device_id}:up", "bytes": str(up_bytes)},
        {".id": "*2", "comment": f"mikroman:acct:dev_{device_id}:down", "bytes": str(down_bytes)},
    ]


def test_first_sample_reports_no_rate():
    """A single reading cannot yield a rate; it only establishes a reference."""
    tracker = LiveRateTracker()
    result = tracker.sample(rules(1, 1_000, 5_000), now=100.0)
    assert result == {}


def test_rate_is_bits_per_second_over_elapsed_time():
    tracker = LiveRateTracker()
    tracker.sample(rules(1, 1_000, 5_000), now=100.0)
    # 2 seconds later: +250,000 bytes down, +25,000 bytes up
    result = tracker.sample(rules(1, 26_000, 255_000), now=102.0)

    # 250,000 bytes / 2s = 125,000 B/s = 1,000,000 bits/s
    assert result[1]["rx_bps"] == pytest.approx(1_000_000)
    # 25,000 bytes / 2s = 12,500 B/s = 100,000 bits/s
    assert result[1]["tx_bps"] == pytest.approx(100_000)


def test_counter_reset_does_not_produce_negative_or_absurd_rate():
    tracker = LiveRateTracker()
    tracker.sample(rules(1, 900_000, 900_000), now=10.0)
    result = tracker.sample(rules(1, 10, 20), now=11.0)
    assert result[1]["rx_bps"] >= 0
    assert result[1]["tx_bps"] >= 0


def test_zero_elapsed_time_is_ignored():
    """Two readings in the same instant must not divide by zero."""
    tracker = LiveRateTracker()
    tracker.sample(rules(1, 100, 100), now=5.0)
    result = tracker.sample(rules(1, 500, 500), now=5.0)
    assert result == {}


def test_idle_device_reports_zero_not_a_stale_value():
    """The frozen-rate symptom: no new bytes must read as 0, never as the last value."""
    tracker = LiveRateTracker()
    tracker.sample(rules(1, 1_000, 5_000), now=100.0)
    tracker.sample(rules(1, 26_000, 255_000), now=101.0)
    idle = tracker.sample(rules(1, 26_000, 255_000), now=102.0)
    assert idle[1]["rx_bps"] == 0
    assert idle[1]["tx_bps"] == 0


@pytest.mark.asyncio
async def test_user_live_rate_is_the_sum_of_its_devices(session):
    """Per-user speedometers aggregate their devices' measured rates."""
    from backend.app.services.traffic_accounting import aggregate_user_rates

    user = User(name="Mark", speed_limit="unlimited")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    for i, ip in enumerate(["192.168.88.242", "192.168.88.243"], start=1):
        session.add(Device(
            user_id=user.id, mac_address=f"AA:BB:CC:00:00:{i:02X}",
            ip_address=ip, is_active=True,
        ))
    await session.commit()

    devices = (await session.execute(Device.__table__.select())).fetchall()
    per_device = {
        devices[0].id: {"rx_bps": 1_000_000, "tx_bps": 200_000},
        devices[1].id: {"rx_bps": 500_000, "tx_bps": 100_000},
    }

    totals = await aggregate_user_rates(session, per_device)
    assert totals[user.id]["rx_bps"] == 1_500_000
    assert totals[user.id]["tx_bps"] == 300_000
