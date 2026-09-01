"""Per-interface / gateway rollups rebuilt from the sampled counters, and the
per-device midnight split that shares the same helper.

The bug these guard against: both accounting paths credited a whole counter
delta to the date of the poll that read it, so a poll resuming after an outage
that ran past local midnight filed a full evening of traffic under the wrong
day. On the developer's own install that put ~18 GB of one night onto the next
morning's date.
"""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import (
    AppSetting,
    Base,
    Device,
    DeviceTrafficRollup,
    InterfaceMetric,
    InterfaceTrafficRollup,
    Router,
    RouterTrafficRollup,
    TrafficRollup,
    User,
)
from backend.app.services.interface_rollups import (
    is_tunnel_interface,
    recompute_interface_rollups,
)
from backend.app.services.rollups import split_bytes_by_day
from backend.app.services.router_time import router_local_now
from backend.app.services.traffic_accounting import LAST_COLLECT_KEY, TrafficAccountingService

OFFSET_MIN = 300  # router at UTC+5


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _seed_router(session, offset=OFFSET_MIN, monitored='["ether1"]'):
    session.add(Router(id=1, name="Main", host="10.0.0.1", username="u", password="p"))
    session.add(AppSetting(key="router_gmt_offset_minutes", value=str(offset)))
    session.add(AppSetting(key="monitored_interfaces_1", value=monitored))
    await session.commit()


def _sample(name, utc_dt, rx, tx=0, router_id=1):
    return InterfaceMetric(
        router_id=router_id, interface_name=name,
        rx_bytes_total=rx, tx_bytes_total=tx, timestamp=utc_dt,
    )


# --- the pure day-split helper --------------------------------------------------

def test_split_same_day_is_a_single_untouched_entry():
    a = datetime(2026, 5, 10, 8, 0)
    b = datetime(2026, 5, 10, 20, 0)
    assert split_bytes_by_day(a, b, 1000, 40) == [(date(2026, 5, 10), 1000, 40)]


def test_split_across_midnight_is_proportional_and_conserves_the_total():
    # 22:00 -> 02:00 next day: 2h before midnight, 2h after -> an even split.
    a = datetime(2026, 5, 9, 22, 0)
    b = datetime(2026, 5, 10, 2, 0)
    parts = split_bytes_by_day(a, b, 1000, 100)
    assert [d for d, _, _ in parts] == [date(2026, 5, 9), date(2026, 5, 10)]
    assert parts[0][1] == 500 and parts[1][1] == 500
    assert sum(p[1] for p in parts) == 1000
    assert sum(p[2] for p in parts) == 100


def test_split_over_three_days_puts_the_rounding_remainder_on_the_last_day():
    a = datetime(2026, 5, 9, 12, 0)
    b = datetime(2026, 5, 11, 12, 0)  # exactly 48h: 12h + 24h + 12h
    parts = split_bytes_by_day(a, b, 1001, 7)
    assert [d for d, _, _ in parts] == [date(2026, 5, 9), date(2026, 5, 10), date(2026, 5, 11)]
    assert sum(p[1] for p in parts) == 1001
    assert sum(p[2] for p in parts) == 7


# --- interface classification -------------------------------------------------

@pytest.mark.parametrize("name", ["wg0", "wireguard1", "zt5u4c", "zerotier0", "gre-tun1", "l2tp-in1"])
def test_tunnel_interfaces_are_recognised(name):
    assert is_tunnel_interface(name) is True


@pytest.mark.parametrize("name", ["ether1", "bridge", "br.lan", "wifi2", "sfp-sfpplus1"])
def test_physical_and_bridge_interfaces_are_not_tunnels(name):
    assert is_tunnel_interface(name) is False


# --- recompute from samples -------------------------------------------------

async def _seed_two_day_samples(session):
    """ether1 crosses the 05-09 -> 05-10 router-local midnight; wg0 only moves
    on 05-10. Midnight 05-10 local == 2026-05-09 19:00 UTC (offset +5h)."""
    rows = [
        _sample("ether1", datetime(2026, 5, 9, 17, 0), 1000),   # local 22:00 05-09
        _sample("ether1", datetime(2026, 5, 9, 18, 0), 3000),   # local 23:00 05-09  -> +2000 on 05-09
        _sample("ether1", datetime(2026, 5, 9, 19, 30), 3000),  # local 00:30 05-10  -> +0
        _sample("ether1", datetime(2026, 5, 9, 21, 0), 9000),   # local 02:00 05-10  -> +6000 on 05-10
        _sample("ether1", datetime(2026, 5, 9, 23, 0), 9000),   # local 04:00 05-10  -> +0
        _sample("wg0", datetime(2026, 5, 9, 20, 0), 100),       # local 01:00 05-10
        _sample("wg0", datetime(2026, 5, 9, 22, 0), 600),       # local 03:00 05-10  -> +500 on 05-10
    ]
    for r in rows:
        session.add(r)
    await session.commit()


async def test_recompute_attributes_each_interface_to_the_day_the_bytes_moved(session):
    await _seed_router(session)
    await _seed_two_day_samples(session)

    n = await recompute_interface_rollups(session, 1, since_date=date(2026, 5, 9), until_date=date(2026, 5, 10))
    assert n == 2

    iface = {
        (r.interface_name, r.record_date): (r.bytes_in, r.bytes_out)
        for r in (await session.execute(select(InterfaceTrafficRollup))).scalars()
    }
    assert iface[("ether1", date(2026, 5, 9))] == (2000, 0)
    assert iface[("ether1", date(2026, 5, 10))] == (6000, 0)
    assert iface[("wg0", date(2026, 5, 10))] == (500, 0)
    # wg0 moved nothing on the 9th, so it gets no row for that day.
    assert ("wg0", date(2026, 5, 9)) not in iface


async def test_gateway_rollup_is_the_monitored_interfaces_only(session):
    await _seed_router(session)  # monitors ether1
    await _seed_two_day_samples(session)
    await recompute_interface_rollups(session, 1, since_date=date(2026, 5, 9), until_date=date(2026, 5, 10))

    gw = {r.record_date: (r.bytes_in, r.bytes_out)
          for r in (await session.execute(select(RouterTrafficRollup))).scalars()}
    assert gw[date(2026, 5, 9)] == (2000, 0)
    assert gw[date(2026, 5, 10)] == (6000, 0)  # wg0's 500 is NOT in the gateway total


async def test_a_counter_reset_shows_as_one_dropped_step_not_a_huge_delta(session):
    await _seed_router(session)
    await _seed_two_day_samples(session)
    # A restart: the counter falls back below its last value.
    session.add(_sample("ether1", datetime(2026, 5, 9, 23, 30), 200))  # local 04:30 05-10
    await session.commit()

    await recompute_interface_rollups(session, 1, since_date=date(2026, 5, 9), until_date=date(2026, 5, 10))
    gw = {r.record_date: r.bytes_in
          for r in (await session.execute(select(RouterTrafficRollup))).scalars()}
    assert gw[date(2026, 5, 10)] == 6000  # the 9000 -> 200 step contributed 0


async def test_recompute_is_idempotent(session):
    await _seed_router(session)
    await _seed_two_day_samples(session)
    await recompute_interface_rollups(session, 1, since_date=date(2026, 5, 9), until_date=date(2026, 5, 10))
    await recompute_interface_rollups(session, 1, since_date=date(2026, 5, 9), until_date=date(2026, 5, 10))

    rows = (await session.execute(select(InterfaceTrafficRollup))).scalars().all()
    # Exactly one row per (interface, day) - a second pass replaced, not added.
    keys = [(r.interface_name, r.record_date) for r in rows]
    assert len(keys) == len(set(keys))
    gw = {r.record_date: r.bytes_in
          for r in (await session.execute(select(RouterTrafficRollup))).scalars()}
    assert gw[date(2026, 5, 10)] == 6000


async def test_a_day_with_no_surviving_samples_is_left_untouched(session):
    await _seed_router(session)
    await _seed_two_day_samples(session)
    # A rollup from a day whose samples have already been pruned.
    session.add(InterfaceTrafficRollup(
        router_id=1, interface_name="ether1", record_date=date(2026, 1, 1),
        bytes_in=999, bytes_out=0,
    ))
    session.add(RouterTrafficRollup(router_id=1, record_date=date(2026, 1, 1), bytes_in=777, bytes_out=0))
    await session.commit()

    await recompute_interface_rollups(session, 1, since_date=date(2026, 1, 1), until_date=date(2026, 5, 10))

    old = (await session.execute(
        select(InterfaceTrafficRollup).where(InterfaceTrafficRollup.record_date == date(2026, 1, 1))
    )).scalar_one()
    assert old.bytes_in == 999
    old_gw = (await session.execute(
        select(RouterTrafficRollup).where(RouterTrafficRollup.record_date == date(2026, 1, 1))
    )).scalar_one()
    assert old_gw.bytes_in == 777


# --- the per-device midnight split via collect() -----------------------------

class _FakeRouter:
    def __init__(self, rules):
        self.rules = rules

    async def get_mangle_rules(self):
        return [dict(r) for r in self.rules]


async def test_collect_spreads_a_cross_midnight_delta_across_two_days(session):
    session.add(AppSetting(key="router_gmt_offset_minutes", value="0"))
    user = User(name="Mark", speed_limit="unlimited")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    dev = Device(user_id=user.id, router_id=1, mac_address="AA:BB:CC:00:11:22",
                 ip_address="192.168.88.50", is_active=True)
    session.add(dev)
    await session.commit()
    await session.refresh(dev)

    now_local = await router_local_now(session)
    yesterday_evening = (now_local - timedelta(days=1)).replace(
        hour=22, minute=0, second=0, microsecond=0
    )

    rules = [
        {".id": "*1", "comment": f"mikroman:acct:dev_{dev.id}:down", "bytes": "0"},
        {".id": "*2", "comment": f"mikroman:acct:dev_{dev.id}:up", "bytes": "0"},
    ]
    svc = TrafficAccountingService(_FakeRouter(rules), router_id=1)

    # First collect establishes the baseline, then we backdate the stored
    # "last collect" marker to yesterday evening and present a fresh delta.
    await svc.collect(session)
    baselines = await svc._load_baselines(session)
    baselines[LAST_COLLECT_KEY] = yesterday_evening.isoformat()
    await svc._save_baselines(session, baselines)
    await session.commit()

    rules[0]["bytes"] = "10000"
    rules[1]["bytes"] = "400"
    await svc.collect(session)

    dev_rows = (await session.execute(
        select(DeviceTrafficRollup).where(DeviceTrafficRollup.device_id == dev.id)
    )).scalars().all()
    assert len({r.record_date for r in dev_rows}) == 2, "delta should land on two dates"
    assert sum(r.bytes_in for r in dev_rows) == 10000
    assert sum(r.bytes_out for r in dev_rows) == 400

    # The user rollups mirror the device totals, per day.
    user_by_day = {
        r.record_date: (r.bytes_in, r.bytes_out)
        for r in (await session.execute(
            select(TrafficRollup).where(TrafficRollup.user_id == user.id)
        )).scalars()
    }
    for r in dev_rows:
        assert user_by_day[r.record_date] == (r.bytes_in, r.bytes_out)


# --- the analytics response surfaces the new columns -------------------------

async def test_historical_traffic_carries_interfaces_and_the_extra_columns(session):
    from backend.app.services.analytics_engine import AnalyticsEngine

    await _seed_router(session)
    user = User(name="Mark", speed_limit="unlimited")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    dev = Device(user_id=user.id, router_id=1, mac_address="AA:BB:CC:00:11:22",
                 ip_address="192.168.88.50", is_active=True,
                 last_seen=datetime(2026, 5, 10, 9, 0))
    session.add(dev)
    await session.commit()
    await session.refresh(dev)

    d = date(2026, 5, 10)
    session.add(DeviceTrafficRollup(device_id=dev.id, record_date=d, bytes_in=700, bytes_out=30))
    session.add(TrafficRollup(user_id=user.id, record_date=d, bytes_in=700, bytes_out=30))
    session.add(RouterTrafficRollup(router_id=1, record_date=d, bytes_in=900, bytes_out=50))
    session.add(InterfaceTrafficRollup(router_id=1, interface_name="ether1", record_date=d, bytes_in=900, bytes_out=50))
    session.add(InterfaceTrafficRollup(router_id=1, interface_name="wg0", record_date=d, bytes_in=120, bytes_out=8))
    await session.commit()
    # Force the engine's ``select(User)`` to reload so the selectin-loaded
    # ``devices`` collection is populated rather than served stale-empty from
    # the identity map.
    session.expire_all()

    resp = await AnalyticsEngine.get_historical_traffic(
        session=session, start_date=d, end_date=d, router_id=1,
    )

    names = {i.interface_name: i for i in resp.interfaces}
    assert set(names) == {"ether1", "wg0"}
    assert names["wg0"].is_tunnel is True and names["wg0"].is_monitored is False
    assert names["ether1"].is_monitored is True
    # Tunnel sorts ahead of the heavier WAN interface.
    assert resp.interfaces[0].interface_name == "wg0"
    assert names["wg0"].all_time_bytes == 128

    u = resp.users[0]
    assert u.last_seen == datetime(2026, 5, 10, 9, 0)
    assert u.all_time_bytes == 730
    dv = resp.devices[0]
    assert dv.last_seen == datetime(2026, 5, 10, 9, 0)
    assert dv.all_time_bytes == 730


async def test_quota_light_path_skips_the_extras(session):
    from backend.app.services.analytics_engine import AnalyticsEngine

    await _seed_router(session)
    session.add(InterfaceTrafficRollup(
        router_id=1, interface_name="wg0", record_date=date(2026, 5, 10), bytes_in=5, bytes_out=5
    ))
    await session.commit()

    resp = await AnalyticsEngine.get_historical_traffic(
        session=session, start_date=date(2026, 5, 10), end_date=date(2026, 5, 10),
        router_id=1, include_breakdown_extras=False,
    )
    assert resp.interfaces == []
