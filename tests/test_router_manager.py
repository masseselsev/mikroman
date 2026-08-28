import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base, Router
from backend.app.schemas.router import RouterTestConnectionRequest
from backend.app.services.router_manager import RouterManager


@pytest.mark.asyncio
async def test_router_manager_test_connection_success():
    manager = RouterManager()

    with respx.mock(base_url="https://192.168.88.1:443/rest") as respx_mock:
        respx_mock.get("/system/resource").mock(
            return_value=httpx.Response(
                200,
                json={"version": "7.24.1", "board-name": "RB5009UG+S+IN", "cpu-load": 5, "uptime": "1d 04:20:00"}
            )
        )

        req = RouterTestConnectionRequest(
            host="192.168.88.1",
            port=443,
            use_ssl=True,
            username="admin",
            password="secretpassword"
        )
        res = await manager.test_connection(req)
        assert res.success is True
        assert res.ros_version == "7.24.1"
        assert res.board_name == "RB5009UG+S+IN"
        assert res.cpu_load == 5


@pytest.mark.asyncio
async def test_router_manager_test_connection_failure():
    manager = RouterManager()

    with respx.mock(base_url="https://192.168.88.2:443/rest") as respx_mock:
        respx_mock.get("/system/resource").mock(
            return_value=httpx.Response(401, text="Unauthorized")
        )

        req = RouterTestConnectionRequest(
            host="192.168.88.2",
            port=443,
            use_ssl=True,
            username="admin",
            password="wrongpassword"
        )
        res = await manager.test_connection(req)
        assert res.success is False
        assert "401" in (res.message or "") or "Failed" in (res.message or "")


@pytest.mark.asyncio
async def test_router_manager_get_client_from_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        manager = RouterManager()

        # Insert test router into DB
        router = Router(
            name="Main RB5009",
            host="192.168.88.1",
            port=443,
            use_ssl=True,
            ssl_verify=False,
            username="admin",
            password="secretpassword",
            is_active=True,
            is_default=True
        )
        session.add(router)
        await session.commit()

        client = await manager.get_client(router_id=router.id, session=session)
        assert client is not None
        assert client.host == "192.168.88.1"
        assert client.port == 443
        assert client.username == "admin"

        # Default router query
        default_client = await manager.get_client(router_id=None, session=session)
        assert default_client is not None
        assert default_client.host == "192.168.88.1"

        await manager.aclose()

    await engine.dispose()
