"""Tests for what the app costs: log volume, database lock time, request count.

The numbers in these assertions come from the live router-hosted container, not
from imagination: 995 of the 1000 lines in the router's log ring were MikroMan's
own echo, ``DELETE FROM system_metrics WHERE timestamp < ?`` was failing with
"database is locked", and 741 RouterOS REST calls went out in a measured
320-second window.
"""
import logging
import logging.config
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.app.core import diagnostics
from backend.app.core import logging_config as lc
from backend.app.core.config import Settings
from backend.app.db.models import Base, InterfaceMetric, Router, RouterLog, SystemMetric
from backend.app.db.prune import delete_older_than
from backend.app.db.session import get_db
from backend.app.main import _heavy_deadline, app
from backend.app.schemas.routeros import RouterSystemResource
from backend.app.services.log_collector import LogCollector
from backend.app.services.metrics_collector import MetricsCollector
from backend.app.services.routeros import RouterOSClient


@pytest.fixture(autouse=True)
def isolate_logging():
    """Restore the root logger and the module's file state around each test.

    ``configure_logging`` installs handlers on the root logger, which pytest owns
    for report capture; and it mutates module state pointing at a temporary
    directory. Without the restore, one test's handlers - and its cap on
    ``httpx`` - would apply to every test that ran afterwards.
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_levels = {name: logging.getLogger(name).level for name in lc._NOISY_LOGGERS}
    saved_filters = {name: list(logging.getLogger(name).filters) for name in lc._NOISY_LOGGERS}
    saved_state = dict(lc._state)
    yield
    for handler in list(root.handlers):
        if handler not in saved_handlers:
            handler.close()
            root.removeHandler(handler)
    root.handlers[:] = saved_handlers
    for name, level in saved_levels.items():
        logging.getLogger(name).setLevel(level)
        logging.getLogger(name).filters[:] = saved_filters[name]
    lc._state.clear()
    lc._state.update(saved_state)


@pytest.fixture
def point_logging_at(tmp_path, monkeypatch):
    """Reconfigure logging against a temporary data directory.

    Replaces the module's ``settings`` rather than editing the shared one: the
    database URL and log caps are read from that object by the rest of the app,
    and mutating the global would leak into every later test.
    """

    def _point(**overrides) -> Path:
        lc.configure_logging()  # install the console handler first, if absent
        monkeypatch.setattr(lc, "settings", Settings(
            DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path / 'app.db'}",
            LOG_TO_FILE=True,
            **overrides,
        ))
        path = lc.configure_logging()
        assert path is not None, f"file logging did not start: {lc.log_file_error()}"
        return path

    return _point


def _flush() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


async def _fresh_db():
    """An in-memory database with the whole schema, and its engine + factory."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def _sample(timestamp: datetime, **fields) -> SystemMetric:
    return SystemMetric(
        router_id=None, cpu_load=1.0, memory_used_bytes=1, memory_total_bytes=2,
        memory_usage_pct=50.0, timestamp=timestamp, **fields,
    )


def _log_row(router_id: int, timestamp: datetime, message: str) -> RouterLog:
    return RouterLog(
        router_id=router_id, external_id=f"*{router_id}-{message}", topics="info",
        message=message, severity="info", category="system", timestamp=timestamp,
    )


# --- the echo: MikroMan narrating itself into the router's log ring -----------


def test_per_request_logs_never_reach_the_persistent_file(point_logging_at):
    """httpx and uvicorn.access chatter must not be written down anywhere.

    Those two loggers produced 978 of the 995 echo lines measured on the device.
    The app's own WARNING is the thing that has to survive, so the cap has to be
    per-logger and not a global raise to WARNING.
    """
    path = point_logging_at()
    logging.getLogger("httpx").info("HTTP Request: GET https://router/rest/system/resource 200 OK")
    logging.getLogger("uvicorn.access").info('172.17.0.1:5321 - "GET /api/v1/users HTTP/1.1" 200')
    logging.getLogger("aiogram").info("Update id=123456 got handled")
    logging.getLogger("mikroman.main").warning("Sync tick failing for router hAP (1): OSError")
    _flush()

    text = path.read_text(encoding="utf-8")
    assert "HTTP Request" not in text, "per-request httpx lines are the noise that filled the ring"
    assert '"GET /api/v1/users' not in text, "uvicorn access lines are the other half"
    assert "Update id=" not in text, "aiogram logs every Telegram update at INFO"
    assert "Sync tick failing" in text


def test_the_silence_survives_somebody_else_configuring_logging(point_logging_at):
    """A later ``dictConfig`` must not re-open the flood.

    uvicorn applies its own configuration before importing the app, so setting
    the level is enough today. That is an ordering fact about one server, not a
    property worth relying on: this is the shape that would break the day the
    app is run under a different runner, and it is the filter - not the level -
    that holds.
    """
    path = point_logging_at()
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "loggers": {"uvicorn.access": {"level": "INFO"}},
    })
    assert logging.getLogger("uvicorn.access").level == logging.INFO
    logging.getLogger("uvicorn.access").info('172.17.0.1:9 - "GET /api/v1/users HTTP/1.1" 200')
    _flush()
    assert '"GET /api/v1/users' not in path.read_text(encoding="utf-8")


def test_the_log_file_follows_the_database(tmp_path, monkeypatch):
    """The log lives beside the database, so moving the data dir moves the log."""
    monkeypatch.setattr(lc, "settings", Settings(DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path / 'd' / 'app.db'}"))
    assert lc.resolve_log_dir() == tmp_path / "d"

    monkeypatch.setattr(lc, "settings", Settings(DATABASE_URL="sqlite+aiosqlite:////data/app.db"))
    assert lc.resolve_log_dir() == Path("/data")


def test_a_read_only_data_directory_degrades_instead_of_failing(tmp_path, monkeypatch):
    """A volume that cannot be written to must not stop the app starting."""
    readonly = tmp_path / "ro"
    readonly.mkdir()
    readonly.chmod(0o500)
    monkeypatch.setattr(lc, "settings", Settings(
        DATABASE_URL=f"sqlite+aiosqlite:///{readonly / 'app.db'}", LOG_TO_FILE=True
    ))
    try:
        path = lc.configure_logging()
    finally:
        readonly.chmod(0o700)
    assert path is None
    assert lc.log_file_error(), "the reason has to be reportable, not swallowed"


def test_the_tail_keeps_tracebacks_whole(tmp_path):
    """An unparsable continuation belongs to the entry before it.

    Dropping the body of a traceback to a line-format check would hide exactly
    the thing the persistent log exists to capture.
    """
    path = tmp_path / "mikroman.log"
    path.write_text(
        "2026-09-08 20:00:30,781 [WARNING] mikroman.metrics_collector: "
        "Metrics collection failing for router 2: OperationalError: database is locked\n"
        "Traceback (most recent call last):\n"
        '  File "app.py", line 3, in tick\n'
        "ValueError: boom\n"
        "2026-09-08 20:01:46,985 [INFO] mikroman.metrics_collector: recovered\n",
        encoding="utf-8",
    )
    lc._state["file_path"] = path
    entries = lc.read_recent_entries(lines=50)
    assert len(entries) == 2
    assert entries[0]["severity"] == "warning"
    assert "ValueError: boom" in entries[0]["message"]
    assert entries[1]["timestamp"] == datetime(2026, 9, 8, 20, 1, 46, 985000)
    assert [e["severity"] for e in lc.read_recent_entries(lines=50, severity="warning")] == ["warning"]
    assert lc.read_recent_entries(lines=50, contains="locked") == entries[:1]


# --- the database: no transaction may hold the write lock for long -----------


@pytest.mark.asyncio
async def test_retention_prune_is_batched_and_keeps_the_newest_rows():
    """1 200 expired rows leave in bounded transactions; the fresh row survives.

    The statement that failed on the device was this one, run while somebody
    else held the lock. ``LIMIT`` inside the batch is what bounds the write
    transaction - a bare range delete has no size at all.
    """
    engine, factory = await _fresh_db()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with factory() as session:
        for i in range(1200):
            session.add(_sample(now - timedelta(days=40, seconds=i)))
        session.add(_sample(now - timedelta(days=1)))
        await session.commit()

        assert await delete_older_than(session, SystemMetric.__table__, now - timedelta(days=30), batch=500) == 1200
        left = (await session.execute(select(SystemMetric))).scalars().all()
        assert len(left) == 1
        assert left[0].cpu_load == 1.0 and left[0].timestamp > now - timedelta(days=2)
    await engine.dispose()


@pytest.mark.asyncio
async def test_each_batch_is_its_own_statement():
    """1 300 rows at batch=400 is four deletes, not one 1 300-row transaction."""
    engine, factory = await _fresh_db()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    observed = []
    async with factory() as session:
        for i in range(1300):
            session.add(_sample(now - timedelta(days=40, seconds=i)))
        await session.commit()

        original = session.execute

        async def counting(stmt, *args, **kwargs):
            first_line = str(stmt).split("\n")[0]
            if first_line.startswith("DELETE"):
                observed.append(first_line)
            return await original(stmt, *args, **kwargs)

        session.execute = counting  # type: ignore[method-assign]
        await delete_older_than(session, SystemMetric.__table__, now - timedelta(days=30), batch=400)

    assert len(observed) == 4, "three full batches plus the remainder"
    assert all(" IN " in sql for sql in observed), "each delete names one batch's ids"
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_stopped_app_catches_up_without_rescanning_every_tick():
    """The hourly throttle: one pass, then nothing until the interval expires."""
    engine, factory = await _fresh_db()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    collector = MetricsCollector()
    async with factory() as session:
        session.add(InterfaceMetric(
            router_id=1, interface_name="ether1", rx_rate_bps=0, tx_rate_bps=0,
            rx_bytes_total=0, tx_bytes_total=0, timestamp=now - timedelta(days=45),
        ))
        await session.commit()

        assert await collector._prune_expired(session, now) == 1
        assert await collector._prune_expired(session, now) == 0
        assert await collector._prune_expired(session, now + timedelta(seconds=5)) == 0
        assert (await session.execute(select(InterfaceMetric))).scalars().all() == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_log_prune_by_record_cap_keeps_the_newest():
    """``max_records`` trims from the old end without materialising id lists."""
    engine, factory = await _fresh_db()
    now = datetime.now()
    collector = LogCollector()
    async with factory() as session:
        session.add(Router(name="Cap", host="192.168.88.1", is_active=True))
        for i in range(25):
            session.add(_log_row(1, now - timedelta(minutes=25 - i), f"line {i}"))
        await session.commit()

        deleted = await collector.prune_old_logs(
            session, router_id=1, retention_days=14, max_records=10
        )
        assert deleted == 15
        kept = (await session.execute(select(RouterLog).order_by(RouterLog.timestamp))).scalars().all()
        assert [row.message for row in kept] == [f"line {i}" for i in range(15, 25)]
    await engine.dispose()


@pytest.mark.asyncio
async def test_pruning_one_router_leaves_another_routers_history_alone():
    engine, factory = await _fresh_db()
    now = datetime.now()
    collector = LogCollector()
    async with factory() as session:
        for router_id in (1, 2):
            session.add(Router(id=router_id, name=f"R{router_id}", host=f"10.0.0.{router_id}", is_active=True))
        for router_id in (1, 2):
            for i in range(12):
                session.add(_log_row(router_id, now - timedelta(days=60, minutes=i), f"old {router_id}-{i}"))
        await session.commit()

        assert await collector.prune_old_logs(session, router_id=1, retention_days=14) == 12
        other = (await session.execute(select(RouterLog).where(RouterLog.router_id == 2))).scalars().all()
        assert len(other) == 12
    await engine.dispose()


# --- the REST load on the router --------------------------------------------


@pytest.fixture
def router_settings():
    return Settings(
        ROUTEROS_HOST="192.168.88.1", ROUTEROS_PORT=443, ROUTEROS_USE_SSL=True,
        ROUTEROS_SSL_VERIFY=False, ROUTEROS_USER="rest", ROUTEROS_PASSWORD="password",
    )


@pytest.mark.asyncio
async def test_a_resource_read_the_tick_already_made_is_not_asked_for_again(router_settings):
    """``collect_and_store`` accepts the caller's answer instead of re-reading it."""
    client = RouterOSClient(router_settings)
    collector = MetricsCollector()
    collector._next_prune_at = float("inf")  # keep retention out of the request count
    engine, factory = await _fresh_db()
    already_read = RouterSystemResource(
        cpu_load=9, free_memory=10, total_memory=20, uptime="3h"
    )

    with respx.mock(base_url="https://192.168.88.1:443/rest") as mock:
        resource = mock.get("/system/resource").respond(200, json=[
            {"cpu-load": "12", "free-memory": "1000", "total-memory": "2000", "uptime": "1d"}
        ])
        mock.get("/system/health").respond(200, json=[{"name": "cpu-temperature", "value": "44"}])
        mock.get("/interface").respond(200, json=[])
        async with factory() as session:
            await collector.collect_and_store(session, router_id=1, client=client, resource=already_read)
            assert resource.call_count == 0, "the tick read it once already; asking again doubles the load"

            await collector.collect_and_store(session, router_id=1, client=client)
            assert resource.call_count == 1, "with no handover it still reads it itself"
    await engine.dispose()


@pytest.mark.asyncio
async def test_the_public_address_is_read_once_per_window(router_settings):
    """/ip/cloud fed a 15-minute resolver cache but was re-read on every frame."""
    client = RouterOSClient(router_settings)
    with respx.mock(base_url="https://192.168.88.1:443/rest") as mock:
        route = mock.get("/ip/cloud").respond(200, json=[{"public-address": "203.0.113.7"}])
        for _ in range(10):
            assert await client.get_cloud_public_address() == "203.0.113.7"
        assert route.call_count == 1
        assert await client.get_cloud_public_address(refresh=True) == "203.0.113.7"
        assert route.call_count == 2, "refresh=True must still be able to force a read"
    await client.aclose()


@pytest.mark.asyncio
async def test_a_refused_ip_cloud_is_not_re_asked_every_frame(router_settings):
    """A router with DDNS off must not pay for the refusal every few seconds."""
    client = RouterOSClient(router_settings)
    with respx.mock(base_url="https://192.168.88.1:443/rest") as mock:
        route = mock.get("/ip/cloud").respond(500, json={"detail": "no such command"})
        for _ in range(5):
            assert await client.get_cloud_public_address() is None
        assert route.call_count == 1
    await client.aclose()


def test_heavy_passes_are_spread_across_the_interval():
    """Three routers must not stack their expensive half into one tick."""
    interval, started = 60.0, 1000.0
    first = _heavy_deadline(started, interval, 0, 3)
    second = _heavy_deadline(started, interval, 1, 3)
    third = _heavy_deadline(started, interval, 2, 3)
    assert first <= started < second, "only the first router is due on the first tick"
    assert second < third <= started + interval
    assert third - first == pytest.approx(interval * 2 / 3)
    assert _heavy_deadline(started, interval, 0, 1) <= started, "a lone router is due at once"


@pytest.mark.asyncio
async def test_a_slow_router_no_longer_sets_the_fleet_clock(tmp_path, monkeypatch):
    """One lagging device must not decide how often the healthy ones are sampled.

    Measured on the container: three routers, one local and two across the
    Internet, served by one sequential loop - the local router, which answers in
    milliseconds, was sampled once every ~100 s because a cycle also contained a
    1.0 s TLS handshake twice over and the occasional 5 s connect timeout. The
    assertion here is about independence, not speed: the fast router must keep
    collecting while the slow one is still awaiting its own answer.
    """
    import asyncio

    from backend.app import main

    class Router:
        def __init__(self, rid, host):
            self.id, self.host, self.name = rid, host, f"R{rid}"

    class FakeManager:
        async def get_all_active_routers(self, session):
            return [Router(1, "192.0.2.1"), Router(2, "192.0.2.2")]

        async def get_client(self, router_id, session=None):
            return router_id

    samples = {1: 0, 2: 0}

    class FakeCollector:
        async def collect_and_store(self, session, router_id, client, resource=None):
            if router_id == 2:
                await asyncio.sleep(0.15)  # the slow, remote router
            samples[router_id] += 1

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tick.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def no_backfill():
        return None

    async def no_heavy(*args, **kwargs):
        return None

    monkeypatch.setattr(main, "AsyncSessionLocal", factory)
    monkeypatch.setattr(main, "router_manager", FakeManager())
    monkeypatch.setattr(main, "_backfill_interface_rollups_once", no_backfill)
    monkeypatch.setattr(main, "_reconcile_queues", no_heavy)
    monkeypatch.setattr(main, "_reconcile_traffic_state", no_heavy)
    monkeypatch.setattr(
        "backend.app.services.metrics_collector.metrics_collector", FakeCollector()
    )
    monkeypatch.setattr(main.settings, "POLL_INTERVAL_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(main.settings, "HEAVY_SYNC_INTERVAL_SECONDS", 3600.0, raising=False)
    # Auto-scan off: the point is the telemetry half, not device discovery.
    from backend.app.db.models import AppSetting

    async with factory() as session:
        session.add(AppSetting(key="auto_scan_enabled", value="false"))
        await session.commit()

    task = asyncio.create_task(main.background_sync_worker())
    await asyncio.sleep(0.5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await engine.dispose()

    assert samples[1] >= 3, f"the local router should have sampled repeatedly, got {samples[1]}"
    assert samples[2] >= 1, "the slow router must still be collected from"
    assert samples[1] > samples[2], (
        f"cadence is per router, not fleet-wide: fast={samples[1]} slow={samples[2]}"
    )


# --- what the app is spending -----------------------------------------------


def test_diagnostics_separates_the_process_from_the_cgroup():
    """Counters are process-wide accumulators, so assert on the delta."""
    before = diagnostics.request_counts().get("192.0.2.53", 0)
    diagnostics.note_request("192.0.2.53")
    diagnostics.note_request("192.0.2.53")
    diagnostics.record("sync.telemetry", 0.5)
    diagnostics.record("sync.telemetry", 1.5)
    snap = diagnostics.snapshot()
    assert snap["routeros_requests_by_host"]["192.0.2.53"] - before == 2
    assert snap["routeros_requests_total"] >= 2
    passes = snap["background_passes"]["sync.telemetry"]
    assert passes["max_seconds"] == 1.5
    assert passes["count"] >= passes["avg_seconds"] and passes["count"] >= 2
    assert snap["memory_peak_bytes"] > 0
    if diagnostics._is_linux():
        assert snap["memory_bytes"] and snap["memory_bytes"] > 0, (
            "the process's own resident set, which is what the container's "
            "cgroup figure overstates by counting the page cache"
        )


@pytest.mark.asyncio
async def test_every_rest_call_through_the_pool_is_counted(router_settings):
    """The counter lives in the transport, so no call site can skip it."""
    client = RouterOSClient(router_settings)
    before = diagnostics.request_counts().get("192.168.88.1", 0)
    with respx.mock(base_url="https://192.168.88.1:443/rest") as mock:
        mock.get("/system/resource").respond(200, json=[
            {"cpu-load": "1", "free-memory": "2", "total-memory": "3", "uptime": "1d"}
        ])
        for _ in range(3):
            await client.get_system_resource()
    after = diagnostics.request_counts().get("192.168.88.1", 0)
    assert after - before == 3
    await client.aclose()


@pytest.mark.asyncio
async def test_the_diagnostics_endpoint_needs_no_router_and_no_database():
    """"Is it using too much?" must be answerable while the router is down."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.get("/api/v1/system/diagnostics")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["app_version"] == app.version
    assert data["cadence_seconds"]["heavy_sync"] == Settings().HEAVY_SYNC_INTERVAL_SECONDS
    assert set(["log_file", "background_passes", "memory_bytes"]) <= set(data)


@pytest.mark.asyncio
async def test_the_app_log_is_readable_over_the_api(point_logging_at):
    """GET /logs?source=app - the only way to read them on a router container."""
    point_logging_at()
    logging.getLogger("mikroman.main").warning("Interface rollup tick error for router 2: locked")
    _flush()

    async def no_database():
        # The app source must not need a router row, or even a working database.
        yield None  # type: ignore[misc]

    app.dependency_overrides[get_db] = no_database
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as api:
            response = await api.get("/api/v1/logs?source=app&limit=50")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    rows = response.json()["data"]
    assert any("Interface rollup tick error" in row["message"] for row in rows)
    assert all(row["router_id"] is None for row in rows), "these are the app's own lines, not a router's"
    assert rows[-1]["severity"] == "warning"
    assert rows[-1]["topics"] == "mikroman.main"
