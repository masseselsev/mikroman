"""Tests for mangle-counter based per-device traffic accounting.

Replaces Simple Queue byte counters, which were measured to stay frozen at zero
on RouterOS 7.25 while traffic flowed.
"""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import (
    AppSetting,
    Base,
    Device,
    DeviceTrafficRollup,
    TrafficRollup,
    User,
    UserTrafficBucket,
)
from backend.app.services.traffic_accounting import (
    TrafficAccountingService,
    bucket_start_for,
    parse_acct_comment,
    parse_self_comment,
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
    # A WAN must be chosen for a router before any accounting runs for it.
    # Pick one here so the accounting paths under test have something to
    # measure; the "no WAN selected" case is covered separately.
    key = f"monitored_interfaces_{router_id}" if router_id else "monitored_interfaces_default"
    if await session.get(AppSetting, key) is None:
        session.add(AppSetting(key=key, value='["ether1"]'))
        await session.commit()

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
    # One pair for the device, plus one pair for the router's own input/output
    # traffic on the monitored WAN interface.
    assert summary["created"] == 4

    tags = {r.get("comment") for r in router.rules}
    assert f"mikroman:acct:dev_{device.id}:up" in tags
    assert f"mikroman:acct:dev_{device.id}:down" in tags
    assert "mikroman:acct:self:up:ether1" in tags
    assert "mikroman:acct:self:down:ether1" in tags
    # foreign rule untouched
    assert "special dummy rule to show fasttrack counters" in tags
    assert router.deleted == []

    # Rules must count without altering traffic.
    for rule in router.rules:
        comment = rule.get("comment") or ""
        if not comment.startswith("mikroman:acct:"):
            continue
        assert rule["action"] == "passthrough"
        # Device volume is forwarded traffic; the router's own is not, and
        # matching it in `forward` would measure nothing at all.
        expected = "forward" if parse_acct_comment(comment) else ("input", "output")
        assert rule["chain"] == expected or rule["chain"] in expected


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

    device.is_deleted = True
    await session.commit()

    summary = await svc.sync_counter_rules(session)
    assert summary["removed"] == 2
    # The device's rules are gone; the router's own self-traffic pair is not a
    # device rule and must survive a device disappearing.
    assert not any(parse_acct_comment(r.get("comment")) for r in router.rules)
    assert sum(1 for r in router.rules if parse_self_comment(r.get("comment"))) == 2


@pytest.mark.asyncio
async def test_an_idle_device_keeps_its_counter_rules(session):
    """`is_active` flaps with discovery and must not prune a counter.

    It used to. A machine that went quiet for one discovery pass had its
    passthrough rules deleted, so everything it moved before the next pass saw
    it again crossed the WAN matching no rule and was attributed to nobody. On
    a real router that was 6.6 GB of a 20.7 GB day, with a desktop live in the
    router's own ARP table and no counter on it at all.
    """
    _, device = await _seed(session)
    router = FakeRouter()
    svc = TrafficAccountingService(router, router_id=1)
    await svc.sync_counter_rules(session)

    device.is_active = False
    await session.commit()

    summary = await svc.sync_counter_rules(session)
    assert summary["removed"] == 0
    assert len([r for r in router.rules if parse_acct_comment(r.get("comment"))]) == 2

    # And it keeps counting: the bytes it moves while "inactive" are credited.
    router.set_bytes(device.id, "down", 4_000_000)
    await svc.collect(session)
    router.set_bytes(device.id, "down", 9_000_000)
    await svc.collect(session)

    roll = (await session.execute(
        select(DeviceTrafficRollup).where(DeviceTrafficRollup.device_id == device.id)
    )).scalar_one()
    assert roll.bytes_in == 5_000_000


@pytest.mark.asyncio
async def test_two_rows_sharing_an_ip_get_one_rule_between_them(session):
    """Otherwise both rules match the same packets and every byte counts twice.

    Two device rows on one address is not hypothetical - a rotated MAC recorded
    twice, or a host that re-DHCPed into an address another record still holds.
    """
    _, device = await _seed(session)
    twin = Device(
        mac_address="AA:BB:CC:DD:EE:99",
        hostname="same-ip-twin",
        ip_address=device.ip_address,
        router_id=1,
        is_active=False,
    )
    session.add(twin)
    await session.commit()

    router = FakeRouter()
    svc = TrafficAccountingService(router, router_id=1)
    await svc.sync_counter_rules(session)

    acct = [parse_acct_comment(r.get("comment")) for r in router.rules]
    acct = [a for a in acct if a]
    assert len(acct) == 2                      # one pair, not two
    assert {a[0] for a in acct} == {device.id}  # held by the active row


@pytest.mark.asyncio
async def test_no_self_traffic_rules_until_a_wan_is_chosen(session):
    """A router with no WAN picked in the selector accounts nothing of its own.

    The device rules are still created (they key off the device IP, not an
    interface), but the input/output passthrough pair is withheld until an
    uplink is chosen - the monitored set is never guessed.
    """
    user = User(name="Mark", speed_limit="unlimited")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    session.add(Device(
        user_id=user.id, router_id=1, mac_address="AA:BB:CC:00:11:22",
        ip_address="192.168.88.50", custom_name="iPhone", is_active=True,
    ))
    await session.commit()

    svc = TrafficAccountingService(FakeRouter(), router_id=1)
    summary = await svc.sync_counter_rules(session)

    assert summary["created"] == 2, "device pair only - no self-traffic pair"
    tags = {r.get("comment") for r in svc.router_client.rules}
    assert not any(t and t.startswith("mikroman:acct:self:") for t in tags)


@pytest.mark.asyncio
async def test_devices_without_router_id_are_still_accounted(session):
    """A NULL router_id previously excluded real clients from all analytics."""
    await _seed(session, router_id=None, user_name="Kristina")
    # The service under test is scoped to router 1; give router 1 a WAN so the
    # self-traffic rule pair is created alongside the device rules.
    session.add(AppSetting(key="monitored_interfaces_1", value='["ether1"]'))
    await session.commit()
    router = FakeRouter()
    svc = TrafficAccountingService(router, router_id=1)

    summary = await svc.sync_counter_rules(session)
    # 2 for the device + 2 for the router's own traffic.
    assert summary["created"] == 4, "device with NULL router_id must still be accounted"


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

    # The same delta is also credited to the user's 30-minute intraday bucket,
    # aligned to the half hour, so the 1D history view has a shape to draw.
    buckets = (await session.execute(select(UserTrafficBucket))).scalars().all()
    assert len(buckets) == 1
    assert buckets[0].user_id == user.id
    assert buckets[0].bytes_in == 500_000
    assert buckets[0].bytes_out == 60_000
    assert buckets[0].bucket_start == bucket_start_for(buckets[0].bucket_start)
    assert buckets[0].bucket_start.minute in (0, 30)


@pytest.mark.asyncio
async def test_intraday_buckets_accumulate_and_prune(session):
    from datetime import timedelta

    from backend.app.services import traffic_accounting as ta

    user, device = await _seed(session)
    router = FakeRouter()
    svc = TrafficAccountingService(router, router_id=1)
    await svc.sync_counter_rules(session)

    router.set_bytes(device.id, "down", 1_000_000)
    await svc.collect(session)                       # baseline
    router.set_bytes(device.id, "down", 1_400_000)
    await svc.collect(session)                       # +400_000 into the current bucket
    router.set_bytes(device.id, "down", 1_500_000)
    await svc.collect(session)                       # +100_000, same bucket

    buckets = (await session.execute(select(UserTrafficBucket))).scalars().all()
    assert len(buckets) == 1
    assert buckets[0].bytes_in == 500_000

    # A bucket older than the retention window is pruned on the next tick.
    stale = UserTrafficBucket(
        user_id=user.id,
        bucket_start=bucket_start_for(buckets[0].bucket_start)
        - timedelta(days=ta.BUCKET_RETENTION_DAYS + 1),
        bytes_in=123,
        bytes_out=0,
    )
    session.add(stale)
    await session.commit()

    router.set_bytes(device.id, "down", 1_600_000)
    await svc.collect(session)

    remaining = (await session.execute(select(UserTrafficBucket))).scalars().all()
    assert all(
        b.bucket_start > bucket_start_for(buckets[0].bucket_start) - timedelta(days=ta.BUCKET_RETENTION_DAYS)
        for b in remaining
    )
    assert 123 not in [b.bytes_in for b in remaining]


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

    from backend.app.db.models import RouterTrafficRollup
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
    async def test_restarting_the_app_process_loses_no_traffic(self, session):
        """The baseline lives in the database, not in the service object. A
        container restart (or the app being down for a while) while the router
        keeps running must credit the whole gap on the next collect, exactly
        like a network outage."""
        _, device = await _seed(session)
        router = FakeRouter()

        # First run of the app: establish the baseline, then it goes away.
        svc_before = TrafficAccountingService(router, router_id=1)
        await svc_before.sync_counter_rules(session)
        router.set_bytes(device.id, "down", 3_000_000)
        await svc_before.collect(session, router_uptime_seconds=5000)
        del svc_before

        # Router kept counting the whole time it was down; uptime advanced.
        router.set_bytes(device.id, "down", 44_000_000)

        # Fresh process: a brand-new service instance, same database.
        svc_after = TrafficAccountingService(router, router_id=1)
        await svc_after.collect(session, router_uptime_seconds=7000)

        roll = (await session.execute(
            select(DeviceTrafficRollup).where(DeviceTrafficRollup.device_id == device.id)
        )).scalar_one()
        assert roll.bytes_in == 41_000_000  # the entire gap, once

    @pytest.mark.asyncio
    async def test_a_device_pruned_while_going_inactive_flushes_its_last_bytes(self, session):
        """The prune-before-collect hole: sync_counter_rules deletes the rule of
        a device that is no longer accountable, so its final interval must be
        flushed first or it is lost - and across an outage that interval is not
        small. Going merely idle no longer prunes anything (see
        `test_an_idle_device_keeps_its_counter_rules`); being deleted does."""
        _, device = await _seed(session)
        router = FakeRouter()
        svc = TrafficAccountingService(router, router_id=1)
        await svc.sync_counter_rules(session)

        router.set_bytes(device.id, "down", 2_000_000)
        await svc.collect(session)  # baseline

        # 3 MB more, then the record is removed.
        router.set_bytes(device.id, "down", 5_000_000)
        device.is_deleted = True
        await session.commit()

        summary = await svc.sync_counter_rules(session)
        assert summary["removed"] == 2  # both directions gone

        roll = (await session.execute(
            select(DeviceTrafficRollup).where(DeviceTrafficRollup.device_id == device.id)
        )).scalar_one()
        assert roll.bytes_in == 3_000_000  # credited before the rule was deleted

        baselines = await TrafficAccountingService._load_baselines(session)
        assert f"{device.id}:down" not in baselines  # series closed cleanly


# --- the router's own traffic -------------------------------------------------

class TestRouterSelfTraffic:
    """Volume the router generates or receives on its own behalf.

    Per-device rules match the `forward` chain and so are structurally incapable
    of seeing it: DNS, NTP, package checks, DDNS, whatever the router's own
    containers pull, and MikroMan's REST polling all travel `input`/`output`.
    That volume used to appear only as part of the unexplained gap between the
    WAN interface total and the sum of the devices.
    """

    @pytest.mark.asyncio
    async def test_self_rules_sit_in_the_input_and_output_chains(self, session):
        await _seed(session)
        router = FakeRouter()
        svc = TrafficAccountingService(router, router_id=1)
        await svc.sync_counter_rules(session)

        by_tag = {r["comment"]: r for r in router.rules if parse_self_comment(r.get("comment"))}
        assert set(by_tag) == {"mikroman:acct:self:down:ether1", "mikroman:acct:self:up:ether1"}

        down = by_tag["mikroman:acct:self:down:ether1"]
        assert down["chain"] == "input"
        assert down["in-interface"] == "ether1"
        assert down["action"] == "passthrough"

        up = by_tag["mikroman:acct:self:up:ether1"]
        assert up["chain"] == "output"
        assert up["out-interface"] == "ether1"

    @pytest.mark.asyncio
    async def test_a_second_sync_does_not_duplicate_them(self, session):
        await _seed(session)
        router = FakeRouter()
        svc = TrafficAccountingService(router, router_id=1)
        await svc.sync_counter_rules(session)
        second = await svc.sync_counter_rules(session)
        assert second["created"] == 0
        assert sum(1 for r in router.rules if parse_self_comment(r.get("comment"))) == 2

    @pytest.mark.asyncio
    async def test_self_volume_accumulates_as_deltas_into_its_own_rollup(self, session):
        from backend.app.db.models import RouterSelfTrafficRollup

        await _seed(session)
        router = FakeRouter()
        svc = TrafficAccountingService(router, router_id=1)
        await svc.sync_counter_rules(session)

        def set_self(direction, value):
            tag = f"mikroman:acct:self:{direction}:ether1"
            for r in router.rules:
                if r.get("comment") == tag:
                    r["bytes"] = str(value)

        # First read establishes the baseline and credits nothing.
        set_self("down", 1_000)
        set_self("up", 400)
        await svc.collect(session)
        rows = (await session.execute(select(RouterSelfTrafficRollup))).scalars().all()
        assert rows == []

        set_self("down", 6_000)
        set_self("up", 900)
        await svc.collect(session)

        rows = (await session.execute(select(RouterSelfTrafficRollup))).scalars().all()
        assert len(rows) == 1
        assert (rows[0].bytes_in, rows[0].bytes_out) == (5_000, 500)
        assert rows[0].router_id == 1

        # A second tick adds onto the same day's row rather than making another.
        set_self("down", 6_500)
        set_self("up", 900)
        await svc.collect(session)
        rows = (await session.execute(select(RouterSelfTrafficRollup))).scalars().all()
        assert len(rows) == 1
        assert (rows[0].bytes_in, rows[0].bytes_out) == (5_500, 500)

    @pytest.mark.asyncio
    async def test_the_rollup_is_found_again_when_the_router_id_is_null(self, session):
        """`= NULL` matches nothing; without an IS NULL lookup this made a new
        row every tick, turning one day into thousands of rows."""
        from backend.app.db.models import RouterSelfTrafficRollup

        await _seed(session, router_id=None)
        router = FakeRouter()
        svc = TrafficAccountingService(router, router_id=None)
        await svc.sync_counter_rules(session)

        def set_self(direction, value):
            tag = f"mikroman:acct:self:{direction}:ether1"
            for r in router.rules:
                if r.get("comment") == tag:
                    r["bytes"] = str(value)

        set_self("down", 100)
        await svc.collect(session)
        for step in (200, 300, 400):
            set_self("down", step)
            await svc.collect(session)

        rows = (await session.execute(select(RouterSelfTrafficRollup))).scalars().all()
        assert len(rows) == 1, "one row per day, not one per collection tick"
        assert rows[0].bytes_in == 300
        assert rows[0].router_id is None


def test_parse_self_comment_ignores_everything_else():
    assert parse_self_comment("mikroman:acct:self:down:ether1") == ("down", "ether1")
    assert parse_self_comment("mikroman:acct:self:up:pppoe-out1") == ("up", "pppoe-out1")
    # A device tag has four segments, not five - the two parsers must not overlap.
    assert parse_self_comment("mikroman:acct:dev_7:up") is None
    assert parse_acct_comment("mikroman:acct:self:down:ether1") is None
    assert parse_self_comment("mikroman:acct:self:sideways:ether1") is None
    assert parse_self_comment("mikroman:acct:self:down:") is None
    assert parse_self_comment("some user rule") is None
    assert parse_self_comment(None) is None
