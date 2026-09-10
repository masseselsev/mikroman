"""The monitoring page's own hot path must not read the event log, nor the whole
mangle table.

Everything in this file guards the per-second telemetry frame served over
`/ws/telemetry`. That path was the last remaining reader that still paid for
`Device.history` through the relationship default, and the only one that repeats
a thousand times a day per open browser tab — which is why opening the
monitoring page visibly raised the *router's* CPU, not just the container's.

Two independent costs are pinned here, because they were found separately and
have different fixes: an ORM cascade (a load option) and an unqualified
`/ip/firewall/mangle` print (a `.proplist`).
"""
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base, Device, DeviceHistory, Router, User
from backend.app.services.traffic_controller import MANGLE_RATE_FIELDS, TrafficController

MAC_A = "AA:BB:CC:00:00:01"
MAC_B = "AA:BB:CC:00:00:02"


async def _db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class FakeRouterClient:
    """Stands in for the RouterOS client, recording how the mangle read was asked for."""

    def __init__(self):
        self.mangle_calls = []

    async def get_mangle_rules(self, fields=None):
        self.mangle_calls.append(fields)
        # One accounting rule per direction per device, as the reconciler writes
        # them: `comment` carries the identity, `bytes` the counter.
        return [
            {".id": "*1", "comment": "mikroman:dev:1:up", "bytes": "1000/0"},
            {".id": "*2", "comment": "mikroman:dev:1:down", "bytes": "2000/0"},
        ]


@pytest.mark.asyncio
async def test_a_telemetry_frame_never_reads_the_device_event_log():
    """`select(Device)` is enough to load history — the relationship is eager.

    Asserted on the SQL that runs, not on the objects: an eager relationship
    issues a query whether or not the caller touches the attribute, which is
    exactly how the analytics and discovery instances of this bug hid.
    """
    engine, factory = await _db()
    seen = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    async with factory() as session:
        router = Router(name="R", host="127.0.0.1", is_active=True, is_default=True)
        session.add(router)
        await session.flush()
        user = User(name="Owner", router_id=router.id)
        session.add(user)
        await session.flush()
        device = Device(mac_address=MAC_A, user_id=user.id, router_id=router.id,
                        hostname="Laptop", vendor="Apple", is_active=True)
        session.add(device)
        await session.flush()
        session.add_all([
            DeviceHistory(device_id=device.id, mac_address=MAC_A, event_type="ip_changed",
                          details=f"e{i}")
            for i in range(400)
        ])
        await session.commit()

        controller = TrafficController(FakeRouterClient(), router_id=router.id)
        # Only what the frame itself runs counts. The 400 history rows above are
        # written over this same connection, and their INSERTs would satisfy the
        # assertion for the wrong reason if the buffer were not reset here.
        seen.clear()
        stats = await controller.get_realtime_traffic_stats(session, router_id=router.id)

    sql = "\n".join(seen)
    assert "device_history" not in sql, "the per-second frame loaded the event log"
    # The guard must not be bought with a frame that quietly lost its content.
    assert stats, "a frame with users configured returned nothing"
    assert stats[0]["name"] == "Owner"
    assert stats[0]["device_count"] == 1, "the owner's device list vanished with the eager load"
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_telemetry_frame_asks_for_only_the_counters_it_differentiates():
    """The router renders every attribute of every rule on an unqualified print."""
    engine, factory = await _db()
    client = FakeRouterClient()
    async with factory() as session:
        router = Router(name="R", host="127.0.0.1", is_active=True, is_default=True)
        session.add(router)
        await session.commit()
        controller = TrafficController(client, router_id=router.id)
        await controller.get_realtime_traffic_stats(session, router_id=router.id)

    assert client.mangle_calls == [list(MANGLE_RATE_FIELDS) if isinstance(
        MANGLE_RATE_FIELDS, list) else MANGLE_RATE_FIELDS], \
        "the hot path requested the whole rule table"
    assert set(MANGLE_RATE_FIELDS) == {".id", "comment", "bytes"}
    await engine.dispose()


@pytest.mark.asyncio
async def test_the_mangle_read_sends_proplist_only_when_fields_are_named():
    """Reconciliation needs whole rules, so the default must stay unqualified."""
    from backend.app.services.routeros.firewall import FirewallMixin

    captured = []

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return [{".id": "*1"}]

    class _Http:
        async def get(self, path, params=None):
            captured.append((path, params))
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _Client(FirewallMixin):
        def _get_client(self):
            return _Http()

    client = _Client()
    await client.get_mangle_rules(fields=MANGLE_RATE_FIELDS)
    await client.get_mangle_rules()

    assert captured[0] == ("/ip/firewall/mangle", {".proplist": ",".join(MANGLE_RATE_FIELDS)})
    assert captured[1] == ("/ip/firewall/mangle", None), \
        "the reconciler must still receive complete rules"
