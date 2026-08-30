import logging
import os
from typing import AsyncGenerator

from sqlalchemy import text
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

        # SQLite automatic schema evolution for runtime changes
        if "sqlite" in settings.DATABASE_URL:
            try:
                res = await conn.execute(text("PRAGMA table_info(routers)"))
                columns = [row[1] for row in res.fetchall()]
                if columns and "ca_cert" not in columns:
                    await conn.execute(text("ALTER TABLE routers ADD COLUMN ca_cert TEXT"))
            except Exception:
                pass

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
                }
                for column, ddl in additions.items():
                    if column not in columns:
                        await conn.execute(text(f"ALTER TABLE devices ADD COLUMN {column} {ddl}"))
            except Exception as e:
                logger.warning(f"Could not apply device schema additions: {e}")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for providing database session to FastAPI endpoints."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
