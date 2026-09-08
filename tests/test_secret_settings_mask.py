"""`GET /system/settings` must not hand out the Telegram bot token.

The API has no session identity, so a value returned here is readable by
whatever can reach the port. On the router-container deployment that is anything
on the LAN. These tests pin both halves of the fix: the value is masked on the
way out, and the masked form round-trips back in without overwriting the real
credential or being handed to the running bot.
"""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.api.v1.endpoints.system import SECRET_PLACEHOLDER
from backend.app.db.models import AppSetting, Base
from backend.app.db.session import get_db
from backend.app.main import app

TOKEN_KEY = "telegram_bot_token"
REAL_TOKEN = "1234567:AAH-real-token-not-a-placeholder"


@pytest.fixture
async def settings_api():
    """Client plus a session factory, so a test can read what was stored.

    Asserting on the API response alone cannot tell "masked on the way out" from
    "asterisks in the database", and the second one is the actual failure.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, session_factory
    app.dependency_overrides.clear()
    await engine.dispose()


async def _stored(session_factory, key: str):
    async with session_factory() as session:
        setting = await session.get(AppSetting, key)
        return setting.value if setting else None


@pytest.mark.asyncio
async def test_a_stored_token_comes_back_masked(settings_api):
    client, session_factory = settings_api
    assert (await client.post("/api/v1/system/settings", json={TOKEN_KEY: REAL_TOKEN})).status_code == 200

    data = (await client.get("/api/v1/system/settings")).json()["data"]
    assert data[TOKEN_KEY] == SECRET_PLACEHOLDER
    assert REAL_TOKEN not in str(data)
    # The real value is what stays on disk.
    assert await _stored(session_factory, TOKEN_KEY) == REAL_TOKEN


@pytest.mark.asyncio
async def test_an_absent_token_is_not_reported_as_set(settings_api):
    """The placeholder means "configured"; inventing it for an empty field would
    tell the operator a bot is set up when none is."""
    client, _ = settings_api
    data = (await client.get("/api/v1/system/settings")).json()["data"]
    assert data.get(TOKEN_KEY, "") != SECRET_PLACEHOLDER


@pytest.mark.asyncio
async def test_round_tripping_the_placeholder_keeps_the_token(settings_api):
    """The settings form loads the masked value and posts the whole form back.

    Without the guard that single click replaces a working credential with eight
    asterisks, and the only symptom is a bot that stopped answering.
    """
    client, session_factory = settings_api
    await client.post("/api/v1/system/settings", json={TOKEN_KEY: REAL_TOKEN})

    resp = await client.post("/api/v1/system/settings", json={TOKEN_KEY: SECRET_PLACEHOLDER, "theme": "dark"})
    assert resp.status_code == 200
    assert await _stored(session_factory, TOKEN_KEY) == REAL_TOKEN
    # Everything else in the same request still saves.
    assert await _stored(session_factory, "theme") == "dark"


@pytest.mark.asyncio
async def test_a_real_replacement_and_a_clear_still_work(settings_api):
    client, session_factory = settings_api
    await client.post("/api/v1/system/settings", json={TOKEN_KEY: REAL_TOKEN})

    await client.post("/api/v1/system/settings", json={TOKEN_KEY: "999:new"})
    assert await _stored(session_factory, TOKEN_KEY) == "999:new"

    await client.post("/api/v1/system/settings", json={TOKEN_KEY: ""})
    assert await _stored(session_factory, TOKEN_KEY) == ""


@pytest.mark.asyncio
async def test_the_running_bot_is_never_reconfigured_with_the_mask(settings_api, monkeypatch):
    """Storing the mask is one bug; handing it to the live service is another.

    `reconfigure` restarts polling with whatever token it is given, so a
    placeholder reaching it takes the bot down even if the database is intact.
    """
    from backend.app.api.v1.endpoints import telegram as telegram_module

    seen = {}

    class FakeService:
        async def reconfigure(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(telegram_module, "telegram_bot_service", FakeService(), raising=False)

    client, _ = settings_api
    await client.post("/api/v1/system/settings", json={TOKEN_KEY: REAL_TOKEN})
    seen.clear()
    await client.post("/api/v1/system/settings", json={TOKEN_KEY: SECRET_PLACEHOLDER})
    assert seen.get("token") is None, "the placeholder was passed through as a new token"

    await client.post("/api/v1/system/settings", json={TOKEN_KEY: "888:changed"})
    assert seen.get("token") == "888:changed"
