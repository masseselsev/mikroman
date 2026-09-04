import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base, Router
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services.router_manager import router_manager


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

    # Update User (including device assignment)
    patch_resp = await api_client.patch(f"/api/v1/users/{user_id}", json={
        "speed_limit": "100M",
        "device_macs": []
    })
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
async def test_create_user_survives_an_unreachable_router(api_client: AsyncClient):
    """The DB row is written first; a router that is down must not 500 the create.

    Regression: adding a user on a remote router that was unreachable raised
    500 from the synchronous Simple Queue creation, even though the user had
    already been committed.
    """
    await router_manager.aclose()

    gen = app.dependency_overrides[get_db]()
    session = await gen.__anext__()
    try:
        session.add(Router(
            id=1, name="Marusyan", host="192.0.2.77", port=80, use_ssl=False,
            username="admin", password="x", is_default=True,
        ))
        await session.commit()
    finally:
        await gen.aclose()

    with respx.mock(base_url="http://192.0.2.77:80/rest", assert_all_called=False) as m:
        # Reads work (empty), but creating the queue fails - the router is down.
        m.get("/queue/simple").mock(return_value=httpx.Response(200, json=[]))
        m.get("/ip/firewall/address-list").mock(return_value=httpx.Response(200, json=[]))
        m.put("/queue/simple").mock(side_effect=httpx.ConnectError("down"))

        resp = await api_client.post(
            "/api/v1/users", json={"name": "Mama", "speed_limit": "unlimited", "router_id": 1}
        )

    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["name"] == "Mama"

    listed = await api_client.get("/api/v1/users?router_id=1")
    assert any(u["name"] == "Mama" for u in listed.json()["data"])
    await router_manager.aclose()


@pytest.mark.asyncio
async def test_settings_api(api_client: AsyncClient):
    # Get settings
    resp = await api_client.get("/api/v1/system/settings")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "theme" in data
    assert "lang" in data
    assert "unassigned_device_speed_limit" in data

    # Update settings
    update_resp = await api_client.post("/api/v1/system/settings", json={
        "theme": "dark",
        "lang": "ru",
        "unassigned_device_speed_limit": "5M/5M"
    })
    assert update_resp.status_code == 200
    assert update_resp.json()["data"] is True

    # Verify settings persisted
    verify_resp = await api_client.get("/api/v1/system/settings")
    assert verify_resp.json()["data"]["theme"] == "dark"
    assert verify_resp.json()["data"]["lang"] == "ru"
    assert verify_resp.json()["data"]["unassigned_device_speed_limit"] == "5M/5M"
