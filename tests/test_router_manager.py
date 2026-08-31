import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base, Router
from backend.app.schemas.router import RouterTestConnectionRequest
from backend.app.services.router_manager import (
    NoRouterConfiguredError,
    OfflineRouterOSClient,
    RouterManager,
    router_manager,
)


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


class TestNoRouterConfiguredNeverTouchesTheRouter:
    """A missing router must never become a failed login in the router's log.

    Before this, an API request arriving with no router configured built a
    client from the settings defaults - user "admin" with an empty password -
    and sent it at ROUTEROS_HOST. Every such request produced
    "login failure for user admin ... via rest-api" on the router: noise that is
    indistinguishable from a brute-force attempt and can trip an anti-bruteforce
    rule against the app's own address.
    """

    def test_settings_default_user_is_not_a_usable_credential(self):
        # The guard exists precisely because the defaults look plausible.
        from backend.app.core.config import settings
        assert settings.ROUTEROS_USER == "admin"
        assert settings.ROUTEROS_PASSWORD == "", (
            "an empty default password is what made the fallback dangerous; "
            "if this ever gains a value, revisit env_fallback_client()"
        )

    def test_env_fallback_is_withheld_when_no_password_was_supplied(self, monkeypatch):
        from backend.app.core.config import settings
        monkeypatch.setattr(settings, "ROUTEROS_PASSWORD", "")
        assert router_manager.env_fallback_client() is None

    def test_env_fallback_is_used_when_credentials_were_supplied(self, monkeypatch):
        # The documented .env deployment path must keep working.
        from backend.app.core.config import settings
        monkeypatch.setattr(settings, "ROUTEROS_PASSWORD", "a-real-password")
        client = router_manager.env_fallback_client()
        assert client is not None
        assert not isinstance(client, OfflineRouterOSClient)

    @pytest.mark.asyncio
    async def test_offline_client_raises_locally_instead_of_connecting(self):
        client = OfflineRouterOSClient()
        with pytest.raises(NoRouterConfiguredError):
            await client.get_system_resource()

    @pytest.mark.asyncio
    async def test_offline_client_opens_no_connection(self):
        # The failure must happen before any socket is created, or the router
        # still sees the attempt.
        client = OfflineRouterOSClient()
        with pytest.raises(NoRouterConfiguredError):
            async with client._get_client():
                pass
        assert client._client is None
