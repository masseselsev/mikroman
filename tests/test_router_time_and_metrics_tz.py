"""Per-router UTC offset, and the Router Health charts reading in router time.

The metric samples are stored naive-UTC. The header clock shows router-local
time (read live from the router), so the graphs have to agree - they were
plotting raw UTC, ~5 h behind on a UTC+5 box. And the offset is now stored per
router, so switching to a router in another zone does not skew its dates until
its next telemetry tick.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import AppSetting, Base, SystemMetric
from backend.app.services.metrics_collector import metrics_collector
from backend.app.services.router_time import (
    get_router_offset,
    router_local_date,
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


class TestPerRouterOffset:
    @pytest.mark.asyncio
    async def test_offset_is_kept_per_router_with_a_shared_fallback(self, session):
        await store_router_offset(session, 300, router_id=1)   # UTC+5
        await store_router_offset(session, 180, router_id=2)   # UTC+3

        assert await get_router_offset(session, router_id=1) == 300
        assert await get_router_offset(session, router_id=2) == 180
        # A caller with no router id gets the last-written value (mirrored).
        assert await get_router_offset(session) == 180

    @pytest.mark.asyncio
    async def test_unknown_router_falls_back_to_the_shared_value(self, session):
        await store_router_offset(session, 300, router_id=1)
        # Router 9 has never reported its clock yet.
        assert await get_router_offset(session, router_id=9) == 300

    @pytest.mark.asyncio
    async def test_router_local_date_uses_the_router_s_own_offset(self, session):
        await store_router_offset(session, 300, router_id=1)
        await store_router_offset(session, -600, router_id=2)  # UTC-10
        # 22:00 UTC -> next day on router 1 (+5), previous day on router 2 (-10)
        moment = datetime(2026, 9, 3, 22, 0)
        d1 = await router_local_date(session, moment, router_id=1)
        d2 = await router_local_date(session, moment, router_id=2)
        assert d1.isoformat() == "2026-09-04"
        assert d2.isoformat() == "2026-09-03"


class TestMetricChartsAreRouterLocal:
    @pytest.mark.asyncio
    async def test_system_history_timestamps_are_shifted_to_router_local(self, session):
        session.add(AppSetting(key="router_gmt_offset_minutes_1", value="300"))
        sample_utc = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)
        session.add(SystemMetric(
            router_id=1, timestamp=sample_utc,
            cpu_load=7.0, memory_used_bytes=1, memory_total_bytes=2, memory_usage_pct=50.0,
        ))
        await session.commit()

        resp = await metrics_collector.get_system_history(session, router_id=1, range_key="24h")

        assert resp.points, "the sample should be inside a 24h window"
        got = resp.points[-1].timestamp
        # +5h from the stored UTC sample, give or take the bucket's last-item pick.
        delta_min = (got - sample_utc).total_seconds() / 60
        assert 299 <= delta_min <= 301, f"expected ~+300 min, got {delta_min}"

    @pytest.mark.asyncio
    async def test_no_offset_stored_means_no_shift(self, session):
        sample_utc = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
        session.add(SystemMetric(
            router_id=1, timestamp=sample_utc,
            cpu_load=1.0, memory_used_bytes=1, memory_total_bytes=2, memory_usage_pct=10.0,
        ))
        await session.commit()

        resp = await metrics_collector.get_system_history(session, router_id=1, range_key="24h")
        assert abs((resp.points[-1].timestamp - sample_utc).total_seconds()) < 1
