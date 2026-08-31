"""Tests for mangle-counter based per-device traffic accounting.

Replaces Simple Queue byte counters, which were measured to stay frozen at zero
on RouterOS 7.25 while traffic flowed.
"""
import pytest
from sqlalchemy import select
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
        # Flip on to simulate the router being unreachable: every counter read
        # raises, exactly as httpx does on a connect failure.
        self.fail_reads = False

    async def get_mangle_rules(self):
        if self.fail_reads:
            raise ConnectionError("router unreachable")
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
    from backend.app.services.router_time import router_local_date

    await _seed(session)
    router = FakeRouter()
    svc = TrafficAccountingService(router, router_id=1)

    assert await svc.get_accounting_started(session) is None
    await svc.sync_counter_rules(session)
    assert await svc.get_accounting_started(session) == await router_local_date(session)


def test_compute_delta_edge_cases():
    d = TrafficAccountingService.compute_delta
    assert d(1500, 1000) == 500
    assert d(1000, 1000) == 0
    assert d(5000, None) == 0        # baseline reading contributes nothing
    assert d(200, 5000) == 200       # counter went backwards -> reset

    # reset=True forces the post-reset path even when the counter reads higher
    # than its stale baseline: right after a reboot a fast counter can climb
    # past the pre-reboot value within one poll, and treating that as an
    # ordinary delta would lose every byte moved since the reboot.
    assert d(1500, 1000, reset=True) == 1500
    assert d(200, 5000, reset=True) == 200
    assert d(5000, None, reset=True) == 0   # still nothing to difference from


class TestRebootAwareCollection:
    """A router reboot resets every byte counter; the accounting must notice.

    A plain network outage is deliberately NOT a reboot - the router keeps
    counting the whole time, so ordinary differencing on reconnect already
    captures the gap. These tests pin the distinction.
    """

    @pytest.mark.asyncio
    async def test_a_normal_tick_differences_against_the_baseline(self, session):
        _, device = await _seed(session)
        router = FakeRouter()
        svc = TrafficAccountingService(router, router_id=1)
        await svc.sync_counter_rules(session)

        router.set_bytes(device.id, "down", 1_000_000)
        await svc.collect(session, router_uptime_seconds=3600)  # establishes baseline

        router.set_bytes(device.id, "down", 1_500_000)
        await svc.collect(session, router_uptime_seconds=3660)  # uptime advanced

        roll = (await session.execute(
            select(DeviceTrafficRollup).where(DeviceTrafficRollup.device_id == device.id)
        )).scalar_one()
        assert roll.bytes_in == 500_000

    @pytest.mark.asyncio
    async def test_an_outage_is_not_a_reboot_and_the_whole_gap_is_captured(self, session):
        _, device = await _seed(session)
        router = FakeRouter()
        svc = TrafficAccountingService(router, router_id=1)
        await svc.sync_counter_rules(session)

        router.set_bytes(device.id, "down", 1_000_000)
        await svc.collect(session, router_uptime_seconds=3600)

        # Five minutes of no polls (network down). The router kept counting;
        # uptime advanced by ~300s, so this is NOT a reboot.
        router.set_bytes(device.id, "down", 50_000_000)
        await svc.collect(session, router_uptime_seconds=3900)

        roll = (await session.execute(
            select(DeviceTrafficRollup).where(DeviceTrafficRollup.device_id == device.id)
        )).scalar_one()
        assert roll.bytes_in == 49_000_000  # the entire gap

    @pytest.mark.asyncio
    async def test_uptime_going_backwards_credits_bytes_since_the_reboot(self, session):
        _, device = await _seed(session)
        router = FakeRouter()
        svc = TrafficAccountingService(router, router_id=1)
        await svc.sync_counter_rules(session)

        router.set_bytes(device.id, "down", 8_000_000)
        await svc.collect(session, router_uptime_seconds=7200)

        # Router rebooted: uptime is now tiny, and the counter - which reset to
        # zero - has already climbed to 9_000_000, *past* the 8_000_000
        # baseline, within the first poll after boot.
        router.set_bytes(device.id, "down", 9_000_000)
        await svc.collect(session, router_uptime_seconds=45)

        roll = (await session.execute(
            select(DeviceTrafficRollup).where(DeviceTrafficRollup.device_id == device.id)
        )).scalar_one()
        # The first collect only set the baseline (contributes nothing); the
        # post-reboot collect credits 9_000_000 - all bytes since the reboot -
        # rather than 1_000_000 (9M - 8M) or a negative number.
        assert roll.bytes_in == 9_000_000

    @pytest.mark.asyncio
    async def test_missing_uptime_falls_back_to_the_counter_heuristic(self, session):
        # Older callers, or a failed /system/resource read, pass nothing.
        _, device = await _seed(session)
        router = FakeRouter()
        svc = TrafficAccountingService(router, router_id=1)
        await svc.sync_counter_rules(session)

        router.set_bytes(device.id, "down", 8_000_000)
        await svc.collect(session)  # baseline only

        router.set_bytes(device.id, "down", 2_000_000)  # obvious counter reset
        await svc.collect(session)

        roll = (await session.execute(
            select(DeviceTrafficRollup).where(DeviceTrafficRollup.device_id == device.id)
        )).scalar_one()
        # 2M < 8M is caught by the backwards-counter heuristic with no uptime.
        assert roll.bytes_in == 2_000_000


class TestOutageWithoutReboot:
    """The case flagged in review: the router is unreachable for a while but
    never restarts. No bytes may be lost and none double-counted."""

    @pytest.mark.asyncio
    async def test_a_failed_poll_is_a_no_op_and_the_gap_is_captured_on_return(self, session):
        _, device = await _seed(session)
        router = FakeRouter()
        svc = TrafficAccountingService(router, router_id=1)
        await svc.sync_counter_rules(session)

        router.set_bytes(device.id, "down", 1_000_000)
        await svc.collect(session, router_uptime_seconds=3600)  # baseline

        # Router unreachable: the poll raises. collect() must touch nothing.
        router.fail_reads = True
        out = await svc.collect(session, router_uptime_seconds=None)
        assert out == {"devices": 0, "bytes_in": 0, "bytes_out": 0}
        baselines = await TrafficAccountingService._load_baselines(session)
        assert baselines[f"{device.id}:down"] == 1_000_000, "baseline unchanged by a failed poll"

        # Twenty minutes later it is back; the counter ran the whole time.
        router.fail_reads = False
        router.set_bytes(device.id, "down", 80_000_000)
        await svc.collect(session, router_uptime_seconds=4800)

        roll = (await session.execute(
            select(DeviceTrafficRollup).where(DeviceTrafficRollup.device_id == device.id)
        )).scalar_one()
        assert roll.bytes_in == 79_000_000  # the entire gap, credited exactly once

    @pytest.mark.asyncio
    async def test_repeated_failures_then_recovery_lose_nothing(self, session):
        _, device = await _seed(session)
        router = FakeRouter()
        svc = TrafficAccountingService(router, router_id=1)
        await svc.sync_counter_rules(session)

        router.set_bytes(device.id, "up", 500_000)
        await svc.collect(session, router_uptime_seconds=1000)

        router.fail_reads = True
        for _ in range(5):
            await svc.collect(session)

        router.fail_reads = False
        router.set_bytes(device.id, "up", 4_500_000)
        await svc.collect(session, router_uptime_seconds=1600)

        roll = (await session.execute(
            select(DeviceTrafficRollup).where(DeviceTrafficRollup.device_id == device.id)
        )).scalar_one()
        assert roll.bytes_out == 4_000_000

    @pytest.mark.asyncio
    async def test_a_device_pruned_while_going_inactive_flushes_its_last_bytes(self, session):
        """The prune-before-collect hole: sync_counter_rules deletes the rule of
        a device that has gone inactive, so its final interval must be flushed
        first or it is lost - and across an outage that interval is not small."""
        _, device = await _seed(session)
        router = FakeRouter()
        svc = TrafficAccountingService(router, router_id=1)
        await svc.sync_counter_rules(session)

        router.set_bytes(device.id, "down", 2_000_000)
        await svc.collect(session)  # baseline

        # 3 MB more, then the device drops off and discovery marks it inactive.
        router.set_bytes(device.id, "down", 5_000_000)
        device.is_active = False
        await session.commit()

        summary = await svc.sync_counter_rules(session)
        assert summary["removed"] == 2  # both directions gone

        roll = (await session.execute(
            select(DeviceTrafficRollup).where(DeviceTrafficRollup.device_id == device.id)
        )).scalar_one()
        assert roll.bytes_in == 3_000_000  # credited before the rule was deleted

        baselines = await TrafficAccountingService._load_baselines(session)
        assert f"{device.id}:down" not in baselines  # series closed cleanly
