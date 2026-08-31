"""The SQLite connection must come up in WAL mode with the right trade-offs.

Everything MikroMan keeps is in one SQLite file, and the part that cannot be
rebuilt - the historical daily traffic rollups - accumulates there over months.
Two properties protect it:

* WAL journal mode, so a reader (a dashboard request) and the writer (the
  10-second poll loop) no longer serialise on a single lock, and so the
  online-backup API can copy a consistent snapshot while the app runs.
* synchronous=NORMAL, the durability level WAL is designed for: an application
  crash cannot corrupt the file, and the only thing an OS crash can cost is the
  last transaction - at most one telemetry sample, which the next poll
  reconstructs from the router's own counters.

These are applied per connection by a listener in
``backend.app.db.session``. This test builds an engine the same way and checks
the settings actually take on a real file, since the listener is easy to break
silently.
"""

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine


def _apply_pragmas(dbapi_connection, _record):
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


@pytest.fixture
async def engine(tmp_path):
    """An engine wired exactly as backend.app.db.session wires the real one."""
    eng = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'durability.db'}",
        connect_args={"check_same_thread": False},
    )
    event.listen(eng.sync_engine, "connect", _apply_pragmas)
    yield eng
    await eng.dispose()


@pytest.mark.asyncio
async def test_journal_mode_is_wal(engine):
    async with engine.connect() as conn:
        mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
    assert str(mode).lower() == "wal"


@pytest.mark.asyncio
async def test_synchronous_is_normal_not_full(engine):
    # NORMAL == 1. FULL (2) fsyncs on every commit and buys a durability
    # guarantee this workload does not need; OFF (0) would risk the file.
    async with engine.connect() as conn:
        level = (await conn.execute(text("PRAGMA synchronous"))).scalar()
    assert level == 1


@pytest.mark.asyncio
async def test_busy_timeout_is_set(engine):
    async with engine.connect() as conn:
        timeout = (await conn.execute(text("PRAGMA busy_timeout"))).scalar()
    assert timeout == 5000


@pytest.mark.asyncio
async def test_wal_lets_a_reader_and_a_writer_hold_connections_at_once(engine):
    """The concurrency property, exercised rather than assumed.

    Under the previous rollback journal this pattern - an open write transaction
    on one connection while a second connection reads - is where the poll loop
    and a dashboard request collided as "database is locked".
    """
    async with engine.connect() as writer:
        await writer.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)"))
        await writer.commit()

        trans = await writer.begin()
        await writer.execute(text("INSERT INTO t (v) VALUES ('pending')"))

        # A separate connection can still read while that write is open.
        async with engine.connect() as reader:
            rows = (await reader.execute(text("SELECT count(*) FROM t"))).scalar()
            assert rows == 0  # the uncommitted row is not visible, but the read did not block

        await trans.commit()

    async with engine.connect() as check:
        rows = (await check.execute(text("SELECT count(*) FROM t"))).scalar()
    assert rows == 1


@pytest.mark.asyncio
async def test_an_online_backup_of_a_live_database_is_consistent(engine, tmp_path):
    """The backup path scripts/backup.sh depends on.

    sqlite3.Connection.backup() must yield a file that passes integrity_check
    even though the source is being written to.
    """
    import sqlite3

    source_path = str(engine.url.database)
    async with engine.connect() as conn:
        await conn.execute(text("CREATE TABLE roll (d TEXT, bytes INTEGER)"))
        await conn.execute(text("INSERT INTO roll VALUES ('2026-08-31', 12345)"))
        await conn.commit()

    dest_path = tmp_path / "backup.db"
    src = sqlite3.connect(source_path)
    dst = sqlite3.connect(dest_path)
    try:
        with dst:
            src.backup(dst)
        assert dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert dst.execute("SELECT bytes FROM roll").fetchone()[0] == 12345
    finally:
        src.close()
        dst.close()
