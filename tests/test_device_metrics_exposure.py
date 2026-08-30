"""Per-device live rate and daily volume must reach the API.

Both figures are already computed for the per-user aggregates - the tracker
returns a rate per device and DeviceTrafficRollup stores a daily total per
device - but only the user-level sums were exposed, so the dashboard could not
show which specific device was consuming the bandwidth.
"""
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base, Device, DeviceTrafficRollup, User
from backend.app.services.traffic_accounting import live_rate_tracker
from backend.app.services.traffic_controller import TrafficController


class FakeRouter:
    """Serves mangle counters; no queues are consulted for live figures."""

    def __init__(self, rules):
        self._rules = rules

    async def get_mangle_rules(self):
        return self._rules


def acct_rules(pairs):
    """pairs: {device_id: (up_bytes, down_bytes)}"""
    out = []
    for device_id, (up, down) in pairs.items():
        out.append({".id": f"*u{device_id}", "comment": f"mikroman:acct:dev_{device_id}:up", "bytes": str(up)})
        out.append({".id": f"*d{device_id}", "comment": f"mikroman:acct:dev_{device_id}:down", "bytes": str(down)})
    return out


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture(autouse=True)
def reset_tracker():
    """The tracker is process-wide; clear it so tests do not see each other."""
    live_rate_tracker._previous.clear()
    yield
    live_rate_tracker._previous.clear()


async def _seed(session):
    user = User(name="Mark", speed_limit="unlimited")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    devices = []
    for i, ip in enumerate(["192.168.88.242", "192.168.88.243"], start=1):
        d = Device(
            user_id=user.id, mac_address=f"AA:BB:CC:00:00:{i:02X}",
            ip_address=ip, custom_name=f"dev{i}", is_active=True,
        )
        session.add(d)
        devices.append(d)
    await session.commit()
    for d in devices:
        await session.refresh(d)
    return user, devices


@pytest.mark.asyncio
async def test_per_device_rates_are_returned(session):
    user, devices = await _seed(session)
    d1, d2 = devices
    ctrl = TrafficController(FakeRouter(acct_rules({d1.id: (0, 0), d2.id: (0, 0)})))

    # First call establishes the tracker reference.
    await ctrl.get_realtime_traffic_stats(session)

    ctrl.router_client = FakeRouter(acct_rules({
        d1.id: (10_000, 500_000),
        d2.id: (2_000, 20_000),
    }))
    stats = await ctrl.get_realtime_traffic_stats(session)

    row = next(s for s in stats if s["user_id"] == user.id)
    per_device = row["devices"]
    assert set(per_device) == {d1.id, d2.id}
    # d1 moved far more than d2, and both must be strictly positive.
    assert per_device[d1.id]["current_rate_in"] > per_device[d2.id]["current_rate_in"] > 0
    assert per_device[d1.id]["current_rate_out"] > 0

    # The user aggregate must equal the sum of its devices.
    assert row["current_rate_in"] == pytest.approx(
        per_device[d1.id]["current_rate_in"] + per_device[d2.id]["current_rate_in"], rel=0.01
    )


@pytest.mark.asyncio
async def test_per_device_daily_volume_is_returned(session):
    user, devices = await _seed(session)
    d1, d2 = devices
    today = date.today()
    session.add(DeviceTrafficRollup(device_id=d1.id, record_date=today, bytes_in=2_100_000_000, bytes_out=45_000_000))
    session.add(DeviceTrafficRollup(device_id=d2.id, record_date=today, bytes_in=15_000, bytes_out=9_000))
    await session.commit()

    ctrl = TrafficController(FakeRouter([]))
    stats = await ctrl.get_realtime_traffic_stats(session)
    per_device = next(s for s in stats if s["user_id"] == user.id)["devices"]

    assert per_device[d1.id]["bytes_today_in"] == 2_100_000_000
    assert per_device[d1.id]["bytes_today_out"] == 45_000_000
    assert per_device[d2.id]["bytes_today_in"] == 15_000


@pytest.mark.asyncio
async def test_device_without_a_sample_reports_zero_not_stale(session):
    """A device with no counter reads 0 - never a leftover figure."""
    user, devices = await _seed(session)
    ctrl = TrafficController(FakeRouter([]))

    stats = await ctrl.get_realtime_traffic_stats(session)
    per_device = next(s for s in stats if s["user_id"] == user.id)["devices"]

    for d in devices:
        assert per_device[d.id]["current_rate_in"] == 0
        assert per_device[d.id]["current_rate_out"] == 0
        assert per_device[d.id]["bytes_today_in"] == 0
