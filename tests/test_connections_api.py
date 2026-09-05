import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base, Device, User, UserDestinationStat
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services.guards import WriteGuardViolation
from backend.app.services.router_manager import router_manager


@pytest.fixture
async def api_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_db():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.session_factory = factory
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_user_destinations_endpoint_sorting(api_client):
    async with api_client.session_factory() as s:
        user = User(name="TestUser")
        s.add(user)
        await s.commit()
        uid = user.id

        s.add_all([
            UserDestinationStat(
                user_id=uid,
                destination_ip="1.1.1.1",
                domain="one.one",
                country_code="US",
                bytes_in=100,
                bytes_out=400,
                total_bytes=500,
                hit_count=10,
            ),
            UserDestinationStat(
                user_id=uid,
                destination_ip="8.8.8.8",
                domain="dns.google",
                country_code="US",
                bytes_in=1500,
                bytes_out=500,
                total_bytes=2000,
                hit_count=2,
            ),
        ])
        await s.commit()

    # Sort by total_bytes desc
    res = await api_client.get(f"/api/v1/analytics/users/{uid}/destinations?sort_by=total_bytes&order=desc")
    assert res.status_code == 200
    data = res.json()["data"]
    assert len(data) == 2
    assert data[0]["domain"] == "dns.google"
    assert data[0]["total_bytes"] == 2000
    assert data[0]["flag_emoji"] != ""

    # Sort by hit_count desc
    res_hits = await api_client.get(f"/api/v1/analytics/users/{uid}/destinations?sort_by=hit_count&order=desc")
    assert res_hits.status_code == 200
    data_hits = res_hits.json()["data"]
    assert data_hits[0]["domain"] == "one.one"
    assert data_hits[0]["hit_count"] == 10


@pytest.mark.asyncio
async def test_connections_api_endpoints_mocked(api_client, monkeypatch):
    class FakeConnectionRouter:
        def get_immune_ips(self):
            return {"192.168.88.1", "127.0.0.1"}

        async def get_active_connections(self, proplist=None):
            return [
                {
                    ".id": "*1",
                    "protocol": "tcp",
                    "src-address": "192.168.88.50:49152",
                    "dst-address": "8.8.8.8:443",
                    "tcp-state": "established",
                    "orig-rate": "1000",
                    "repl-rate": "2000",
                    "orig-bytes": "10000",
                    "repl-bytes": "20000",
                    "timeout": "1h",
                },
                {
                    ".id": "*2",
                    "protocol": "udp",
                    "src-address": "192.168.88.50:5353",
                    "dst-address": "1.1.1.1:53",
                    "orig-rate": "0",
                    "repl-rate": "0",
                    "orig-bytes": "500",
                    "repl-bytes": "600",
                    "timeout": "10s",
                },
            ]

        async def get_dns_cache_entries(self):
            return {"8.8.8.8": "dns.google"}

        async def remove_firewall_connection(self, connection_id, src_ip=None, dst_ip=None):
            immune = self.get_immune_ips()
            if src_ip in immune or dst_ip in immune:
                raise WriteGuardViolation("ImmuneTargetGuard", "Refused write", dst_ip or src_ip)
            return True

    fake_client = FakeConnectionRouter()

    async def fake_require_client(session=None, router_id=None):
        return fake_client

    monkeypatch.setattr(router_manager, "require_client", fake_require_client)

    # Seed device
    async with api_client.session_factory() as s:
        user = User(name="Alice")
        s.add(user)
        await s.commit()
        dev = Device(user_id=user.id, mac_address="AA:BB:CC:00:11:22", ip_address="192.168.88.50", custom_name="Alice Phone", is_active=True)
        s.add(dev)
        await s.commit()
        dev_id = dev.id

    # 1. GET /api/v1/connections
    res = await api_client.get("/api/v1/connections")
    assert res.status_code == 200
    page = res.json()["data"]
    # `total` is the honest count of matching connections, separate from
    # `items` (which is additionally capped by `limit`) - see the truncation
    # test below for the case where the two diverge.
    assert page["total"] == 2
    items = page["items"]
    assert len(items) == 2
    assert items[0]["device_name"] == "Alice Phone"
    assert items[0]["user_name"] == "Alice"
    assert items[0]["domain"] == "dns.google"
    assert items[0]["flag_emoji"] != ""

    # 2. Filter by protocol
    res_proto = await api_client.get("/api/v1/connections?protocol=udp")
    assert res_proto.status_code == 200
    page_udp = res_proto.json()["data"]
    assert page_udp["total"] == 1
    items_udp = page_udp["items"]
    assert len(items_udp) == 1
    assert items_udp[0]["protocol"] == "udp"

    # 3. Filter by device_id
    res_dev = await api_client.get(f"/api/v1/connections?device_id={dev_id}")
    assert res_dev.status_code == 200
    assert len(res_dev.json()["data"]["items"]) == 2

    # 6. `limit` truncates `items` but `total` still counts everything that matched.
    res_limited = await api_client.get("/api/v1/connections?limit=1")
    assert res_limited.status_code == 200
    page_limited = res_limited.json()["data"]
    assert page_limited["total"] == 2
    assert len(page_limited["items"]) == 1

    # 4. POST /api/v1/connections/*1/kill (success)
    res_kill = await api_client.post("/api/v1/connections/*1/kill", json={"src_ip": "192.168.88.50", "dst_ip": "8.8.8.8"})
    assert res_kill.status_code == 200
    assert res_kill.json()["data"] is True

    # 5. POST /api/v1/connections/*1/kill (immune refusal)
    res_kill_immune = await api_client.post("/api/v1/connections/*1/kill", json={"src_ip": "192.168.88.50", "dst_ip": "192.168.88.1"})
    assert res_kill_immune.status_code == 400
    assert "WriteGuard" in res_kill_immune.json().get("detail", "")


@pytest.mark.asyncio
async def test_a_transport_failure_is_a_real_error_not_a_silent_empty_list(api_client, monkeypatch):
    """Regression: the endpoint used to catch every failure fetching live
    connections and answer `200 OK` with `data: []`. A polling UI cannot tell
    that apart from "genuinely zero connections right now", so it replaced its
    list wholesale on every poll - a single transient failure (a slow router,
    the client's own circuit breaker) wiped the on-screen table for a cycle
    with no visible error, before the next poll quietly repopulated it.

    `raise_app_exceptions=False` here (unlike the shared `api_client` fixture)
    makes this transport behave like the real ASGI server: an unhandled
    exception becomes a `500` response instead of propagating straight into
    the test, so this exercises exactly what a real client sees.
    """
    class FailingRouter:
        async def get_active_connections(self, proplist=None):
            raise ConnectionError("router unreachable")

    async def fake_require_client(session=None, router_id=None):
        return FailingRouter()

    monkeypatch.setattr(router_manager, "require_client", fake_require_client)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/connections")
    assert res.status_code == 500
