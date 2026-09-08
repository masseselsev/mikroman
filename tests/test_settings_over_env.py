"""Tests for the settings-over-environment rule.

The app had two ways to say the same thing - a variable in the container
environment and a row in ``app_settings`` - and which one won was decided
per-consumer by accident. The temperature threshold was the clearest case: one
code path read the environment and another read the stored row, so the same
board could be "cool" on one screen and "too hot" on another.

The rule now, in words: the stored setting wins, the environment is the default
it falls back to, and a value that cannot be parsed is reported and ignored
rather than silently taken as zero.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.core import tunables
from backend.app.core.config import Settings
from backend.app.db.models import AppSetting, Base


async def _db(rows):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        for key, value in rows.items():
            session.add(AppSetting(key=key, value=value))
        await session.commit()
    return engine, factory


@pytest.mark.asyncio
async def test_a_stored_interval_beats_the_environment_default():
    engine, factory = await _db({"poll_interval_seconds": "30", "heavy_sync_interval_seconds": "300"})
    async with factory() as session:
        assert await tunables.poll_interval_seconds(session) == 30.0
        assert await tunables.heavy_sync_interval_seconds(session) == 300.0
    await engine.dispose()


@pytest.mark.asyncio
async def test_nothing_stored_leaves_the_environment_in_charge():
    """An install that pinned POLL_INTERVAL_SECONDS=15 must not be changed by this."""
    engine, factory = await _db({})
    async with factory() as session:
        value = await tunables.poll_interval_seconds(session)
    assert value == float(Settings().POLL_INTERVAL_SECONDS)
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_value_that_is_not_a_number_is_reported_and_ignored():
    """Silently reading "ten" as 0 would sample the router every zero seconds."""
    engine, factory = await _db({"poll_interval_seconds": "ten"})
    async with factory() as session:
        assert await tunables.poll_interval_seconds(session) == float(Settings().POLL_INTERVAL_SECONDS)
    await engine.dispose()


@pytest.mark.asyncio
async def test_an_interval_is_clamped_to_a_range_the_app_survives():
    """A stored 0 would make the worker spin; 99999 would mean no data for a day."""
    engine, factory = await _db({"poll_interval_seconds": "0"})
    async with factory() as session:
        assert await tunables.poll_interval_seconds(session) == 5.0
    await engine.dispose()

    engine, factory = await _db({"poll_interval_seconds": "99999"})
    async with factory() as session:
        assert await tunables.poll_interval_seconds(session) == 3600.0
    await engine.dispose()


@pytest.mark.asyncio
async def test_the_temperature_line_comes_from_one_place_now():
    """``temp_warning_threshold`` is the key the dialog writes; the env is the fallback."""
    engine, factory = await _db({"temp_warning_threshold": "65"})
    async with factory() as session:
        assert await tunables.alert_temp_threshold_celsius(session) == 65.0
    await engine.dispose()

    engine, factory = await _db({})
    async with factory() as session:
        assert await tunables.alert_temp_threshold_celsius(session) == float(
            Settings().ALERT_TEMP_THRESHOLD_CELSIUS
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_per_router_threshold_overrides_the_global_one():
    """Router 2 can run hotter than router 1 and the alert has to know which."""
    engine, factory = await _db({
        "temp_warning_threshold": "70",
        "temp_warning_threshold_2": "85",
    })
    async with factory() as session:
        assert await tunables.alert_temp_threshold_celsius(session, router_id=2) == 85.0
        # A router with no scoped row takes the global one.
        assert await tunables.alert_temp_threshold_celsius(session, router_id=7) == 70.0
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_switch_can_be_turned_off_from_settings():
    engine, factory = await _db({"alert_new_device_enabled": "false"})
    async with factory() as session:
        assert await tunables.alert_new_device_enabled(session) is False
    await engine.dispose()

    engine, factory = await _db({})
    async with factory() as session:
        assert await tunables.alert_new_device_enabled(session) is True
    await engine.dispose()


@pytest.mark.asyncio
async def test_the_bot_follows_the_dashboard_language():
    """No "bot language" control exists, so the UI language is the operator's answer.

    ``TELEGRAM_DEFAULT_LANG`` cannot be set in the RouterOS container at all -
    there is no .env and no exec - which left every alert in the compiled-in
    default no matter what had been chosen on screen.
    """
    engine, factory = await _db({"lang": "ru"})
    async with factory() as session:
        assert await tunables.telegram_language(session) == "ru"
    await engine.dispose()

    engine, factory = await _db({"lang": "ru", "telegram_lang": "en"})
    async with factory() as session:
        # An explicit bot language still wins when someone really does want the
        # two to differ.
        assert await tunables.telegram_language(session) == "en"
    await engine.dispose()

    engine, factory = await _db({"lang": "klingon"})
    async with factory() as session:
        assert await tunables.telegram_language(session) in ("en", "ru")
    await engine.dispose()


@pytest.mark.asyncio
async def test_an_unreadable_database_falls_back_instead_of_raising():
    """A tick must not be lost because a setting row could not be read."""
    class Session:
        async def get(self, *args, **kwargs):
            raise ConnectionError("database is locked")

    assert await tunables.poll_interval_seconds(Session()) == float(
        Settings().POLL_INTERVAL_SECONDS
    )
    assert await tunables.alert_cpu_threshold(Session()) == int(Settings().ALERT_CPU_THRESHOLD_PERCENT)
    assert await tunables.telegram_language(Session()) in ("en", "ru")
