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
