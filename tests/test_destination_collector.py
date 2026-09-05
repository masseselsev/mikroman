"""The conntrack -> UserDestinationStat producer.

The table, the endpoint and the UI tab all existed before this collector did,
so the "Destinations & Domains" view was permanently empty. These tests pin the
two things that make the numbers mean anything: RouterOS reports cumulative
counters (so only the delta may be added), and it recycles connection ids (so a
counter that goes backwards is a new connection, not a negative delta).
"""
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base, Device, User, UserDestinationStat
from backend.app.services.destination_collector import DestinationCollector, _endpoint_ip


class FakeClient:
    """A router that reports whatever conntrack rows the test hands it."""

    def __init__(self, conns, dns=None):
        self.conns = conns
        self.dns = dns or {}

    async def get_active_connections(self):
        return self.conns

    async def get_dns_cache_entries(self):
        return self.dns


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        s.add(User(id=1, name="Kristina", router_id=1))
        s.add(Device(id=10, mac_address="AA:BB:CC:DD:EE:01", ip_address="192.168.88.50",
                     user_id=1, router_id=1))
        await s.commit()
        yield s
    await engine.dispose()


def conn(cid, src, dst, orig, repl):
    return {
        ".id": cid,
        "protocol": "tcp",
        "src-address": src,
        "dst-address": dst,
        "orig-bytes": str(orig),
        "repl-bytes": str(repl),
    }


@pytest.mark.asyncio
async def test_first_sample_records_the_destination_with_one_hit(session):
    collector = DestinationCollector()
    client = FakeClient(
        [conn("*1", "192.168.88.50:51000", "142.250.185.14:443", 1000, 9000)],
        dns={"142.250.185.14": "youtube.com"},
    )

    assert await collector.sample_router(session, 1, client) == 1

    row = (await session.execute(select(UserDestinationStat))).scalars().one()
    assert row.user_id == 1 and row.device_id == 10
    assert row.destination_ip == "142.250.185.14"
    assert row.domain == "youtube.com"
    assert row.country_code == "US"
    # orig = what the device sent (upload), repl = what came back (download)
    assert (row.bytes_out, row.bytes_in) == (1000, 9000)
    assert row.total_bytes == 10_000
    assert row.hit_count == 1


@pytest.mark.asyncio
async def test_second_sample_adds_only_the_delta_not_the_running_total(session):
    collector = DestinationCollector()
    await collector.sample_router(session, 1, FakeClient(
        [conn("*1", "192.168.88.50:51000", "142.250.185.14:443", 1000, 9000)]
    ))
    await collector.sample_router(session, 1, FakeClient(
        [conn("*1", "192.168.88.50:51000", "142.250.185.14:443", 1500, 30_000)]
    ))

    row = (await session.execute(select(UserDestinationStat))).scalars().one()
    # Counting cumulative values twice would give 2500 / 39000.
    assert (row.bytes_out, row.bytes_in) == (1500, 30_000)
    assert row.total_bytes == 31_500
    # Same connection throughout - still one hit.
    assert row.hit_count == 1


@pytest.mark.asyncio
async def test_a_recycled_connection_id_counts_as_a_new_connection(session):
    collector = DestinationCollector()
    await collector.sample_router(session, 1, FakeClient(
        [conn("*1", "192.168.88.50:51000", "142.250.185.14:443", 5000, 50_000)]
    ))
    # RouterOS handed *1 to a different socket: the counters restart low.
    await collector.sample_router(session, 1, FakeClient(
        [conn("*1", "192.168.88.50:51000", "142.250.185.14:443", 40, 200)]
    ))

    row = (await session.execute(select(UserDestinationStat))).scalars().one()
    assert (row.bytes_out, row.bytes_in) == (5040, 50_200)
    assert row.hit_count == 2


@pytest.mark.asyncio
async def test_lan_to_lan_and_unknown_sources_are_not_destinations(session):
    collector = DestinationCollector()
    client = FakeClient([
        # Both ends on the LAN - a file copy, not a destination.
        conn("*1", "192.168.88.50:51000", "192.168.88.9:445", 900, 900),
        # A source that belongs to no known device cannot be attributed.
        conn("*2", "192.168.88.240:33000", "8.8.8.8:53", 90, 120),
    ])

    assert await collector.sample_router(session, 1, client) == 0
    assert (await session.execute(select(UserDestinationStat))).scalars().all() == []


@pytest.mark.asyncio
async def test_a_domain_learned_later_is_filled_in(session):
    collector = DestinationCollector()
    await collector.sample_router(session, 1, FakeClient(
        [conn("*1", "192.168.88.50:51000", "142.250.185.14:443", 100, 100)]
    ))
    await collector.sample_router(session, 1, FakeClient(
        [conn("*1", "192.168.88.50:51000", "142.250.185.14:443", 200, 200)],
        dns={"142.250.185.14": "youtube.com"},
    ))

    row = (await session.execute(select(UserDestinationStat))).scalars().one()
    assert row.domain == "youtube.com"


@pytest.mark.asyncio
async def test_closed_connections_leave_the_in_memory_cache(session):
    collector = DestinationCollector()
    await collector.sample_router(session, 1, FakeClient(
        [conn("*1", "192.168.88.50:51000", "142.250.185.14:443", 100, 100)]
    ))
    assert (1, "*1") in collector._seen

    await collector.sample_router(session, 1, FakeClient([]))
    # An empty sample is ambiguous (offline vs idle), so it changes nothing...
    assert (1, "*1") in collector._seen

    await collector.sample_router(session, 1, FakeClient(
        [conn("*2", "192.168.88.50:51001", "8.8.8.8:53", 60, 90)]
    ))
    # ...but a sample that no longer lists *1 does.
    assert (1, "*1") not in collector._seen
    assert (1, "*2") in collector._seen


def test_endpoint_ip_survives_ipv6():
    assert _endpoint_ip("1.2.3.4:443") == "1.2.3.4"
    assert _endpoint_ip("[2001:db8::1]:443") == "2001:db8::1"
    assert _endpoint_ip("2001:db8::1") == "2001:db8::1"
    assert _endpoint_ip("") == ""
