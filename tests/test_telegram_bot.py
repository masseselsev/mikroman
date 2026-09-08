import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.core.config import Settings
from backend.app.core.i18n import format_bytes, format_speed, get_text
from backend.app.services.telegram_bot import TelegramBotService


def test_i18n_translations_completeness():
    # Test English
    assert "Router Status" in get_text("status_title", lang="en")
    assert "Active Network Users" in get_text("users_title", lang="en")
    assert "Pause Internet" in get_text("btn_pause", lang="en")
    assert "Reboot Router" in get_text("btn_reboot", lang="en")

    # Test Russian
    assert "Статус роутера" in get_text("status_title", lang="ru")
    assert "Пользователи сети" in get_text("users_title", lang="ru")
    assert "Пауза" in get_text("btn_pause", lang="ru")
    assert "Перезагрузить" in get_text("btn_reboot", lang="ru")

    # Test formatters
    assert format_bytes(500) == "500 B"
    assert format_bytes(1024 * 1024 * 5) == "5.0 MB"
    assert format_bytes(1024 * 1024 * 1024 * 12) == "12.00 GB"

    assert format_speed(500) == "500 bps"
    assert format_speed(25 * 1000 * 1000) == "25.0 Mbps"


@pytest.mark.asyncio
async def test_telegram_bot_initialization():
    settings = Settings(
        TELEGRAM_BOT_TOKEN="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        TELEGRAM_ADMIN_CHAT_IDS=[12345678],
        TELEGRAM_MODE="polling"
    )
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    bot_service = TelegramBotService(
        session_factory=session_factory,
        config=settings
    )

    assert bot_service.bot is not None
    assert bot_service.dp is not None
    assert bot_service._is_authorized(12345678) is True
    assert bot_service._is_authorized(99999999) is False

    await engine.dispose()


async def _db_with(settings_rows):
    """An in-memory database seeded with AppSetting rows, as the wizard writes them."""
    from backend.app.db.models import AppSetting, Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        for key, value in settings_rows.items():
            session.add(AppSetting(key=key, value=value))
        await session.commit()
    return engine, factory


@pytest.mark.asyncio
async def test_stored_telegram_settings_are_applied_at_startup():
    """The token lives in the database; the service used to read only the env.

    That mismatch meant a restart brought the app back with alerts off and one
    informational line in the log - on the router container, that was every boot.
    """
    engine, factory = await _db_with({
        "telegram_bot_token": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        "telegram_admin_ids": "987654321,1122334455",
        "telegram_mode": "polling",
        "telegram_webhook_url": "",
    })
    service = TelegramBotService(session_factory=factory, config=Settings(TELEGRAM_BOT_TOKEN=None))
    assert service.bot is None, "nothing in the environment, so nothing yet"

    assert await service.load_persisted_settings() is True
    assert service.bot is not None and service.dp is not None
    assert service.config.TELEGRAM_ADMIN_CHAT_IDS == [987654321, 1122334455]
    # An empty stored row must not overwrite anything.
    assert service.config.TELEGRAM_WEBHOOK_URL in (None, "")
    await engine.dispose()


@pytest.mark.asyncio
async def test_environment_token_survives_a_database_with_nothing_saved():
    engine, factory = await _db_with({})
    settings = Settings(TELEGRAM_BOT_TOKEN="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
    service = TelegramBotService(session_factory=factory, config=settings)
    assert await service.load_persisted_settings() is True
    assert service.config.TELEGRAM_BOT_TOKEN.startswith("123456:")
    await engine.dispose()


@pytest.mark.asyncio
async def test_an_unreadable_database_leaves_the_bot_off_but_serving():
    class Broken:
        def __call__(self):
            raise ConnectionError("database is locked")

    service = TelegramBotService(session_factory=Broken(), config=Settings(TELEGRAM_BOT_TOKEN=None))
    assert await service.load_persisted_settings() is False
    assert service.bot is None
