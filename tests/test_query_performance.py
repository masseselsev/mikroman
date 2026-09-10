"""Tests for the query-performance work: indexes, planner stats, eager-load caps.

Every number quoted here was measured against a copy of the live router
database (hundreds of thousands of ``interface_metrics`` rows, and ``device_history`` rows of
which one device holds tens of thousands), not estimated:

    interface metrics, 1 h window    ~40x faster   (composite index + ANALYZE)
    interface metrics, 6 h window    ~45x faster
    background rollup recompute      ~2x faster
    select(Device) with history      ~100x faster   (noload in the analytics path)

Absolute milliseconds are left out: they belong to one machine and one
database, and quoting them here would invite reading them as a product spec.
"""
import inspect
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import noload, selectinload

from backend.app.db import session as db_session
from backend.app.db.models import (
    Base,
    Device,
    DeviceHistory,
    DeviceTrafficRollup,
    InterfaceMetric,
    Router,
    TrafficRollup,
    User,
)


def _model_composites():
    """{index name: (table, columns)} for every composite Index declared on a model."""
    out = {}
    for table in Base.metadata.tables.values():
        for index in table.indexes:
            cols = tuple(column.name for column in index.columns)
            if len(cols) > 1:
                out[index.name] = (table.name, cols)
    return out


def test_runtime_index_list_matches_the_models():
    """``init_db`` creates indexes by name, from a list that is not the models'.

    Nothing compares the two, so an index added to a model and forgotten in
    ``_ensure_query_indexes`` silently never appears on an existing database -
    ``create_all`` does not add indexes to tables that are already there. Same
    for the reverse: a name in the runtime list with no column set behind it.
    """
    declared = _model_composites()
    runtime = {
        name: (table, tuple(columns.replace(" ", "").split(",")))
        for name, table, columns in db_session._INDEXES
    }
    assert set(runtime) == set(declared), (
        f"only in models: {sorted(set(declared) - set(runtime))}; "
        f"only in session.py: {sorted(set(runtime) - set(declared))}"
    )
    for name, (table, columns) in runtime.items():
        assert declared[name] == (table, columns), f"{name} columns drifted"


def test_migration_declares_the_same_indexes():
    """The Alembic revision, the runtime list and the models are one change."""
    import importlib.util
    from pathlib import Path

    path = Path("backend/migrations/versions/024_query_indexes.py")
    spec = importlib.util.spec_from_file_location("mig024", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    migration = {name: (table, tuple(cols)) for name, table, cols in module.INDEXES}
    declared = _model_composites()
    assert set(migration) <= set(declared), f"migration creates indexes no model declares: " \
                                           f"{sorted(set(migration) - set(declared))}"
    for name, (table, cols) in migration.items():
        assert declared[name] == (table, cols), f"{name} differs between migration and model"


@pytest.mark.asyncio
async def test_a_router_id_and_time_filter_prefers_the_composite_index():
    """The planner must use (router_id, timestamp), not router_id alone.

    Without statistics it chose `ix_interface_metrics_router_id` and walked
    hundreds of thousands of index entries to answer a one-hour question. The plan text is the
    assertion, because the row count is the thing that changed.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _stats(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        base = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc).replace(tzinfo=None)
        session.add_all([
            InterfaceMetric(
                router_id=1, interface_name="ether1", rx_rate_bps=1000, tx_rate_bps=500,
                rx_bytes_total=i * 100, tx_bytes_total=i * 10,
                timestamp=base + timedelta(seconds=i * 10),
            )
            for i in range(5000)
        ])
        await session.commit()
        # Statistics, because that is half of what the change does: without them
        # the planner picked a router-id index and walked the whole table.
        await session.execute(text("ANALYZE"))

    async with factory() as session:
        rows = (await session.execute(text(
            "explain query plan select 1 from interface_metrics "
            "where router_id = 1 and timestamp > '2026-09-08 12:30:00'"
        ))).fetchall()
        plan = " ".join(str(r[-1]) for r in rows)
        assert "ix_interface_metrics_router_time" in plan, plan
        assert "SEARCH" in plan, f"expected an index seek, got: {plan}"
    await engine.dispose()


@pytest.mark.asyncio
async def test_the_upgrade_path_creates_every_declared_index(tmp_path):
    """An installed database is never re-created, so it has to be brought forward.

    `create_all` skips existing tables - and with them any index declared on
    them later - and nothing in the runtime path runs Alembic, so
    `_ensure_query_indexes` is the only way a router that has been collecting
    for months gets the composite index its queries have been wanting. This runs
    it against a temporary file on purpose rather than reloading the session
    module: reloading strands the shared engine's pool, and an aiosqlite
    connection finalised after its own event loop is closed raises in that
    thread, not here.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all builds the indexes for a fresh database, which is exactly
        # why it is no help to an existing one. Drop them to get the shape of an
        # install that has been collecting for months.
        for name, _table, _cols in db_session._INDEXES:
            await conn.execute(text(f"DROP INDEX {name}"))

    async with engine.begin() as conn:
        created = await db_session._ensure_query_indexes(conn)
    assert created == len(db_session._INDEXES)

    async with engine.begin() as conn:
        names = {
            row[0]
            for row in (await conn.execute(text(
                "select name from sqlite_master where type='index'"
            ))).fetchall()
        }
        second = await db_session._ensure_query_indexes(conn)
    for name, _table, _cols in db_session._INDEXES:
        assert name in names, f"{name} was not created"
    assert second == 0, "a second pass must find the schema already current"
    await engine.dispose()


def test_init_db_runs_the_index_pass():
    """Wiring: the upgrade path is called at start-up, not merely available.

    Nothing else would notice `_ensure_query_indexes` being orphaned - the
    indexes would simply never appear on a deployed database, and the queries
    would stay at a whole-index walk with every test still
    passing, because each test builds its own engine.
    """
    source = inspect.getsource(db_session.init_db)
    assert "_ensure_query_indexes" in source
    assert "PRAGMA optimize" in source


@pytest.mark.asyncio
async def test_the_analytics_query_does_not_materialise_device_history():
    """One eager collection turned a 5-row read into a 35 186-row one.

    `Device.history` is `lazy="selectin"`, so loading devices loads every event
    each one ever produced. The analytics path never reads it, and paying
    484.6 ms of the request for it on a router that has one flapping phone is
    what made switching a preset feel stuck.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


    async with factory() as session:
        user = User(name="Owner", router_id=None)
        session.add(user)
        await session.commit()
        device = Device(mac_address="AA:BB:CC:DD:EE:01", user_id=user.id, router_id=None)
        session.add(device)
        await session.commit()
        session.add_all([
            DeviceHistory(device_id=device.id, mac_address="AA:BB:CC:DD:EE:01",
                          event_type="ip_changed", details=f"event {i}")
            for i in range(50)
        ])
        await session.commit()

    async def load(with_option):
        async with factory() as session:
            query = select(Device)
            if with_option is not None:
                query = query.options(with_option)
            devices = (await session.execute(query)).scalars().all()
            # Touch the collection: with noload it stays unloaded and reading it
            # would show an empty list, which is exactly what the analytics path
            # must not depend on.
            return devices, [len(d.history) for d in devices]

    plain_count, plain_history = await load(None)
    assert plain_history == [50], "the relationship is eager by default - that is the bug's mechanism"

    _noloaded, unloaded_history = await load(noload(Device.history))
    assert unloaded_history == [0], "noload must actually skip the collection"

    explicit, loaded = await load(selectinload(Device.history))
    assert loaded == [50], "a path that does want history still gets it, explicitly"
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_historical_traffic_survives_the_unload():
    """End to end: the endpoint that stopped loading history still reports it.

    The user and device rows it returns come from the rollups, so dropping the
    event log must not change a single figure - if it does, something in the
    path was reading `device.history` and would now see an empty list.
    """
    from backend.app.services.analytics_engine import AnalyticsEngine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    today = date(2026, 9, 8)
    async with factory() as session:
        router = Router(name="R", host="127.0.0.1", is_active=True, is_default=True)
        session.add(router)
        await session.flush()
        user = User(name="Owner", router_id=router.id)
        session.add(user)
        await session.flush()
        device = Device(mac_address="AA:BB:CC:DD:EE:01", user_id=user.id, router_id=router.id)
        session.add(device)
        await session.flush()
        session.add(DeviceHistory(
            device_id=device.id, mac_address="AA:BB:CC:DD:EE:01",
            event_type="discovered", details="first seen",
        ))
        session.add_all([
            TrafficRollup(user_id=user.id, record_date=today, bytes_in=500, bytes_out=100),
            DeviceTrafficRollup(device_id=device.id, record_date=today, bytes_in=500, bytes_out=100),
        ])
        await session.commit()

    async with factory() as session:
        result = await AnalyticsEngine.get_historical_traffic(
            session, today - timedelta(days=3), today, router_id=router.id, range_preset="7d"
        )
        assert [u.user_name for u in result.users] == ["Owner"]
        assert result.users[0].bytes_in == 500, "rollup sums must survive unloading the event log"
        assert result.users[0].bytes_out == 100
        assert len(result.devices) == 1
        assert result.devices[0].bytes_in == 500
        # The timeline carries gateway daily totals - a different ledger from the
        # per-device rollups above, which is why the figure is 0 here - but the
        # day must still be present: that is the proof nothing in this path was
        # leaning on `device.history` arriving as a side effect of the query.
        assert [point.record_date for point in result.timeline] == [
            today - timedelta(days=3), today - timedelta(days=2),
            today - timedelta(days=1), today,
        ]
    await engine.dispose()


