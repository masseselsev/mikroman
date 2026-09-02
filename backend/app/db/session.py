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


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for providing database session to FastAPI endpoints."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
