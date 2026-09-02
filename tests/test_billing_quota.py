"""ISP quota for the billing cycle, with alerting at chosen thresholds.

A quota is only useful if it warns before it is reached, so several thresholds
can be armed at once (for example 50, 80 and 100 percent). Each threshold fires
once per billing cycle: re-alerting on every poll would be noise, and the cycle
reset must re-arm them so the next month warns again.
"""
from datetime import date, datetime
from datetime import time as dtime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import AppSetting, Base, InterfaceMetric
from backend.app.services.analytics_engine import (
    AnalyticsEngine,
    get_billing_cycle_bounds,
)
from backend.app.services.quota import (
    QuotaConfig,
    clean_portal_url,
    crossed_thresholds,
    get_quota_config,
    parse_thresholds,
    save_quota_config,
)
from backend.app.services.rollups import resolve_monitored_interfaces, slice_of_day_bytes


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


def test_parse_thresholds_sorts_dedupes_and_bounds():
    assert parse_thresholds("80,50,100") == [50, 80, 100]
    assert parse_thresholds("50, 50 ,80") == [50, 80]
    assert parse_thresholds("") == []
    assert parse_thresholds(None) == []
    # Nonsense values are dropped rather than aborting the whole list.
    assert parse_thresholds("50,abc,90") == [50, 90]
    # Out-of-range values cannot fire meaningfully.
    assert parse_thresholds("0,50,150") == [50]


def test_no_thresholds_cross_below_the_lowest():
    fired = crossed_thresholds(used_bytes=10, limit_bytes=100, thresholds=[50, 80, 100], already_fired=[])
    assert fired == []


def test_every_threshold_passed_fires_at_once_after_a_gap():
    """A poll gap must not skip a threshold that was passed in between."""
    fired = crossed_thresholds(used_bytes=95, limit_bytes=100, thresholds=[50, 80, 100], already_fired=[])
    assert fired == [50, 80]


def test_a_threshold_fires_only_once_per_cycle():
    fired = crossed_thresholds(used_bytes=95, limit_bytes=100, thresholds=[50, 80, 100], already_fired=[50])
    assert fired == [80]
    assert crossed_thresholds(95, 100, [50, 80, 100], already_fired=[50, 80]) == []


def test_reaching_the_limit_fires_the_hundred_percent_threshold():
    assert 100 in crossed_thresholds(100, 100, [50, 100], already_fired=[50])
    assert 100 in crossed_thresholds(250, 100, [100], already_fired=[])


def test_an_unset_limit_never_alerts():
    assert crossed_thresholds(used_bytes=999, limit_bytes=0, thresholds=[50], already_fired=[]) == []
    assert crossed_thresholds(used_bytes=999, limit_bytes=None, thresholds=[50], already_fired=[]) == []


@pytest.mark.asyncio
async def test_quota_config_round_trips(session):
    await save_quota_config(session, QuotaConfig(
        limit_bytes=500 * 1024 ** 3,
        thresholds=[50, 80, 100],
        notify_telegram=True,
    ))
    loaded = await get_quota_config(session)
    assert loaded.limit_bytes == 500 * 1024 ** 3
    assert loaded.thresholds == [50, 80, 100]
    assert loaded.notify_telegram is True


@pytest.mark.asyncio
async def test_defaults_are_inert_when_nothing_is_configured(session):
    loaded = await get_quota_config(session)
    assert loaded.limit_bytes == 0
    assert loaded.thresholds == []
    assert loaded.portal_url is None
    assert loaded.portal_label is None


@pytest.mark.asyncio
async def test_portal_link_round_trips_and_clears(session):
    await save_quota_config(session, QuotaConfig(
        limit_bytes=100, thresholds=[80],
        portal_url="https://my.isp.example/usage", portal_label="ISP usage",
    ))
    loaded = await get_quota_config(session)
    assert loaded.portal_url == "https://my.isp.example/usage"
    assert loaded.portal_label == "ISP usage"

    # An empty URL removes the link rather than storing a blank href.
    await save_quota_config(session, QuotaConfig(limit_bytes=100, portal_url="  "))
    assert (await get_quota_config(session)).portal_url is None


def test_clean_portal_url_rejects_anything_that_is_not_plain_http():
    assert clean_portal_url(None) is None
    assert clean_portal_url("") is None
    assert clean_portal_url("https://modem.local/stats") == "https://modem.local/stats"
    with pytest.raises(ValueError):
        clean_portal_url("javascript:alert(1)")
    with pytest.raises(ValueError):
        clean_portal_url("ftp://host/x")
    with pytest.raises(ValueError):
        clean_portal_url("https://user:pass@host/x")  # embedded credentials


@pytest.mark.asyncio
async def test_fired_thresholds_reset_when_the_cycle_changes(session):
    """A new billing cycle must re-arm every threshold."""
    from backend.app.services.quota import mark_fired, unfired_for_cycle

    cycle_a = date(2026, 8, 28)
    await mark_fired(session, cycle_a, 50)
    assert await unfired_for_cycle(session, cycle_a) == [50]

    cycle_b = date(2026, 9, 28)
    assert await unfired_for_cycle(session, cycle_b) == []

    setting = await session.get(AppSetting, "quota_fired_thresholds")
    assert setting is not None


class TestBillingCycleBounds:
    def test_midnight_anchor_matches_the_old_inclusive_dates(self):
        # anchor 15 at 00:00, ref mid-cycle -> Aug 15 00:00 .. Sep 15 00:00
        start, end = get_billing_cycle_bounds(15, 0, 0, datetime(2026, 8, 29, 12, 0))
        assert start == datetime(2026, 8, 15, 0, 0)
        assert end == datetime(2026, 9, 15, 0, 0)

    def test_before_the_reset_time_on_the_anchor_day_is_still_the_old_cycle(self):
        # reset is day 5 at 14:30; it is 10:00 on the 5th -> cycle started Aug 5
        start, end = get_billing_cycle_bounds(5, 14, 30, datetime(2026, 9, 5, 10, 0))
        assert start == datetime(2026, 8, 5, 14, 30)
        assert end == datetime(2026, 9, 5, 14, 30)

    def test_after_the_reset_time_on_the_anchor_day_is_the_new_cycle(self):
        start, end = get_billing_cycle_bounds(5, 14, 30, datetime(2026, 9, 5, 16, 0))
        assert start == datetime(2026, 9, 5, 14, 30)
        assert end == datetime(2026, 10, 5, 14, 30)

    def test_exactly_at_the_reset_instant_counts_as_the_new_cycle(self):
        start, _ = get_billing_cycle_bounds(5, 14, 30, datetime(2026, 9, 5, 14, 30))
        assert start == datetime(2026, 9, 5, 14, 30)

    def test_day_31_anchor_clamps_to_the_last_day_of_a_short_month(self):
        start, end = get_billing_cycle_bounds(31, 9, 0, datetime(2026, 2, 15, 12, 0))
        assert start == datetime(2026, 1, 31, 9, 0)
        assert end == datetime(2026, 2, 28, 9, 0)  # 2026 is not a leap year

    def test_previous_cycle_is_the_one_before(self):
        start, end = get_billing_cycle_bounds(5, 14, 30, datetime(2026, 9, 20, 8, 0), previous=True)
        assert start == datetime(2026, 8, 5, 14, 30)
        assert end == datetime(2026, 9, 5, 14, 30)

    def test_year_boundary(self):
        start, end = get_billing_cycle_bounds(20, 6, 0, datetime(2026, 1, 5, 3, 0))
        assert start == datetime(2025, 12, 20, 6, 0)
        assert end == datetime(2026, 1, 20, 6, 0)

    def test_anchor_day_one_is_a_single_calendar_month(self):
        # The old get_billing_cycle_dates had a bug here (two-month window).
        start, end = get_billing_cycle_bounds(1, 0, 0, datetime(2026, 9, 15, 0, 0))
        assert start == datetime(2026, 9, 1, 0, 0)
        assert end == datetime(2026, 10, 1, 0, 0)


@pytest.mark.asyncio
async def test_billing_anchor_time_round_trips_with_clamping_and_defaults(session):
    # default before anything is stored
    assert await AnalyticsEngine.get_billing_anchor_time(session) == (0, 0)

    stored = await AnalyticsEngine.set_billing_anchor_time(session, 14, 30)
    assert stored == (14, 30)
    assert await AnalyticsEngine.get_billing_anchor_time(session) == (14, 30)

    # out-of-range values are clamped, not rejected
    assert await AnalyticsEngine.set_billing_anchor_time(session, 99, -5) == (23, 0)
    assert await AnalyticsEngine.get_billing_anchor_time(session) == (23, 0)


class TestSliceOfDayBytes:
    async def _seed(self, session, samples, interface="ether1", router_id=1):
        """samples: list of (datetime, rx_total, tx_total)."""
        for ts, rx, tx in samples:
            session.add(InterfaceMetric(
                router_id=router_id, interface_name=interface,
                rx_rate_bps=0.0, tx_rate_bps=0.0,
                rx_bytes_total=rx, tx_bytes_total=tx, timestamp=ts,
            ))
        await session.commit()

    @pytest.mark.asyncio
    async def test_clean_partial_day_is_a_forward_counter_delta(self, session):
        day = date(2026, 9, 5)
        await self._seed(session, [
            (datetime(2026, 9, 5, 0, 0), 1_000, 100),
            (datetime(2026, 9, 5, 12, 0), 5_000, 300),
            (datetime(2026, 9, 5, 14, 30), 9_000, 800),
            (datetime(2026, 9, 5, 23, 0), 20_000, 2_000),
        ])
        # 00:00 -> 14:30 : (9_000 - 1_000, 800 - 100)
        got = await slice_of_day_bytes(session, 1, day, None, dtime(14, 30), ["ether1"])
        assert got == (8_000, 700)

    @pytest.mark.asyncio
    async def test_a_reboot_mid_slice_drops_only_the_backwards_step(self, session):
        day = date(2026, 9, 5)
        await self._seed(session, [
            (datetime(2026, 9, 5, 0, 0), 10_000, 1_000),
            (datetime(2026, 9, 5, 4, 0), 30_000, 3_000),   # +20_000 / +2_000
            (datetime(2026, 9, 5, 5, 0), 200, 50),          # reboot: counter reset
            (datetime(2026, 9, 5, 10, 0), 7_000, 900),      # +6_800 / +850
        ])
        got = await slice_of_day_bytes(session, 1, day, None, None, ["ether1"])
        assert got == (20_000 + 6_800, 2_000 + 850)

    @pytest.mark.asyncio
    async def test_multiple_interfaces_are_summed(self, session):
        day = date(2026, 9, 5)
        await self._seed(session, [
            (datetime(2026, 9, 5, 0, 0), 0, 0),
            (datetime(2026, 9, 5, 6, 0), 1_000, 100),
        ], interface="ether1")
        await self._seed(session, [
            (datetime(2026, 9, 5, 0, 0), 0, 0),
            (datetime(2026, 9, 5, 6, 0), 4_000, 400),
        ], interface="pppoe-out1")
        got = await slice_of_day_bytes(session, 1, day, None, None, ["ether1", "pppoe-out1"])
        assert got == (5_000, 500)

    @pytest.mark.asyncio
    async def test_a_day_with_no_samples_returns_none(self, session):
        got = await slice_of_day_bytes(session, 1, date(2026, 1, 1), None, None, ["ether1"])
        assert got is None

    @pytest.mark.asyncio
    async def test_window_is_router_local_and_shifted_into_the_utc_sample_frame(self, session):
        """The window comes in router-local (from get_billing_cycle_bounds) but
        interface_metrics.timestamp is naive UTC. At offset +180 a "before 14:30
        local" window is UTC "before 11:30", so it must sum the h=0..11 rows,
        not the naive h=0..14 rows."""
        session.add(AppSetting(key="router_gmt_offset_minutes", value="180"))
        await session.commit()
        day = date(2026, 9, 5)
        # Hourly cumulative counter in the stored (UTC) frame: +1000 rx / +100 tx
        # on every whole hour.
        await self._seed(session, [
            (datetime(2026, 9, 5, h, 0), 1_000 * h, 100 * h) for h in range(24)
        ])
        # Router-local 00:00 -> 14:30 == UTC 21:00(prev day) -> 11:30, so the
        # matching rows are h=0..11: delta (1000*11, 100*11).
        got = await slice_of_day_bytes(session, 1, day, None, dtime(14, 30), ["ether1"])
        assert got == (11_000, 1_100)

    @pytest.mark.asyncio
    async def test_from_time_bound_excludes_earlier_samples(self, session):
        day = date(2026, 9, 5)
        await self._seed(session, [
            (datetime(2026, 9, 5, 8, 0), 1_000, 100),
            (datetime(2026, 9, 5, 14, 30), 9_000, 800),
            (datetime(2026, 9, 5, 20, 0), 12_000, 1_100),
        ])
        # 14:30 -> end : (12_000 - 9_000, 1_100 - 800)
        got = await slice_of_day_bytes(session, 1, day, dtime(14, 30), None, ["ether1"])
        assert got == (3_000, 300)


@pytest.mark.asyncio
async def test_resolve_monitored_interfaces_reads_the_setting_or_is_empty(session):
    # Nothing chosen yet -> empty. The WAN is never guessed; accounting for a
    # router starts only once its uplink is picked in the selector.
    assert await resolve_monitored_interfaces(session, 1) == []
    session.add(AppSetting(key="monitored_interfaces_1", value='["ether1", "pppoe-out1"]'))
    await session.commit()
    assert await resolve_monitored_interfaces(session, 1) == ["ether1", "pppoe-out1"]
    # router_id None uses the _default key
    session.add(AppSetting(key="monitored_interfaces_default", value='["wan"]'))
    await session.commit()
    assert await resolve_monitored_interfaces(session, None) == ["wan"]


def test_billing_cycle_config_accepts_a_time_and_rejects_out_of_range():
    from pydantic import ValidationError

    from backend.app.schemas.analytics import BillingCycleConfig

    # default is midnight
    assert BillingCycleConfig(anchor_day=5).anchor_hour == 0
    assert BillingCycleConfig(anchor_day=5).anchor_minute == 0

    cfg = BillingCycleConfig(anchor_day=5, anchor_hour=14, anchor_minute=30)
    assert (cfg.anchor_hour, cfg.anchor_minute) == (14, 30)

    with pytest.raises(ValidationError):
        BillingCycleConfig(anchor_day=5, anchor_hour=24)
    with pytest.raises(ValidationError):
        BillingCycleConfig(anchor_day=5, anchor_minute=60)


def test_quota_status_dto_has_a_precise_reset_instant_field():
    from backend.app.schemas.analytics import QuotaStatusDTO

    assert QuotaStatusDTO().cycle_end_at is None
