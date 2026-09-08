import logging
import os
from typing import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.core.config import settings
from backend.app.db.models import Base

logger = logging.getLogger("mikroman.db")

# Ensure data directory exists for SQLite
if "sqlite" in settings.DATABASE_URL:
    db_path = settings.DATABASE_URL.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
    if db_path.startswith("./") or "/" in db_path:
        dir_name = os.path.dirname(db_path)
        if dir_name:
            try:
                os.makedirs(dir_name, exist_ok=True)
            except OSError:
                os.makedirs("./data", exist_ok=True)
                settings.DATABASE_URL = "sqlite+aiosqlite:///./data/app.db"

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {}
)


if "sqlite" in settings.DATABASE_URL:

    @event.listens_for(engine.sync_engine, "connect")
    def _configure_sqlite_connection(dbapi_connection, _connection_record):
        """Apply the durability and concurrency PRAGMAs on every new connection.

        SQLite applies these per connection, not per database, so they have to
        be set on connect rather than once at startup.

        ``journal_mode=WAL``
            Readers no longer block the writer and vice versa, so the 10-second
            background poll loop and a dashboard request can no longer collide
            on "database is locked". It also makes a *hot* backup safe: the
            SQLite online-backup API can copy a consistent snapshot while the
            app keeps writing, which is what ``scripts/backup.sh`` relies on.
            The setting is written into the database header and persists.

        ``synchronous=NORMAL``
            The pairing WAL is designed for. The database cannot be corrupted by
            an application crash; only an OS crash or power loss in the moment
            between a commit and the next checkpoint can drop the most recent
            transaction. That transaction is at most one telemetry sample - the
            counters it was derived from are still on the router and the next
            poll re-reads them against the persisted baseline - so the exposure
            is a few seconds of history, never a broken file. ``FULL`` (the
            default) fsyncs on every commit and buys durability this workload
            does not need.

        ``busy_timeout=5000``
            Wait up to five seconds for a lock instead of failing immediately.
            Set explicitly rather than trusting the driver default.
        """
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)


#: Composite indexes the aggregators need, as (name, table, columns).
#:
#: Declared on the models *and* here, and cross-checked by
#: ``tests/test_query_performance.py``: `create_all` skips tables that already
#: exist along with any index on them, so a list is the only way an installed
#: database gets them - and a name that drifts from the model is an index that
#: silently never appears.
_INDEXES = (
    ("ix_interface_metrics_router_time", "interface_metrics", "router_id, timestamp"),
    ("ix_interface_metrics_name_time", "interface_metrics", "interface_name, timestamp"),
    ("ix_system_metrics_router_time", "system_metrics", "router_id, timestamp"),
    ("ix_device_history_device_time", "device_history", "device_id, created_at"),
    ("ix_device_rollups_device_date", "device_traffic_rollups", "device_id, record_date"),
    ("ix_traffic_rollups_user_date", "traffic_rollups", "user_id, record_date"),
    ("ix_router_rollups_router_date", "router_traffic_rollups", "router_id, record_date"),
)


async def _ensure_query_indexes(conn) -> int:
    """Create the composite indexes the aggregators need, if they are missing.

    ``create_all()`` skips tables that already exist - and with them any index
    declared on those tables later - while this application has never run
    Alembic at start-up. So the same set lives in
    ``backend/migrations/versions/024_query_indexes.py`` for a managed schema and
    here for the installs that are already running, which is the pattern the
    column additions above already follow.

    Returns the number of indexes created, so the start-up log says something
    specific the first time a slow router gets faster.
    """
    created = 0
    for name, table, columns in _INDEXES:
        try:
            before = (await conn.execute(
                text("SELECT count(*) FROM sqlite_master WHERE type='index' AND name = :n"),
                {"n": name},
            )).scalar()
            if before:
                continue
            await conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})"))
            created += 1
        except Exception as e:
            # An index that cannot be created (a column the schema does not have
            # yet, a table that is mid-migration) must not stop the app: it just
            # stays as slow as it was, which is a worse outcome than a crash but
            # not a broken one.
            logger.warning(f"Could not create {name} on {table}: {e}")
    return created


async def init_db() -> None:
    """Initialize database tables and sync dynamic schema columns."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        if "sqlite" in settings.DATABASE_URL:
            # Report the effective journal mode once at startup. A value other
            # than "wal" here means the PRAGMA above did not take - most likely
            # the database is on a filesystem that cannot support WAL's shared
            # memory (an NFS mount), in which case hot backups are unsafe and
            # scripts/backup.sh should stop the container first.
            mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
            if str(mode).lower() == "wal":
                logger.info("SQLite journal mode: WAL (hot backups are safe)")
            else:
                logger.warning(
                    f"SQLite journal mode is '{mode}', not WAL. Concurrent access is "
                    f"serialised and an online backup may capture a torn file; take "
                    f"backups with the container stopped."
                )

            # Indexes and planner statistics, applied before anything below can
            # return early. `create_all` will not add an index to a table that
            # already exists, and nothing in the runtime path runs Alembic, so
            # this is how a database that has been growing for months - the
            # 691 142-row interface_metrics table on the router, for one - gets
            # the composite index its queries have been wanting.
            created = await _ensure_query_indexes(conn)
            if created:
                # Statistics are what make the planner choose the new index at
                # all: without sqlite_stat1 it kept walking a router_id index
                # over 296 403 entries for a one-hour question. A full ANALYZE
                # costs ~1 s on a desktop (measured, 691 142 rows) and several
                # times that on the ARM board, so it runs exactly once per
                # database - here, when the plan actually changed - and
                # `PRAGMA optimize` (free) keeps it from being stale later.
                logger.info(
                    f"Created {created} query index/indices for the history and metrics reads; "
                    f"refreshing planner statistics"
                )
                await conn.execute(text("ANALYZE"))
            await conn.execute(text("PRAGMA optimize"))

        # SQLite automatic schema evolution for runtime changes
        if "sqlite" in settings.DATABASE_URL:
            try:
                res = await conn.execute(text("PRAGMA table_info(routers)"))
                columns = [row[1] for row in res.fetchall()]
                # create_all() never alters an existing table, and this install
                # is not on Alembic, so a column added to the Router model after
                # the database was first created must be applied here too - or
                # every `SELECT ... FROM routers` fails with "no such column" and
                # the app looks like it has lost its router configuration.
                if columns:
                    router_additions = {
                        "ca_cert": "TEXT",
                        "comment": "TEXT",
                        "serial_number": "VARCHAR(120)",
                        "archived_at": "DATETIME",
                    }
                    for column, ddl in router_additions.items():
                        if column not in columns:
                            await conn.execute(text(f"ALTER TABLE routers ADD COLUMN {column} {ddl}"))
            except Exception as e:
                logger.warning(f"Could not apply router schema additions: {e}")

            try:
                res = await conn.execute(text("PRAGMA table_info(devices)"))
                columns = [row[1] for row in res.fetchall()]
                if not columns:
                    return
                # create_all() never alters an existing table, so columns added
                # after a database was first created are applied here as well as
                # in the Alembic migrations.
                additions = {
                    "is_hidden": "BOOLEAN NOT NULL DEFAULT 0",
                    "linked_to_device_id": "INTEGER",
                    "connection_kind": "VARCHAR(20)",
                    "wifi_links": "TEXT",
                    "is_container": "BOOLEAN NOT NULL DEFAULT 0",
                    "is_deleted": "BOOLEAN NOT NULL DEFAULT 0",
                }
                for column, ddl in additions.items():
                    if column not in columns:
                        await conn.execute(text(f"ALTER TABLE devices ADD COLUMN {column} {ddl}"))
            except Exception as e:
                logger.warning(f"Could not apply device schema additions: {e}")

            try:
                res = await conn.execute(text("PRAGMA table_info(users)"))
                columns = [row[1] for row in res.fetchall()]
                if columns:
                    if "sort_order" not in columns:
                        await conn.execute(text("ALTER TABLE users ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"))
                    if "router_id" not in columns:
                        await conn.execute(text("ALTER TABLE users ADD COLUMN router_id INTEGER REFERENCES routers(id) ON DELETE SET NULL"))
                        router_row = (await conn.execute(text("SELECT id FROM routers WHERE is_default = 1 LIMIT 1"))).fetchone()
                        if not router_row:
                            router_row = (await conn.execute(text("SELECT id FROM routers ORDER BY id ASC LIMIT 1"))).fetchone()
                        if router_row:
                            await conn.execute(text(f"UPDATE users SET router_id = {router_row[0]} WHERE router_id IS NULL"))
            except Exception as e:
                logger.warning(f"Could not apply users schema additions: {e}")


async def encrypt_legacy_secrets() -> int:
    """Rewrite any still-plain-text credential in the database as ciphertext.

    ``EncryptedString`` encrypts on write, so rows written before that column
    type existed stay readable but remain plain text on disk until something
    happens to save them again - which for a router nobody edits is never. This
    runs once at startup and closes that gap, reading the raw column so it can
    tell an encrypted value from a legacy one.

    Returns the number of values rewritten.
    """
    from backend.app.core.secrets import PREFIX, encrypt_secret

    targets = (
        ("routers", "password"),
        ("router_backups", "backup_password"),
    )
    rewritten = 0
    async with engine.begin() as conn:
        for table, column in targets:
            try:
                rows = (await conn.execute(
                    text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL AND {column} != ''")
                )).fetchall()
            except Exception as e:
                logger.debug(f"Skipping secret migration for {table}.{column}: {e}")
                continue

            for row_id, raw in rows:
                if str(raw).startswith(PREFIX):
                    continue
                await conn.execute(
                    text(f"UPDATE {table} SET {column} = :val WHERE id = :id"),
                    {"val": encrypt_secret(str(raw)), "id": row_id},
                )
                rewritten += 1

    if rewritten:
        logger.info(f"Encrypted {rewritten} stored credential(s) that were still in plain text")
    return rewritten


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for providing database session to FastAPI endpoints."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
