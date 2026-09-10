"""The Telegram bot must never end up with two polling sessions.

Reproduced on a live device: pressing Save in Settings routes through
``reconfigure()``, which stopped the old poller by cancelling its wrapper task
and replaced ``self.bot`` with a new object. The old HTTPS session was dropped
without being closed, Telegram kept its ``getUpdates`` slot open, and the new
poller answered every retry with ``Conflict: terminated by other getUpdates
request`` - once a second, indefinitely, with the alert pipeline wedged behind
it. These tests pin the teardown rather than the retry behaviour, because the
retry log is the symptom and the unclosed session is the cause.
"""
import asyncio

import pytest

from backend.app.core.config import Settings
from backend.app.services.telegram_bot import TelegramBotService

# Synthetic mock token; nothing here reaches the network.
TOKEN = "test_mock_token_not_real"


class FakeBot:
    def __init__(self, label):
        self.label = label
        self.closed = False

    async def close(self):
        self.closed = True
        return True

    async def delete_webhook(self, **_kwargs):
        return True


class FakeDispatcher:
    """Stands in for the bits of aiogram's polling contract this service uses."""

    def __init__(self, label):
        self.label = label
        self.stop_polling_calls = 0
        self.polling = False
        self._stop = asyncio.Event()
        self._started = asyncio.Event()

    async def start_polling(self, _bot):
        self.polling = True
        self._started.set()
        try:
            await self._stop.wait()
        finally:
            self.polling = False

    async def stop_polling(self):
        self.stop_polling_calls += 1
        self._stop.set()


def make_service():
    """A service with no bot built, so `_init_bot` can be installed per test."""
    config = Settings(TELEGRAM_BOT_TOKEN="", TELEGRAM_MODE="polling")
    service = TelegramBotService(session_factory=None, config=config)
    service.config.TELEGRAM_BOT_TOKEN = TOKEN
    return service


@pytest.mark.asyncio
async def test_stop_closes_the_session_and_stops_polling():
    """Cancelling the wrapper task is not enough - the session is the conflict."""
    service = make_service()
    bot, dp = FakeBot("old"), FakeDispatcher("old")
    service.bot, service.dp = bot, dp
    service.polling_task = asyncio.create_task(dp.start_polling(bot))
    await dp._started.wait()

    await service.stop()

    assert dp.stop_polling_calls == 1, "polling was never asked to stop"
    assert bot.closed is True, "the HTTPS session was dropped without closing"
    assert dp.polling is False
    assert service.polling_task is None


@pytest.mark.asyncio
async def test_a_second_start_does_not_open_a_second_session():
    service = make_service()

    async def _no_database_read():
        return True

    service.load_persisted_settings = _no_database_read
    service.bot, service.dp = FakeBot("a"), FakeDispatcher("a")

    await service.start()
    first = service.polling_task
    assert first is not None

    await service.start()
    assert service.polling_task is first, "start() opened a second polling session"
    await service.stop()


@pytest.mark.asyncio
async def test_reconfigure_leaves_exactly_one_live_poller():
    """The settings-save path: one down, one up, never both.

    Before the fix the old bot was replaced by reference and left with an open
    session, so `getUpdates` conflicted forever.
    """
    service = make_service()

    async def _no_database_read():
        return True

    service.load_persisted_settings = _no_database_read
    old_bot, old_dp = FakeBot("old"), FakeDispatcher("old")
    service.bot, service.dp = old_bot, old_dp
    service.polling_task = asyncio.create_task(old_dp.start_polling(old_bot))
    await old_dp._started.wait()

    new_bot, new_dp = FakeBot("new"), FakeDispatcher("new")

    def _init_fake_bot():
        service.bot, service.dp = new_bot, new_dp

    service._init_bot = _init_fake_bot

    await service.reconfigure(token=TOKEN)
    # The replacement polls in a task, so give it one turn before reading its
    # state; `create_task` alone has not run the coroutine yet.
    await asyncio.sleep(0)

    assert old_bot.closed is True, "the replaced session was never closed"
    assert old_dp.polling is False, "the old poller is still running"
    assert new_dp.polling is True, "the replacement never started"
    live = [d.polling for d in (old_dp, new_dp)].count(True)
    assert live == 1, f"{live} polling sessions alive on one token"

    await service.stop()
    assert new_dp.polling is False


@pytest.mark.asyncio
async def test_stop_survives_a_poller_that_was_never_started():
    """Webhook mode and double-stop both land here; `stop_polling` raises on purpose."""
    service = make_service()
    service.bot, service.dp = FakeBot("x"), FakeDispatcher("x")

    async def _raise():
        raise RuntimeError("Polling is not started")

    service.dp.stop_polling = _raise
    await service.stop()  # must not raise: shutdown runs this unconditionally
    assert service.polling_task is None


@pytest.mark.asyncio
async def test_stop_is_bounded_when_the_poller_ignores_the_signal():
    """The container has ~10 s before SIGKILL; a hung ack cannot eat it."""
    service = make_service()
    bot, dp = FakeBot("stuck"), FakeDispatcher("stuck")

    async def _hang():
        await asyncio.sleep(30)

    dp.stop_polling = _hang
    service.bot, service.dp = bot, dp
    service.polling_task = asyncio.create_task(dp.start_polling(bot))
    await dp._started.wait()

    loop = asyncio.get_running_loop()
    started = loop.time()
    await service.stop()
    assert loop.time() - started < 10, "stop() waited on a poller that never acked"
    assert bot.closed is True
