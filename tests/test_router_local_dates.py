"""Daily boundaries must follow the router's clock, not the container's.

The container almost always runs UTC while the router sits in a local zone. On a
UTC+5 router, everything between 19:00 and 24:00 local time falls on the
*previous* UTC date, so traffic recorded during those five hours was filed under
yesterday and "today" on the dashboard silently meant a different day than the
router's own clock showed.
"""
from datetime import date, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import AppSetting, Base
from backend.app.services.router_time import (
    ROUTER_OFFSET_SETTING_KEY,
    router_local_date,
    shift_to_router_local,
    store_router_offset,
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


def test_evening_on_a_positive_offset_router_is_already_the_next_utc_day():
    """23:13 UTC on a UTC+5 router is 04:13 the following day."""
    utc_now = datetime(2026, 8, 30, 23, 13, 0)
    assert shift_to_router_local(utc_now, 300).date() == date(2026, 8, 31)


def test_early_morning_on_a_negative_offset_router_is_still_the_previous_day():
    utc_now = datetime(2026, 8, 31, 2, 0, 0)
    assert shift_to_router_local(utc_now, -300).date() == date(2026, 8, 30)


def test_zero_and_missing_offsets_fall_back_to_utc():
    utc_now = datetime(2026, 8, 30, 23, 13, 0)
    assert shift_to_router_local(utc_now, 0).date() == date(2026, 8, 30)
    assert shift_to_router_local(utc_now, None).date() == date(2026, 8, 30)


@pytest.mark.asyncio
async def test_offset_round_trips_through_settings(session):
    await store_router_offset(session, 300)
    setting = await session.get(AppSetting, ROUTER_OFFSET_SETTING_KEY)
    assert setting.value == "300"

    # The stored offset is what later date lookups use.
    assert await router_local_date(session, now_utc=datetime(2026, 8, 30, 23, 13)) == date(2026, 8, 31)


@pytest.mark.asyncio
async def test_without_a_stored_offset_dates_stay_on_utc(session):
    """No offset known yet must not guess - it falls back to UTC."""
    assert await router_local_date(session, now_utc=datetime(2026, 8, 30, 23, 13)) == date(2026, 8, 30)


@pytest.mark.asyncio
async def test_a_corrupt_stored_offset_is_ignored(session):
    session.add(AppSetting(key=ROUTER_OFFSET_SETTING_KEY, value="not-a-number"))
    await session.commit()
    assert await router_local_date(session, now_utc=datetime(2026, 8, 30, 23, 13)) == date(2026, 8, 30)
