from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import (
    Base,
    Device,
    DeviceTrafficRollup,
    TrafficRollup,
    User,
)
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services.analytics_engine import (
    get_billing_cycle_dates,
    resolve_date_range,
)


@pytest.fixture
async def api_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # also yield session_factory for direct db test seeding
        client.session_factory = session_factory
        yield client

    app.dependency_overrides.clear()
    await engine.dispose()


def test_billing_cycle_date_calculation():
    """Test ISP billing cycle calculation across month boundaries and leap years."""
    # Reference: 2026-08-29 with anchor 15 -> Current cycle is 2026-08-15 to 2026-09-14
    start_d, end_d = get_billing_cycle_dates(anchor_day=15, reference_date=date(2026, 8, 29), previous=False)
    assert start_d == date(2026, 8, 15)
    assert end_d == date(2026, 9, 14)

    # Reference: 2026-08-10 with anchor 15 -> Current cycle started last month: 2026-07-15 to 2026-08-14
    start_d2, end_d2 = get_billing_cycle_dates(anchor_day=15, reference_date=date(2026, 8, 10), previous=False)
    assert start_d2 == date(2026, 7, 15)
    assert end_d2 == date(2026, 8, 14)

    # Previous cycle from 2026-08-29 with anchor 15 -> 2026-07-15 to 2026-08-14
    prev_start, prev_end = get_billing_cycle_dates(anchor_day=15, reference_date=date(2026, 8, 29), previous=True)
    assert prev_start == date(2026, 7, 15)
    assert prev_end == date(2026, 8, 14)

    # New year boundary: 2026-01-05 with anchor 20 -> 2025-12-20 to 2026-01-19
    ny_start, ny_end = get_billing_cycle_dates(anchor_day=20, reference_date=date(2026, 1, 5), previous=False)
    assert ny_start == date(2025, 12, 20)
    assert ny_end == date(2026, 1, 19)


def test_resolve_date_range_presets():
    """Test date range resolution for presets."""
    today = date.today()

    s, e, lbl = resolve_date_range("today", anchor_day=1)
    assert s == today and e == today and lbl == "today"

    s, e, lbl = resolve_date_range("yesterday", anchor_day=1)
    assert s == today - timedelta(days=1) and e == today - timedelta(days=1)

    s, e, lbl = resolve_date_range("7d", anchor_day=1)
    assert s == today - timedelta(days=6) and e == today

    custom_s = date(2026, 5, 1)
    custom_e = date(2026, 5, 10)
    s, e, lbl = resolve_date_range("custom", start_date=custom_s, end_date=custom_e, anchor_day=1)
    assert s == custom_s and e == custom_e and lbl == "custom"


@pytest.mark.asyncio
async def test_analytics_api_endpoints(api_client: AsyncClient):
    """Test GET /api/v1/analytics/billing-cycle, POST billing-cycle, and GET /traffic."""
    # 1. Get default billing cycle (day 1)
    resp = await api_client.get("/api/v1/analytics/billing-cycle")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "anchor_day" in data["data"]

    # 2. Update billing cycle to day 15
    resp_post = await api_client.post("/api/v1/analytics/billing-cycle", json={"anchor_day": 15})
    assert resp_post.status_code == 200
    assert resp_post.json()["data"]["anchor_day"] == 15

    # 3. Create test user and device with rollups
    async with api_client.session_factory() as session:
        user = User(name="AnalyticsUser", speed_limit="10M/30M")
        session.add(user)
        await session.commit()
        await session.refresh(user)

        device = Device(
            user_id=user.id,
            mac_address="AA:BB:CC:11:22:33",
            ip_address="192.168.88.150",
            custom_name="WorkLaptop",
            speed_limit="5M/15M",
            is_active=True
        )
        session.add(device)
        await session.commit()
        await session.refresh(device)

        # Add rollups for today, keyed the way the engine keys them.
        from backend.app.services.router_time import router_local_date
        today = await router_local_date(session)
        dev_rollup = DeviceTrafficRollup(
            device_id=device.id,
            record_date=today,
            bytes_in=1000000,
            bytes_out=500000
        )
        user_rollup = TrafficRollup(
            user_id=user.id,
            record_date=today,
            bytes_in=1000000,
            bytes_out=500000
        )
        session.add_all([dev_rollup, user_rollup])
        await session.commit()

    # 4. Query traffic analytics
    analytics_resp = await api_client.get("/api/v1/analytics/traffic?preset=today")
    assert analytics_resp.status_code == 200
    analytics_data = analytics_resp.json()["data"]

    assert analytics_data["gateway"]["total_bytes"] >= 1500000
    assert len(analytics_data["users"]) >= 1
    assert any(u["user_name"] == "AnalyticsUser" for u in analytics_data["users"])
    assert len(analytics_data["devices"]) >= 1
    assert any(d["mac_address"] == "AA:BB:CC:11:22:33" for d in analytics_data["devices"])
    assert len(analytics_data["timeline"]) >= 1


@pytest.mark.asyncio
async def test_quota_status_carries_a_consistent_end_of_cycle_forecast(api_client: AsyncClient):
    """The strip's forecast: a conservative cycle-so-far projection plus an
    'at current pace' figure. Asserted by self-consistency so the test does not
    depend on the calendar date it runs on."""
    from backend.app.db.models import RouterTrafficRollup
    from backend.app.services.router_time import router_local_date

    await api_client.post("/api/v1/analytics/billing-cycle", json={"anchor_day": 1})

    LIMIT = 100 * 1024 ** 3
    async with api_client.session_factory() as session:
        today = await router_local_date(session)
        # A single heavy day inside the current cycle.
        session.add(RouterTrafficRollup(
            router_id=1, record_date=today,
            bytes_in=30 * 1024 ** 3, bytes_out=5 * 1024 ** 3,
        ))
        await session.commit()

    await api_client.post(
        "/api/v1/analytics/quota",
        json={"limit_bytes": LIMIT, "thresholds": [80, 100], "notify_telegram": False},
    )
    q = (await api_client.get("/api/v1/analytics/quota")).json()["data"]

    assert q["enabled"] is True
    assert q["cycle_days_total"] >= 28
    assert 1 <= q["cycle_days_elapsed"] <= q["cycle_days_total"]

    used, total, elapsed = q["used_bytes"], q["cycle_days_total"], q["cycle_days_elapsed"]
    # Headline projection is the cycle-so-far daily average held for the cycle.
    assert q["projected_bytes_linear"] == int(used / elapsed * total)
    assert q["projected_pct_linear"] == round(q["projected_bytes_linear"] / LIMIT * 100, 1)
    assert q["on_track"] is (q["projected_bytes_linear"] <= LIMIT)
    # "At current pace" is used-so-far plus the recent daily mean over the days
    # left, so it can never come out below what is already spent.
    assert q["projected_bytes_at_pace"] >= used
    assert q["pace_bytes_per_day"] >= 0


@pytest.mark.asyncio
async def test_device_limit_and_pause_endpoints(api_client: AsyncClient):
    """Test POST /api/v1/devices/{id}/limit and POST /api/v1/devices/{id}/pause."""
    async with api_client.session_factory() as session:
        user = User(name="DeviceCtrlUser", speed_limit="unlimited")
        session.add(user)
        await session.commit()
        await session.refresh(user)

        device = Device(
            user_id=user.id,
            mac_address="DD:EE:FF:44:55:66",
            ip_address="192.168.88.160",
            custom_name="KidTablet",
            is_active=True
        )
        session.add(device)
        await session.commit()
        await session.refresh(device)
        dev_id = device.id

    # 1. Set device speed limit
    lim_resp = await api_client.post(f"/api/v1/devices/{dev_id}/limit", json={"speed_limit": "2M/10M"})
    assert lim_resp.status_code == 200
    assert lim_resp.json()["data"] is True

    # 2. Pause device
    pause_resp = await api_client.post(f"/api/v1/devices/{dev_id}/pause", json={"is_paused": True})
    assert pause_resp.status_code == 200
    assert pause_resp.json()["data"] is True

    # 3. Resume device
    resume_resp = await api_client.post(f"/api/v1/devices/{dev_id}/pause", json={"is_paused": False})
    assert resume_resp.status_code == 200
    assert resume_resp.json()["data"] is True


@pytest.mark.asyncio
async def test_multi_day_timeline_aggregation(api_client: AsyncClient):
    """Verify that get_historical_traffic accurately aggregates daily points across router WAN, users, and devices without identical duplication."""
    async with api_client.session_factory() as session:
        from backend.app.db.models import RouterTrafficRollup
        from backend.app.services.analytics_engine import AnalyticsEngine

        today = date.today()
        yesterday = today - timedelta(days=1)

        # Seed Router WAN traffic for yesterday and today
        # Yesterday: 10 GB download, 1 GB upload
        session.add(RouterTrafficRollup(
            router_id=1,
            record_date=yesterday,
            bytes_in=10_000_000_000,
            bytes_out=1_000_000_000
        ))
        # Today: 2 GB download, 200 MB upload
        session.add(RouterTrafficRollup(
            router_id=1,
            record_date=today,
            bytes_in=2_000_000_000,
            bytes_out=200_000_000
        ))
        await session.commit()

        # Query 7d traffic
        data = await AnalyticsEngine.get_historical_traffic(
            session=session,
            start_date=yesterday,
            end_date=today,
            router_id=1,
            range_preset="custom",
            anchor_day=1
        )

        # Verify gateway totals
        assert data.gateway.total_bytes_in == 12_000_000_000
        assert data.gateway.total_bytes_out == 1_200_000_000

        # Verify timeline points for yesterday and today are distinct and accurate
        pts = {p.record_date: p for p in data.timeline}
        assert pts[yesterday].bytes_in == 10_000_000_000
        assert pts[yesterday].bytes_out == 1_000_000_000
        assert pts[today].bytes_in == 2_000_000_000
        assert pts[today].bytes_out == 200_000_000
        # Ensure yesterday and today are NOT identical
        assert pts[yesterday].total_bytes != pts[today].total_bytes


def test_delta_computation_logic():
    """Verify _compute_delta handles normal accumulation, counter wrapping, and reboots."""
    from backend.app.services.analytics_engine import AnalyticsEngine

    # Normal delta: previous 1000, current 1500 -> delta 500
    assert AnalyticsEngine._compute_delta(1500, 1000) == 500

    # No change: previous 1000, current 1000 -> delta 0
    assert AnalyticsEngine._compute_delta(1000, 1000) == 0

    # Reboot / Counter reset: previous 5000, current 200 -> delta 200
    assert AnalyticsEngine._compute_delta(200, 5000) == 200

    # None previous: baseline initialization -> delta 0
    assert AnalyticsEngine._compute_delta(5000, None) == 0

