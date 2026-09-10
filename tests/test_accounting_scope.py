"""Tests for Traffic Accounting Scope (wan_only vs all_routed) and WAN interface filtering."""
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.db.models import AppSetting, Base, Device, User
from backend.app.services.traffic_accounting import (
    LiveRateTracker,
    TrafficAccountingService,
    parse_acct_comment,
    parse_acct_comment_with_interface,
)


class FakeRouter:
    """Minimal in-memory stand-in for RouterOS mangle API."""

    def __init__(self, rules=None):
        self.rules = list(rules or [])
        self._next = 100
        self.created = []
        self.updated = []
        self.deleted = []

    async def get_mangle_rules(self, fields=None):
        return [dict(r) for r in self.rules]

    async def create_mangle_rule(self, payload):
        self._next += 1
        rule_id = f"*{self._next}"
        rule = {**payload, ".id": rule_id, "bytes": "0"}
        self.rules.append(rule)
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


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _seed(session, ip="192.0.2.50", router_id=1):
    session.add(AppSetting(key=f"monitored_interfaces_{router_id}", value='["ether1"]'))
    user = User(name="Alice", speed_limit="unlimited")
    session.add(user)
    await session.commit()
    await session.refresh(user)

    device = Device(
        user_id=user.id,
        router_id=router_id,
        mac_address="AA:BB:CC:DD:EE:01",
        ip_address=ip,
        custom_name="Laptop",
        is_active=True,
    )
    session.add(device)
    await session.commit()
    await session.refresh(device)
    return user, device


def test_parse_acct_comment_with_interface():
    # 4-part tag (legacy / single-WAN)
    assert parse_acct_comment_with_interface("mikroman:acct:dev_7:up") == (7, "up", None)
    assert parse_acct_comment_with_interface("mikroman:acct:dev_12:down") == (12, "down", None)

    # 5-part tag (interface-scoped)
    assert parse_acct_comment_with_interface("mikroman:acct:dev_7:up:ether1") == (7, "up", "ether1")
    assert parse_acct_comment_with_interface("mikroman:acct:dev_12:down:wg0") == (12, "down", "wg0")

    # parse_acct_comment maintains 2-tuple backwards compatibility
    assert parse_acct_comment("mikroman:acct:dev_7:up") == (7, "up")
    assert parse_acct_comment("mikroman:acct:dev_7:up:ether1") == (7, "up")

    # Non-device tags ignored
    assert parse_acct_comment_with_interface("mikroman:acct:self:down:ether1") is None
    assert parse_acct_comment_with_interface("unrelated rule") is None
    assert parse_acct_comment_with_interface(None) is None


@pytest.mark.asyncio
async def test_accounting_scope_default_and_overrides(session):
    router = FakeRouter()
    svc = TrafficAccountingService(router, router_id=1)

    # Default is wan_only
    assert await svc._accounting_scope(session) == "wan_only"

    # Router-specific override
    session.add(AppSetting(key="traffic_accounting_scope_1", value="all_routed"))
    await session.commit()
    assert await svc._accounting_scope(session) == "all_routed"

    # Global override when router-specific absent
    svc2 = TrafficAccountingService(router, router_id=2)
    session.add(AppSetting(key="traffic_accounting_scope", value="all_routed"))
    await session.commit()
    assert await svc2._accounting_scope(session) == "all_routed"


@pytest.mark.asyncio
async def test_wan_only_scope_binds_to_monitored_wan_interface(session):
    _, device = await _seed(session)
    router = FakeRouter()
    svc = TrafficAccountingService(router, router_id=1)

    summary = await svc.sync_counter_rules(session)
    assert summary["created"] == 4  # 2 for device + 2 for router self

    dev_up = next(r for r in router.rules if r.get("comment") == f"mikroman:acct:dev_{device.id}:up")
    dev_down = next(r for r in router.rules if r.get("comment") == f"mikroman:acct:dev_{device.id}:down")

    # Device upload exits via ether1
    assert dev_up["chain"] == "forward"
    assert dev_up["out-interface"] == "ether1"
    assert "in-interface" not in dev_up

    # Device download arrives via ether1
    assert dev_down["chain"] == "forward"
    assert dev_down["in-interface"] == "ether1"
    assert "out-interface" not in dev_down


@pytest.mark.asyncio
async def test_all_routed_scope_creates_open_forward_rules(session):
    _, device = await _seed(session)
    session.add(AppSetting(key="traffic_accounting_scope_1", value="all_routed"))
    await session.commit()

    router = FakeRouter()
    svc = TrafficAccountingService(router, router_id=1)
    await svc.sync_counter_rules(session)

    dev_up = next(r for r in router.rules if r.get("comment") == f"mikroman:acct:dev_{device.id}:up")
    dev_down = next(r for r in router.rules if r.get("comment") == f"mikroman:acct:dev_{device.id}:down")

    assert dev_up["chain"] == "forward"
    assert dev_up.get("out-interface") is None
    assert dev_down["chain"] == "forward"
    assert dev_down.get("in-interface") is None


@pytest.mark.asyncio
async def test_toggling_scope_updates_rules_in_place(session):
    _, device = await _seed(session)
    router = FakeRouter()
    svc = TrafficAccountingService(router, router_id=1)

    # 1. Start in wan_only: rules have ether1 interface constraints
    await svc.sync_counter_rules(session)
    dev_up = next(r for r in router.rules if r.get("comment") == f"mikroman:acct:dev_{device.id}:up")
    assert dev_up["out-interface"] == "ether1"

    # 2. Switch to all_routed
    setting = await session.get(AppSetting, "traffic_accounting_scope_1")
    if setting:
        setting.value = "all_routed"
    else:
        session.add(AppSetting(key="traffic_accounting_scope_1", value="all_routed"))
    await session.commit()

    res = await svc.sync_counter_rules(session)
    assert res["updated"] == 2  # up and down rules updated to remove interface constraint
    assert dev_up.get("out-interface") == ""

    # 3. Switch back to wan_only
    setting = await session.get(AppSetting, "traffic_accounting_scope_1")
    setting.value = "wan_only"
    await session.commit()

    res2 = await svc.sync_counter_rules(session)
    assert res2["updated"] == 2  # updated to add back ether1
    assert dev_up["out-interface"] == "ether1"


@pytest.mark.asyncio
async def test_multi_wan_generates_per_interface_rules(session):
    _, device = await _seed(session)
    # Configure two monitored interfaces
    setting = await session.get(AppSetting, "monitored_interfaces_1")
    setting.value = '["ether1", "lte1"]'
    await session.commit()

    router = FakeRouter()
    svc = TrafficAccountingService(router, router_id=1)
    await svc.sync_counter_rules(session)

    comments = {r.get("comment") for r in router.rules}
    assert f"mikroman:acct:dev_{device.id}:up:ether1" in comments
    assert f"mikroman:acct:dev_{device.id}:down:ether1" in comments
    assert f"mikroman:acct:dev_{device.id}:up:lte1" in comments
    assert f"mikroman:acct:dev_{device.id}:down:lte1" in comments


def test_live_rate_tracker_aggregates_multi_wan_rules():
    tracker = LiveRateTracker()
    now = 1000.0

    rules_t0 = [
        {"comment": "mikroman:acct:dev_7:up:ether1", "bytes": "1000"},
        {"comment": "mikroman:acct:dev_7:up:lte1", "bytes": "2000"},
        {"comment": "mikroman:acct:dev_7:down:ether1", "bytes": "5000"},
    ]
    # First sample establishes baseline
    assert tracker.sample(rules_t0, now=now) == {}

    # Second sample 2 seconds later with traffic
    now += 2.0
    rules_t1 = [
        {"comment": "mikroman:acct:dev_7:up:ether1", "bytes": "2000"},  # +1000 bytes
        {"comment": "mikroman:acct:dev_7:up:lte1", "bytes": "4000"},    # +2000 bytes
        {"comment": "mikroman:acct:dev_7:down:ether1", "bytes": "9000"},# +4000 bytes
    ]
    rates = tracker.sample(rules_t1, now=now)
    # Total up delta = 3000 bytes in 2s -> 1500 B/s -> 12000 bps
    # Total down delta = 4000 bytes in 2s -> 2000 B/s -> 16000 bps
    assert 7 in rates
    assert rates[7]["tx_bps"] == pytest.approx(12000.0)
    assert rates[7]["rx_bps"] == pytest.approx(16000.0)
