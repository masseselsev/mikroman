"""How the coverage figure is computed, and what it is allowed to imply.

The version this replaces divided the whole range's attributed bytes by the
whole range's gateway bytes. On a live install whose range reached one day past
the day per-device accounting was switched on, that read 51.6% - which looks
exactly like "half your traffic was lost" and was not: 44 of the 47 missing GB
were recorded on days no per-device counter had been installed yet.

Coverage is now judged over the measured window only, and the pre-accounting
volume is reported as its own number.
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
    RouterTrafficRollup,
    TrafficRollup,
    User,
)
from backend.app.services.analytics_engine import AnalyticsEngine
from backend.app.services.traffic_accounting import STARTED_SETTING_KEY

GB = 1024 ** 3


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


# --- the assessment in isolation ---------------------------------------------

def test_pre_accounting_volume_is_reported_not_smeared_into_the_ratio():
    """The exact shape that produced the alarming 51.6% on the live install."""
    health = AnalyticsEngine._assess_accounting_health(
        gateway_bytes=99 * GB,
        accounted_bytes=51 * GB,
        has_gateway_sample=True,
        accounting_started=date(2026, 8, 30),
        # 08-29 had no accounting at all, 08-30 was the partial start day
        pre_accounting_bytes=67 * GB,
        measured_bytes=32 * GB,
        measured_accounted_bytes=31 * GB,
    )
    assert health.status == "partial"
    # Judged over the days accounting actually ran end to end, not the range.
    assert health.coverage_pct == pytest.approx(96.88, abs=0.05)
    assert health.pre_accounting_bytes == 67 * GB
    assert health.measured_bytes == 32 * GB
    assert health.accounting_started == date(2026, 8, 30)
    # The range totals are still reported truthfully alongside it.
    assert health.gateway_bytes == 99 * GB
    assert health.accounted_bytes == 51 * GB


def test_a_genuinely_broken_accounting_path_still_reads_as_degraded():
    """The pre-accounting notice must never mask a real fault."""
    health = AnalyticsEngine._assess_accounting_health(
        gateway_bytes=100 * GB,
        accounted_bytes=2 * GB,
        has_gateway_sample=True,
        accounting_started=date(2026, 8, 30),
        pre_accounting_bytes=40 * GB,
        measured_bytes=60 * GB,
        measured_accounted_bytes=2 * GB,
    )
    assert health.status == "degraded"
    assert health.coverage_pct == pytest.approx(3.33, abs=0.05)


def test_a_range_entirely_after_the_start_day_is_plain_ok():
    health = AnalyticsEngine._assess_accounting_health(
        gateway_bytes=20 * GB,
        accounted_bytes=19 * GB,
        has_gateway_sample=True,
        accounting_started=date(2026, 8, 30),
        pre_accounting_bytes=0,
        measured_bytes=20 * GB,
        measured_accounted_bytes=19 * GB,
    )
    assert health.status == "ok"
    assert health.coverage_pct == pytest.approx(95.0, abs=0.05)


def test_without_a_start_marker_the_whole_range_is_the_denominator():
    """A fresh install that never recorded a marker keeps the old behaviour."""
    health = AnalyticsEngine._assess_accounting_health(
        gateway_bytes=10 * GB,
        accounted_bytes=9 * GB,
        has_gateway_sample=True,
        accounting_started=None,
    )
    assert health.status == "ok"
    assert health.coverage_pct == pytest.approx(90.0, abs=0.05)


def test_no_samples_at_all_is_no_data():
    health = AnalyticsEngine._assess_accounting_health(
        gateway_bytes=0, accounted_bytes=0, has_gateway_sample=False
    )
    assert health.status == "no_data"
    assert health.coverage_pct == 0.0


# --- end to end through the analytics query ----------------------------------

@pytest.mark.asyncio
async def test_the_engine_splits_the_range_at_the_accounting_start_day(session):
    """Reproduces the live install's day-by-day shape and checks the verdict.

    Gateway sees four days; per-device accounting only started on the second and
    so only covers the last two in full. The first two days' volume must land in
    ``pre_accounting_bytes``, and coverage must describe the last two.
    """
    session.add(Router(id=1, name="Main", host="10.0.0.1", username="u", password="p"))
    user = User(name="Mark", speed_limit="unlimited")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    device = Device(user_id=user.id, router_id=1, mac_address="AA:BB:CC:00:11:22",
                    ip_address="192.168.88.50", is_active=True)
    session.add(device)
    session.add(AppSetting(key=STARTED_SETTING_KEY, value="2026-08-30"))
    await session.commit()
    await session.refresh(device)

    days = [date(2026, 8, 29), date(2026, 8, 30), date(2026, 8, 31), date(2026, 9, 1)]
    gateway = [25 * GB, 42 * GB, 24 * GB, 8 * GB]
    # Device counters exist only from the start day, and that day is partial.
    accounted = [0, 21 * GB, 23 * GB, 8 * GB]
    for day, gw, acc in zip(days, gateway, accounted):
        session.add(RouterTrafficRollup(router_id=1, record_date=day,
                                        bytes_in=gw, bytes_out=0))
        if acc:
            session.add(DeviceTrafficRollup(device_id=device.id, record_date=day,
                                            bytes_in=acc, bytes_out=0))
            session.add(TrafficRollup(user_id=user.id, record_date=day,
                                      bytes_in=acc, bytes_out=0))
    await session.commit()

    data = await AnalyticsEngine.get_historical_traffic(
        session, start_date=days[0], end_date=days[-1], router_id=1
    )
    health = data.accounting_health

    assert health.accounting_started == date(2026, 8, 30)
    # 08-29 and the partial 08-30 are unattributable by construction.
    assert health.pre_accounting_bytes == 67 * GB
    assert health.measured_bytes == 32 * GB
    assert health.measured_accounted_bytes == 31 * GB
    assert health.coverage_pct == pytest.approx(96.88, abs=0.05)
    assert health.status == "partial"
    # The old ratio would have been this - the number that read as "50% lost".
    old_style = round(health.accounted_bytes / health.gateway_bytes * 100, 2)
    assert old_style < 60.0


@pytest.mark.asyncio
async def test_the_banner_split_adds_up_to_the_breakdown_tables(session):
    """measured + earlier must equal the range total the user table shows.

    The percentage describes a sub-window, so its attributed figure is smaller
    than the tables underneath it. Reported on its own that difference reads as
    a counting bug - which is exactly how it was read - so the two halves must
    visibly add back up to the number the tables total.
    """
    session.add(Router(id=1, name="Main", host="10.0.0.1", username="u", password="p"))
    user = User(name="Mark", speed_limit="unlimited")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    device = Device(user_id=user.id, router_id=1, mac_address="AA:BB:CC:00:11:22",
                    ip_address="192.168.88.50", is_active=True)
    session.add(device)
    session.add(AppSetting(key=STARTED_SETTING_KEY, value="2026-08-30"))
    await session.commit()
    await session.refresh(device)

    days = [date(2026, 8, 29), date(2026, 8, 30), date(2026, 8, 31), date(2026, 9, 1)]
    gateway = [25 * GB, 42 * GB, 24 * GB, 10 * GB]
    accounted = [0, 21 * GB, 20 * GB, 10 * GB]
    for day, gw, acc in zip(days, gateway, accounted):
        session.add(RouterTrafficRollup(router_id=1, record_date=day,
                                        bytes_in=gw, bytes_out=0))
        if acc:
            session.add(DeviceTrafficRollup(device_id=device.id, record_date=day,
                                            bytes_in=acc, bytes_out=0))
            session.add(TrafficRollup(user_id=user.id, record_date=day,
                                      bytes_in=acc, bytes_out=0))
    # Volume the older queue-based accounting attributed before device counters.
    session.add(TrafficRollup(user_id=user.id, record_date=days[0],
                              bytes_in=2 * GB, bytes_out=0))
    await session.commit()

    data = await AnalyticsEngine.get_historical_traffic(
        session, start_date=days[0], end_date=days[-1], router_id=1
    )
    h = data.accounting_health

    # The two halves of the banner reconstruct the range total exactly.
    assert h.measured_accounted_bytes + h.pre_accounting_accounted_bytes == h.accounted_bytes
    # ...which is the number the user table sums to.
    assert sum(u.total_bytes for u in data.users) == h.accounted_bytes
    # The measured window is genuinely smaller than the range - that is the
    # difference the banner has to explain rather than hide.
    assert h.measured_accounted_bytes < h.accounted_bytes
    assert h.pre_accounting_accounted_bytes > 0


@pytest.mark.asyncio
async def test_the_routers_own_traffic_counts_as_attributed(session):
    """Volume in the input/output chains is measured and owned, so it closes a
    real part of the gap rather than sitting in it.

    Per-device rules match `forward` only, so DNS, NTP, updates and whatever the
    router's containers pull could never appear in the device sum. Before this
    was measured it looked identical to accounting having lost it.
    """
    from backend.app.db.models import RouterSelfTrafficRollup

    session.add(Router(id=1, name="Main", host="10.0.0.1", username="u", password="p"))
    user = User(name="Mark", speed_limit="unlimited")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    device = Device(user_id=user.id, router_id=1, mac_address="AA:BB:CC:00:11:22",
                    ip_address="192.168.88.50", is_active=True)
    session.add(device)
    session.add(AppSetting(key=STARTED_SETTING_KEY, value="2026-08-30"))
    await session.commit()
    await session.refresh(device)

    day = date(2026, 8, 31)
    session.add(RouterTrafficRollup(router_id=1, record_date=day, bytes_in=10 * GB, bytes_out=0))
    session.add(DeviceTrafficRollup(device_id=device.id, record_date=day, bytes_in=8 * GB, bytes_out=0))
    session.add(TrafficRollup(user_id=user.id, record_date=day, bytes_in=8 * GB, bytes_out=0))
    # The router pulled 1.5 GB for itself that day.
    session.add(RouterSelfTrafficRollup(router_id=1, record_date=day,
                                        bytes_in=1 * GB, bytes_out=GB // 2))
    await session.commit()

    data = await AnalyticsEngine.get_historical_traffic(
        session, start_date=day, end_date=day, router_id=1
    )

    assert data.router_self.total_bytes == GB + GB // 2
    assert data.router_self.pct_of_total == pytest.approx(15.0, abs=0.1)

    h = data.accounting_health
    # 8 GB of devices + 1.5 GB of router = 9.5 of 10 GB, not 8 of 10.
    assert h.accounted_bytes == 8 * GB + GB + GB // 2
    assert h.coverage_pct == pytest.approx(95.0, abs=0.1)
    assert h.status == "ok"


@pytest.mark.asyncio
async def test_router_traffic_lands_in_the_window_it_was_measured_in(session):
    """Self-traffic is split per day like everything else.

    Summing it only as a range total and subtracting would credit all of it to
    the pre-accounting window, breaking the reconciliation the banner relies on.
    """
    from backend.app.db.models import RouterSelfTrafficRollup

    session.add(Router(id=1, name="Main", host="10.0.0.1", username="u", password="p"))
    user = User(name="Mark", speed_limit="unlimited")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    device = Device(user_id=user.id, router_id=1, mac_address="AA:BB:CC:00:11:22",
                    ip_address="192.168.88.50", is_active=True)
    session.add(device)
    session.add(AppSetting(key=STARTED_SETTING_KEY, value="2026-08-30"))
    await session.commit()
    await session.refresh(device)

    early, late = date(2026, 8, 29), date(2026, 8, 31)
    for day, gw, dev, selfy in ((early, 20 * GB, 0, 0), (late, 10 * GB, 8 * GB, 1 * GB)):
        session.add(RouterTrafficRollup(router_id=1, record_date=day, bytes_in=gw, bytes_out=0))
        if dev:
            session.add(DeviceTrafficRollup(device_id=device.id, record_date=day, bytes_in=dev, bytes_out=0))
            session.add(TrafficRollup(user_id=user.id, record_date=day, bytes_in=dev, bytes_out=0))
        if selfy:
            session.add(RouterSelfTrafficRollup(router_id=1, record_date=day, bytes_in=selfy, bytes_out=0))
    await session.commit()

    h = (await AnalyticsEngine.get_historical_traffic(
        session, start_date=early, end_date=late, router_id=1
    )).accounting_health

    # The router's gigabyte was measured on the 31st, inside the window.
    assert h.measured_accounted_bytes == 9 * GB
    assert h.measured_bytes == 10 * GB
    assert h.coverage_pct == pytest.approx(90.0, abs=0.1)
    # And the halves still reconstruct the range total exactly.
    assert h.measured_accounted_bytes + h.pre_accounting_accounted_bytes == h.accounted_bytes
