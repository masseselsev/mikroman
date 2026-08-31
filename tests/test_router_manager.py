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


async def _manager_with_router(session_factory, **overrides):
    """A manager and session holding one active default router."""
    session = session_factory()
    router = Router(
        name="Main", host="192.0.2.1", port=443, use_ssl=True, ssl_verify=False,
        username="mikroman", password="secret", is_active=True, is_default=True,
        **overrides,
    )
    session.add(router)
    await session.commit()
    return RouterManager(), session, router


async def _memory_sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest.mark.asyncio
async def test_the_default_router_reuses_one_pooled_client():
    """Object identity is the whole point, so it is asserted directly.

    A client owns a keep-alive connection pool; reusing it is what avoids a TCP
    connection and a TLS handshake per request. The cache used to be consulted
    only when an explicit router_id was passed - which no call site in the
    application does - so it was written on every call and read on none. Every
    request built a fresh client and dropped the previous pool unclosed, and
    keep-alive, worth the difference between 5% and 12-27% router CPU under
    load, was never actually in effect.
    """
    engine, session_factory = await _memory_sessions()
    manager, session, _ = await _manager_with_router(session_factory)

    first = await manager.get_client(session=session)
    second = await manager.get_client(session=session)
    third = await manager.get_client(session=session)

    assert first is second is third

    await manager.aclose()
    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_asking_by_id_and_by_default_yield_the_same_client():
    # Otherwise the same router would be reached through two separate pools.
    engine, session_factory = await _memory_sessions()
    manager, session, router = await _manager_with_router(session_factory)

    by_default = await manager.get_client(session=session)
    by_id = await manager.get_client(router_id=router.id, session=session)

    assert by_default is by_id

    await manager.aclose()
    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_reconfiguring_a_router_retires_its_cached_client():
    """Reuse must not outlive the settings the client was built from.

    Caching by id alone would keep serving a client pointed at the old address
    or authenticating with the old password, and the failure would look like a
    router that stopped responding.
    """
    engine, session_factory = await _memory_sessions()
    manager, session, router = await _manager_with_router(session_factory)

    original = await manager.get_client(session=session)

    router.password = "rotated-credential"
    await session.commit()

    replacement = await manager.get_client(session=session)

    assert replacement is not original
    assert replacement.auth == ("mikroman", "rotated-credential")
    # The retired pool is closed rather than left to leak its sockets.
    assert original._client is None or original._client.is_closed

    await manager.aclose()
    await session.close()
    await engine.dispose()

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
