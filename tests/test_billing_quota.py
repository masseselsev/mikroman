"""ISP quota for the billing cycle, with alerting at chosen thresholds.

A quota is only useful if it warns before it is reached, so several thresholds
can be armed at once (for example 50, 80 and 100 percent). Each threshold fires
once per billing cycle: re-alerting on every poll would be noise, and the cycle
reset must re-arm them so the next month warns again.
"""
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import AppSetting, Base
from backend.app.services.quota import (
    QuotaConfig,
    crossed_thresholds,
    get_quota_config,
    parse_thresholds,
    save_quota_config,
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
