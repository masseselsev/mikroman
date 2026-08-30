"""Telegram webhook mode: configuration and authenticity.

Webhook mode was reachable from the settings dropdown but had no field for the
URL, so it could not actually be configured. The endpoint also accepted any
unauthenticated POST and processed it as a genuine Telegram update, which let
anyone able to reach the app inject bot commands such as /reboot.

Telegram signs webhook deliveries with a secret chosen at set_webhook time and
echoes it in the X-Telegram-Bot-Api-Secret-Token header, which is what this
verifies.
"""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.api.v1.endpoints import telegram as telegram_endpoint
from backend.app.db.models import AppSetting, Base
from backend.app.db.session import get_db
from backend.app.main import app


class FakeService:
    """Stands in for the running bot service."""

    def __init__(self, secret=None):
        self.webhook_secret = secret
        self.processed = []

    async def process_webhook_update(self, update):
        self.processed.append(update)


@pytest.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.session_factory = factory
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


UPDATE = {"update_id": 1, "message": {"text": "/reboot", "chat": {"id": 42}}}


@pytest.mark.asyncio
async def test_forged_update_is_rejected_when_a_secret_is_configured(client):
    service = FakeService(secret="s3cret")
    telegram_endpoint.set_telegram_service(service)

    resp = await client.post("/api/v1/telegram/webhook", json=UPDATE)
    assert resp.status_code == 403
    assert service.processed == [], "an unauthenticated update must never be processed"

    resp = await client.post(
        "/api/v1/telegram/webhook",
        json=UPDATE,
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert resp.status_code == 403
    assert service.processed == []


@pytest.mark.asyncio
async def test_authentic_update_is_processed(client):
    service = FakeService(secret="s3cret")
    telegram_endpoint.set_telegram_service(service)

    resp = await client.post(
        "/api/v1/telegram/webhook",
        json=UPDATE,
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    assert resp.status_code == 200
    assert service.processed == [UPDATE]


@pytest.mark.asyncio
async def test_updates_are_refused_when_no_secret_has_been_established(client):
    """Fail closed: without a secret there is no way to tell Telegram from anyone else."""
    service = FakeService(secret=None)
    telegram_endpoint.set_telegram_service(service)

    resp = await client.post("/api/v1/telegram/webhook", json=UPDATE)
    assert resp.status_code == 403
    assert service.processed == []


@pytest.mark.asyncio
async def test_webhook_url_and_secret_persist_through_settings(client):
    """The URL must be storable, or webhook mode cannot be configured at all."""
    resp = await client.post("/api/v1/system/settings", json={
        "telegram_mode": "webhook",
        "telegram_webhook_url": "https://example.org/api/v1/telegram/webhook",
    })
    assert resp.status_code == 200

    async with client.session_factory() as session:
        url = await session.get(AppSetting, "telegram_webhook_url")
        assert url is not None
        assert url.value == "https://example.org/api/v1/telegram/webhook"
