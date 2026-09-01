import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, List, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.db.models import Router
from backend.app.db.session import AsyncSessionLocal
from backend.app.schemas.router import (
    RouterProvisionSslRequest,
    RouterProvisionSslResponse,
    RouterTestConnectionRequest,
    RouterTestConnectionResponse,
)
from backend.app.services.routeros import RouterOSClient
from backend.app.services.routeros_compat import (
    MINIMUM_VERSION,
    format_version,
    log_compatibility,
)

logger = logging.getLogger("mikroman.router_manager")


class NoRouterConfiguredError(RuntimeError):
    """Raised when an operation needs a router and none has been set up."""


class OfflineRouterOSClient(RouterOSClient):
    """Stand-in used when no router is configured. Never opens a connection.

    Endpoints like "create user" or "set a device limit" are database
    operations with a best-effort router sync attached, and they have always
    been expected to succeed before a router exists. They previously got a
    client built from the settings *defaults* - user ``admin`` with an empty
    password - whose calls failed at the router and were swallowed upstream.

    The observable result was a burst of ``login failure for user admin ... via
    rest-api`` in the router's log on every such request: indistinguishable from
    a brute-force attempt, and enough to trip an anti-bruteforce rule and get
    the app's own address blacklisted.

    This keeps the identical calling contract - every router call raises and is
    swallowed exactly as before - while sending nothing over the network.
    """

    def __init__(self) -> None:
        super().__init__(host="", username="", password="")

    @asynccontextmanager
    async def _get_client(self) -> AsyncIterator[httpx.AsyncClient]:
        raise NoRouterConfiguredError(
            "No RouterOS router is configured. Add one in Settings, or supply "
            "ROUTEROS_HOST and ROUTEROS_PASSWORD in the environment."
        )
        yield  # pragma: no cover - unreachable, satisfies the generator contract



class RouterManager:
    """Manages dynamic connections to multiple MikroTik RouterOS devices."""

    def __init__(self):
        self._clients: Dict[int, RouterOSClient] = {}
        # Connection parameters each cached client was built from, so a router
        # edited in Settings is noticed and its stale client retired.
        self._fingerprints: Dict[int, tuple] = {}

    def _create_client_from_model(self, router: Router) -> RouterOSClient:
        return RouterOSClient(
            host=router.host,
            port=router.port,
            use_ssl=router.use_ssl,
            ssl_verify=router.ssl_verify,
            username=router.username,
            password=router.password,
            timeout=5.0
        )

    @staticmethod
    def _fingerprint(router: Router) -> tuple:
        """Everything about a router that changes how we connect to it."""
        return (
            router.host,
            router.port,
            router.use_ssl,
            router.ssl_verify,
            router.username,
            router.password,
        )

    async def get_client(
        self,
        router_id: Optional[int] = None,
        session: Optional[AsyncSession] = None
    ) -> Optional[RouterOSClient]:
        """Get an active RouterOSClient instance for a router ID or the default active router.

        The returned client owns a keep-alive connection pool, so it has to be
        the *same* object across calls. An earlier version consulted the cache
        only when an explicit ``router_id`` was passed:

            if router_id and router_id in self._clients:

        Every call site in the application asks for the default router and
        passes no id, so that condition was never true. The cache was written on
        every call and read on none: each request built a fresh client, opened a
        fresh TCP connection (a fresh TLS handshake, over HTTPS), and abandoned
        the previous pool without closing it. Keep-alive - measured as the
        difference between 5% and 12-27% router CPU under load - was never
        actually in effect.

        Resolving the default router still costs one indexed query per call,
        which is what lets an edit in Settings take effect immediately; only the
        client itself is reused, and only while its connection parameters match.
        """
        # Fetch from DB
        should_close_session = False
        if session is None:
            session = AsyncSessionLocal()
            should_close_session = True

        try:
            if router_id:
                stmt = select(Router).where(Router.id == router_id, Router.is_active == True) # noqa: E712
            else:
                # Get default or first active router
                stmt = select(Router).where(Router.is_active == True).order_by(Router.is_default.desc(), Router.id.asc()) # noqa: E712

            result = await session.execute(stmt)
            router = result.scalars().first()

            if not router:
                return None

            fingerprint = self._fingerprint(router)
            cached = self._clients.get(router.id)
            if cached is not None and self._fingerprints.get(router.id) == fingerprint:
                return cached

            # Either nothing cached, or the router was reconfigured. Retire the
            # old pool rather than leaking its sockets.
            if cached is not None:
                await self._close_quietly(cached)

            client = self._create_client_from_model(router)
            self._clients[router.id] = client
            self._fingerprints[router.id] = fingerprint
            return client
        finally:
            if should_close_session:
                await session.close()

    @staticmethod
    async def _close_quietly(client: RouterOSClient) -> None:
        """Close a client, ignoring failures - it is being discarded anyway."""
        try:
            await client.aclose()
        except Exception as e:
            logger.debug(f"Ignoring error while closing a retired router client: {e}")

    def env_fallback_client(self) -> Optional[RouterOSClient]:
        """Client built from environment credentials, or None if none were set.

        The documented `.env` deployment path configures the router entirely
        through the environment, so that route has to keep working. What must
        not happen is constructing a client from the *defaults* - user `admin`
        with an empty password - and firing it at the router.

        Doing so produced a burst of `login failure for user admin via rest-api`
        in the router's log every time an API request arrived with no router
        configured. That is indistinguishable from a brute-force attempt: it
        pollutes the log, and on a router with anti-bruteforce rules it can get
        the app's own address blacklisted. An unset password means "no
        credentials were supplied", not "try an empty one".
        """
        if not settings.ROUTEROS_PASSWORD:
            return None
        return RouterOSClient()

    async def require_client(
        self,
        router_id: Optional[int] = None,
        session: Optional[AsyncSession] = None
    ) -> RouterOSClient:
        """Client for the active router, or an offline stand-in if none exists.

        Never returns a client that would authenticate against the router with
        credentials nobody supplied, so a missing router can never show up in
        the router's log as a failed login.
        """
        client = await self.get_client(router_id=router_id, session=session)
        if client is not None:
            return client

        fallback = self.env_fallback_client()
        if fallback is not None:
            return fallback

        # Never a client that would authenticate as admin with an empty
        # password. Callers that genuinely need the router see the failure on
        # their first call; callers doing database work with a best-effort sync
        # carry on as they always have.
        return OfflineRouterOSClient()

    async def remove_client(self, router_id: int) -> None:
        """Close and remove a client from the active cache."""
        self._fingerprints.pop(router_id, None)
        client = self._clients.pop(router_id, None)
        if client:
            await client.aclose()

    async def get_default_or_first_router(self, session: Optional[AsyncSession] = None) -> Optional[Router]:
        """Fetch the default or first active router model from database."""
        should_close = False
        if session is None:
            session = AsyncSessionLocal()
            should_close = True

        try:
            stmt = select(Router).where(Router.is_active == True).order_by(Router.is_default.desc(), Router.id.asc()) # noqa: E712
            result = await session.execute(stmt)
            return result.scalars().first()
        finally:
            if should_close:
                await session.close()

    async def get_active_router(self, session: Optional[AsyncSession] = None) -> Optional[Router]:
        """Fetch the active router model from database."""
        return await self.get_default_or_first_router(session)

    async def get_all_active_routers(self, session: Optional[AsyncSession] = None) -> List[Router]:
        """Fetch all active router records from database."""
        should_close = False
        if session is None:
            session = AsyncSessionLocal()
            should_close = True

        try:
            stmt = select(Router).where(Router.is_active == True).order_by(Router.is_default.desc(), Router.id.asc()) # noqa: E712
            result = await session.execute(stmt)
            return list(result.scalars().all())
        finally:
            if should_close:
                await session.close()

    async def test_connection(self, req: RouterTestConnectionRequest) -> RouterTestConnectionResponse:
        """
        Test connection parameters against a MikroTik router before saving.
        Includes smart auto-probing for HTTP/HTTPS fallback and SSL status inspection.
        """
        client = RouterOSClient(
            host=req.host,
            port=req.port,
            use_ssl=req.use_ssl,
            ssl_verify=req.ssl_verify,
            username=req.username,
            password=req.password,
            timeout=5.0
        )
        try:
            res = await client.get_system_resource()
            ssl_info = await client.check_ssl_status()

            # Setup is the moment a version problem is cheapest to act on, so
            # the check runs here and the verdict travels with the response.
            compat = log_compatibility(res.version)
            message = "Connection successful"
            if not compat.supported:
                message = (
                    f"Connected, but RouterOS {compat.version_text} is below the "
                    f"minimum {format_version(MINIMUM_VERSION)} this app requires."
                )
            elif compat.degraded:
                message = (
                    f"Connection successful. Some features need a newer RouterOS: "
                    f"{'; '.join(compat.degraded)}."
                )

            return RouterTestConnectionResponse(
                success=True,
                ros_version=res.version,
                board_name=res.board_name,
                cpu_load=res.cpu_load,
                uptime=res.uptime,
                ssl_status=ssl_info,
                message=message
            )
        except Exception as primary_err:
            logger.warning(f"Initial connection test failed for {req.host}:{req.port} (ssl={req.use_ssl}) - {primary_err}")

            # Smart fallback probe:
            # 1. If HTTPS (443) failed, test HTTP (80)
            if req.use_ssl or req.port == 443:
                try:
                    fallback_client = RouterOSClient(
                        host=req.host,
                        port=80,
                        use_ssl=False,
                        username=req.username,
                        password=req.password,
                        timeout=3.0
                    )
                    res = await fallback_client.get_system_resource()
                    ssl_info = await fallback_client.check_ssl_status()
                    await fallback_client.aclose()

                    return RouterTestConnectionResponse(
                        success=False,
                        ros_version=res.version,
                        board_name=res.board_name,
                        suggested_port=80,
                        suggested_ssl=False,
                        ssl_status=ssl_info,
                        message=f"HTTPS (port {req.port}) is not enabled on router, but HTTP (port 80) connected successfully! You can connect with HTTP and use 1-click Auto-SSL provisioning."
                    )
                except Exception:
                    pass

            # 2. If port was classic binary API (8728/8729), probe REST ports 80/443
            if req.port in (8728, 8729):
                for p, s in [(80, False), (443, True)]:
                    try:
                        probe_client = RouterOSClient(
                            host=req.host,
                            port=p,
                            use_ssl=s,
                            username=req.username,
                            password=req.password,
                            timeout=3.0
                        )
                        res = await probe_client.get_system_resource()
                        await probe_client.aclose()
                        return RouterTestConnectionResponse(
                            success=False,
                            ros_version=res.version,
                            board_name=res.board_name,
                            suggested_port=p,
                            suggested_ssl=s,
                            message=f"Port {req.port} is the classic binary API. MikroMan uses the REST API, which is available on port {p}!"
                        )
                    except Exception:
                        pass

            return RouterTestConnectionResponse(
                success=False,
                message=f"Failed to connect: {str(primary_err)}"
            )
        finally:
            await client.aclose()

    async def provision_ssl_for_router(
        self,
        router_id: int,
        req: RouterProvisionSslRequest,
        session: AsyncSession
    ) -> RouterProvisionSslResponse:
        """
        Generate self-signed certificate and enable www-ssl on the specified router,
        then update the router database record to use port 443 with HTTPS.
        """
        router = await session.get(Router, router_id)
        if not router:
            return RouterProvisionSslResponse(
                success=False,
                message="Router not found",
                port=req.port
            )

        client = await self.get_client(router_id, session=session)
        if not client:
            client = self._create_client_from_model(router)

        prov_result = await client.provision_ssl(common_name=req.common_name, port=req.port)
        if not prov_result.get("success"):
            return RouterProvisionSslResponse(
                success=False,
                message=prov_result.get("message", "Failed to provision SSL"),
                port=req.port
            )

        # Update Router record in DB to HTTPS
        router.port = req.port
        router.use_ssl = True
        router.ssl_verify = False
        await session.commit()
        await session.refresh(router)

        # Refresh cached client
        await self.remove_client(router_id)

        return RouterProvisionSslResponse(
            success=True,
            certificate=prov_result.get("certificate"),
            port=req.port,
            message="SSL certificate successfully provisioned on MikroTik and connection upgraded to HTTPS (port 443)."
        )

    async def aclose(self) -> None:
        """Close all cached client connections."""
        for client in self._clients.values():
            await self._close_quietly(client)
        self._clients.clear()
        self._fingerprints.clear()


router_manager = RouterManager()
