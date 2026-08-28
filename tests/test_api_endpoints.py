import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base
from backend.app.db.session import get_db
from backend.app.main import app


@pytest.fixture
async def api_client():
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
        yield client

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_users_api_crud(api_client: AsyncClient):
    # Create User
    create_resp = await api_client.post("/api/v1/users", json={
        "name": "Maria",
        "speed_limit": "25M/50M",
        "avatar_icon": "user-female",
        "priority": 2
    })
    assert create_resp.status_code == 201
    data = create_resp.json()["data"]
    user_id = data["id"]
    assert data["name"] == "Maria"
    assert data["speed_limit"] == "25M/50M"

    # List Users
    list_resp = await api_client.get("/api/v1/users")
    assert list_resp.status_code == 200
    users = list_resp.json()["data"]
    assert any(u["id"] == user_id for u in users)

    # Update User
    patch_resp = await api_client.patch(f"/api/v1/users/{user_id}", json={"speed_limit": "100M"})
    assert patch_resp.status_code == 200
    assert patch_resp.json()["data"]["speed_limit"] == "100M"

    # Set speed limit via traffic endpoint
    limit_resp = await api_client.post(f"/api/v1/traffic/users/{user_id}/limit", json={"speed_limit": "30M"})
    assert limit_resp.status_code == 200

    # Toggle pause
    pause_resp = await api_client.post(f"/api/v1/traffic/users/{user_id}/pause", json={"is_paused": True})
    assert pause_resp.status_code == 200

    # Delete User
    del_resp = await api_client.delete(f"/api/v1/users/{user_id}")
    assert del_resp.status_code == 200


@pytest.mark.asyncio
async def test_settings_api(api_client: AsyncClient):
    # Get settings
    resp = await api_client.get("/api/v1/system/settings")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "theme" in data
    assert "lang" in data

    # Update settings
    update_resp = await api_client.post("/api/v1/system/settings", json={
        "theme": "dark",
        "lang": "ru"
    })
    assert update_resp.status_code == 200
    assert update_resp.json()["data"] is True

    # Verify settings persisted
    verify_resp = await api_client.get("/api/v1/system/settings")
    assert verify_resp.json()["data"]["theme"] == "dark"
    assert verify_resp.json()["data"]["lang"] == "ru"
