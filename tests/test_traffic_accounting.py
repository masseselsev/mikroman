"""Tests for mangle-counter based per-device traffic accounting.

Replaces Simple Queue byte counters, which were measured to stay frozen at zero
on RouterOS 7.25 while traffic flowed.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import (
    Base,
    Device,
    DeviceTrafficRollup,
    TrafficRollup,
    User,
)
from backend.app.services.traffic_accounting import (
    TrafficAccountingService,
    parse_acct_comment,
)


class FakeRouter:
    """Minimal in-memory stand-in for the RouterOS mangle API."""

    def __init__(self, rules=None):
        self.rules = list(rules or [])
        self._next = 100
        self.created = []
        self.updated = []
        self.deleted = []

    async def get_mangle_rules(self):
        return [dict(r) for r in self.rules]

    async def create_mangle_rule(self, payload):
        self._next += 1
        rule_id = f"*{self._next}"
        self.rules.append({**payload, ".id": rule_id, "bytes": "0"})
        self.created.append(payload)
        return rule_id

    async def update_mangle_rule(self, rule_id, payload):
        for r in self.rules:
            if r[".id"] == rule_id:
                r.update(payload)
        self.updated.append((rule_id, payload))
        return True

    async def delete_mangle_rule(self, rule_id):
        self.rules = [r for r in self.rules if r[".id"] != rule_id]
        self.deleted.append(rule_id)

    def set_bytes(self, device_id, direction, value):
        tag = f"mikroman:acct:dev_{device_id}:{direction}"
        for r in self.rules:
            if r.get("comment") == tag:
                r["bytes"] = str(value)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _seed(session, ip="192.168.88.50", router_id=1, user_name="Mark"):
    user = User(name=user_name, speed_limit="unlimited")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    device = Device(
        user_id=user.id,
        router_id=router_id,
        mac_address="AA:BB:CC:00:11:22",
        ip_address=ip,
        custom_name="iPhone",
        is_active=True,
    )
    session.add(device)
    await session.commit()
    await session.refresh(device)
    return user, device


def test_parse_acct_comment_ignores_foreign_rules():
    """User-defined mangle rules must never be treated as MikroMan's."""
    assert parse_acct_comment("mikroman:acct:dev_7:up") == (7, "up")
    assert parse_acct_comment("mikroman:acct:dev_12:down") == (12, "down")
    assert parse_acct_comment("special dummy rule to show fasttrack counters") is None
    assert parse_acct_comment("mikroman:managed:user_1") is None
    assert parse_acct_comment("mikroman:acct:dev_x:up") is None
    assert parse_acct_comment("mikroman:acct:dev_7:sideways") is None
    assert parse_acct_comment(None) is None


@pytest.mark.asyncio
async def test_sync_creates_one_rule_pair_per_device(session):
    _, device = await _seed(session)
    router = FakeRouter([
        # a pre-existing user rule that must survive untouched
        {".id": "*1", "chain": "prerouting", "action": "passthrough",
         "comment": "special dummy rule to show fasttrack counters", "bytes": "999"},
    ])
    svc = TrafficAccountingService(router, router_id=1)

    summary = await svc.sync_counter_rules(session)
    assert summary["created"] == 2

    tags = {r.get("comment") for r in router.rules}
    assert f"mikroman:acct:dev_{device.id}:up" in tags
    assert f"mikroman:acct:dev_{device.id}:down" in tags
    # foreign rule untouched
    assert "special dummy rule to show fasttrack counters" in tags
    assert router.deleted == []

    # Rules must count without altering traffic.
    for rule in router.rules:
        if (rule.get("comment") or "").startswith("mikroman:acct:"):
            assert rule["action"] == "passthrough"
            assert rule["chain"] == "forward"


@pytest.mark.asyncio
async def test_sync_is_idempotent(session):
    await _seed(session)
    router = FakeRouter()
    svc = TrafficAccountingService(router, router_id=1)

    await svc.sync_counter_rules(session)
    second = await svc.sync_counter_rules(session)

    assert second == {"created": 0, "updated": 0, "removed": 0}, (
        "a second sync rewrote rules - this is the write-storm pattern again"
    )


@pytest.mark.asyncio
async def test_sync_repoints_rule_when_device_ip_changes(session):
    _, device = await _seed(session, ip="192.168.88.50")
    router = FakeRouter()
    svc = TrafficAccountingService(router, router_id=1)
    await svc.sync_counter_rules(session)

    device.ip_address = "192.168.88.77"
    await session.commit()

    summary = await svc.sync_counter_rules(session)
    assert summary["updated"] == 2
    assert summary["created"] == 0
    for rule in router.rules:
        parsed = parse_acct_comment(rule.get("comment"))
        if parsed and parsed[1] == "up":
            assert rule["src-address"] == "192.168.88.77"
        elif parsed:
            assert rule["dst-address"] == "192.168.88.77"


@pytest.mark.asyncio
async def test_sync_prunes_rules_for_removed_devices(session):
    _, device = await _seed(session)
    router = FakeRouter()
    svc = TrafficAccountingService(router, router_id=1)
    await svc.sync_counter_rules(session)

    device.is_active = False
    await session.commit()

    summary = await svc.sync_counter_rules(session)
    assert summary["removed"] == 2
    assert not any((r.get("comment") or "").startswith("mikroman:acct:") for r in router.rules)


@pytest.mark.asyncio
async def test_devices_without_router_id_are_still_accounted(session):
    """A NULL router_id previously excluded real clients from all analytics."""
    await _seed(session, router_id=None, user_name="Kristina")
    router = FakeRouter()
    svc = TrafficAccountingService(router, router_id=1)

    summary = await svc.sync_counter_rules(session)
    assert summary["created"] == 2, "device with NULL router_id must still be accounted"


@pytest.mark.asyncio
async def test_collect_accumulates_deltas_not_absolute_counters(session):
    user, device = await _seed(session)
    router = FakeRouter()
    svc = TrafficAccountingService(router, router_id=1)
    await svc.sync_counter_rules(session)

    # First reading only establishes the baseline.
    router.set_bytes(device.id, "down", 1_000_000)
    router.set_bytes(device.id, "up", 200_000)
    first = await svc.collect(session)
    assert first["bytes_in"] == 0
    assert first["bytes_out"] == 0

    # Second reading credits only what accrued since.
    router.set_bytes(device.id, "down", 1_500_000)
    router.set_bytes(device.id, "up", 260_000)
    second = await svc.collect(session)
    assert second["bytes_in"] == 500_000
    assert second["bytes_out"] == 60_000

    dev_rollup = (await session.execute(
        DeviceTrafficRollup.__table__.select()
    )).fetchall()
    assert len(dev_rollup) == 1
    assert dev_rollup[0].bytes_in == 500_000
    assert dev_rollup[0].bytes_out == 60_000

    # User totals must equal the sum of their devices, never a separate source.
    user_rollup = (await session.execute(TrafficRollup.__table__.select())).fetchall()
    assert len(user_rollup) == 1
    assert user_rollup[0].user_id == user.id
    assert user_rollup[0].bytes_in == 500_000
    assert user_rollup[0].bytes_out == 60_000


@pytest.mark.asyncio
async def test_collect_handles_counter_reset_without_double_counting(session):
    _, device = await _seed(session)
    router = FakeRouter()
    svc = TrafficAccountingService(router, router_id=1)
    await svc.sync_counter_rules(session)

    router.set_bytes(device.id, "down", 5_000_000)
    await svc.collect(session)          # baseline
    router.set_bytes(device.id, "down", 9_000_000)
    await svc.collect(session)          # +4,000,000

    # Router reboot: counter restarts from a small value.
    router.set_bytes(device.id, "down", 120_000)
    result = await svc.collect(session)
    assert result["bytes_in"] == 120_000, "a reset must credit only post-reset bytes"

    rows = (await session.execute(DeviceTrafficRollup.__table__.select())).fetchall()
    assert rows[0].bytes_in == 4_000_000 + 120_000


@pytest.mark.asyncio
async def test_dead_device_accounting_is_reported_not_masked(session):
    """Regression: the exact failure observed in production.

    Gateway shows 42 GB from WAN interface counters while every per-device
    counter reads zero. The old implementation reported
    ``max(gateway, users, devices)``, so the dashboard looked plausible and the
    breakdown silently showed 0 B for everyone. It must now be flagged.
    """
    from datetime import date

    from backend.app.db.models import RouterTrafficRollup
    from backend.app.services.analytics_engine import AnalyticsEngine

    user, _ = await _seed(session)
    today = date.today()
    session.add(RouterTrafficRollup(
        router_id=1, record_date=today,
        bytes_in=38_500_000_000, bytes_out=3_800_000_000,
    ))
    await session.commit()

    data = await AnalyticsEngine.get_historical_traffic(
        session=session, start_date=today, end_date=today,
        router_id=1, range_preset="today", anchor_day=1,
    )

    # Gateway stays truthful - never inflated from, nor collapsed into, device sums.
    assert data.gateway.total_bytes == 42_300_000_000
    # ... but the broken per-device path is surfaced explicitly.
    assert data.accounting_health.status == "degraded"
    assert data.accounting_health.accounted_bytes == 0
    assert data.accounting_health.coverage_pct == 0.0
    assert "accounting" in (data.accounting_health.message or "").lower()


@pytest.mark.asyncio
async def test_healthy_accounting_reports_ok(session):
    """When device counters track the gateway, health must read 'ok'."""
    from datetime import date

    from backend.app.db.models import RouterTrafficRollup
    from backend.app.services.analytics_engine import AnalyticsEngine

    _, device = await _seed(session)
    today = date.today()
    session.add(RouterTrafficRollup(
        router_id=1, record_date=today, bytes_in=1_000_000, bytes_out=100_000,
    ))
    session.add(DeviceTrafficRollup(
        device_id=device.id, record_date=today, bytes_in=950_000, bytes_out=95_000,
    ))
    await session.commit()

    data = await AnalyticsEngine.get_historical_traffic(
        session=session, start_date=today, end_date=today,
        router_id=1, range_preset="today", anchor_day=1,
    )
    assert data.accounting_health.status == "ok"
    assert data.accounting_health.coverage_pct > 90
    # The device must appear in the breakdown with its real volume.
    assert len(data.devices) == 1
    assert data.devices[0].total_bytes == 1_045_000


@pytest.mark.asyncio
async def test_range_predating_accounting_is_partial_not_degraded(session):
    """A window that starts before accounting existed must not raise a false alarm.

    Gateway interface counters have been running since the router booted; device
    counters only since the accounting rules were installed. Comparing the two
    over that range is apples-to-oranges, not a fault.
    """
    from datetime import date, timedelta

    from backend.app.db.models import AppSetting, RouterTrafficRollup
    from backend.app.services.analytics_engine import AnalyticsEngine

    await _seed(session)
    today = date.today()
    session.add(AppSetting(key="accounting_started_at", value=today.isoformat()))
    session.add(RouterTrafficRollup(
        router_id=1, record_date=today - timedelta(days=3),
        bytes_in=30_000_000_000, bytes_out=2_000_000_000,
    ))
    await session.commit()

    data = await AnalyticsEngine.get_historical_traffic(
        session=session, start_date=today - timedelta(days=6), end_date=today,
        router_id=1, range_preset="7d", anchor_day=1,
    )
    assert data.accounting_health.status == "partial"
    assert "started on" in (data.accounting_health.message or "")


@pytest.mark.asyncio
async def test_sync_records_accounting_start_date(session):
    """The start marker is written once, on the first rule creation."""
    from datetime import date

    await _seed(session)
    router = FakeRouter()
    svc = TrafficAccountingService(router, router_id=1)

    assert await svc.get_accounting_started(session) is None
    await svc.sync_counter_rules(session)
    assert await svc.get_accounting_started(session) == date.today()


def test_compute_delta_edge_cases():
    d = TrafficAccountingService.compute_delta
    assert d(1500, 1000) == 500
    assert d(1000, 1000) == 0
    assert d(5000, None) == 0        # baseline reading contributes nothing
    assert d(200, 5000) == 200       # counter reset
