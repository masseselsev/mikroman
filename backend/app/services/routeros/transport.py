"""Connection handling for the RouterOS REST client: pooling and the breaker.

Everything in this module is about *reaching* the router, never about what is
asked of it. The domain mixins in the sibling modules assume a working
``self._get_client()`` and nothing else.

Two behaviours live here because they cannot live at the call sites:

* **A pooled keep-alive connection.** Opening a TLS session per request measured
  at 2.4x the router's CPU cost of reusing one.
* **A circuit breaker in the transport.** The client exposes some forty request
  methods and many swallow their own exceptions, so a breaker wrapped around
  callers would never see the failures. Every request passes through the
  transport, so that is the one place that sees them all.
"""
import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import httpx

from backend.app.core.config import Settings
from backend.app.core.config import settings as global_settings
from backend.app.schemas.routeros import RouterBoardInfo

logger = logging.getLogger("mikroman.routeros")

class RouterUnreachableError(ConnectionError):
    """Raised immediately while a router is known to be unreachable.

    A subclass of ConnectionError so that the many call sites which already
    tolerate a connection failure keep behaving exactly as they did.
    """


# How long a failed connection suppresses further attempts. Long enough that a
# dashboard polling every few seconds makes one real attempt rather than dozens,
# short enough that a router coming back is picked up almost immediately.
UNREACHABLE_COOLDOWN_SECONDS = 15.0


class _CircuitBreakerTransport(httpx.AsyncHTTPTransport):
    """Transport that reports connection failures back to its client.

    The bookkeeping lives here rather than around each call because the client
    exposes some forty request methods and many of them catch their own
    exceptions internally - a breaker wrapped around the caller would simply
    never see those failures. Every request passes through the transport, so
    this is the one place that sees them all.
    """

    def __init__(self, owner: "RouterOSTransport", **kwargs):
        super().__init__(**kwargs)
        self._owner = owner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            response = await super().handle_async_request(request)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
            # Only failures to *reach* the host open the circuit. A 401, a 500 or
            # a slow read all prove the router is there and answering.
            self._owner._note_unreachable(e)
            raise
        self._owner._note_reachable()
        return response


class RouterOSTransport:
    """Connection state for a RouterOS REST client: config, pool, breaker.

    Base of :class:`~backend.app.services.routeros.client.RouterOSClient`. Holds
    everything the domain mixins depend on and nothing they do not: connection
    parameters, the pooled ``httpx`` client behind :meth:`_get_client`, and the
    reachability bookkeeping the transport feeds.
    """

    def __init__(
        self,
        config: Optional[Settings] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        use_ssl: Optional[bool] = None,
        ssl_verify: Optional[bool] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: Optional[float] = None
    ):
        self.config = config or global_settings
        self.host = host if host is not None else self.config.ROUTEROS_HOST
        self.port = port if port is not None else self.config.ROUTEROS_PORT
        self.use_ssl = use_ssl if use_ssl is not None else self.config.ROUTEROS_USE_SSL
        self.ssl_verify = ssl_verify if ssl_verify is not None else (self.config.ROUTEROS_SSL_VERIFY if self.use_ssl else False)
        self.username = username if username is not None else self.config.ROUTEROS_USER
        self.password = password if password is not None else self.config.ROUTEROS_PASSWORD
        timeout_val = timeout if timeout is not None else self.config.ROUTEROS_TIMEOUT_SECONDS

        protocol = "https" if self.use_ssl else "http"
        self.base_url = f"{protocol}://{self.host}:{self.port}/rest"
        self.auth = (self.username, self.password)
        self.verify_ssl = self.ssl_verify if self.use_ssl else False
        self.timeout = httpx.Timeout(timeout_val)
        self._client: Optional[httpx.AsyncClient] = None
        # Monotonic deadline before which the router is treated as unreachable
        # without trying. Zero means the circuit is closed.
        self._unreachable_until: float = 0.0
        # `/system/routerboard` is static between reboots (model, serial, SoC),
        # so it is fetched once per client and reused. The client itself is
        # cached per router and retired when its settings change, so this never
        # goes stale in a way that matters.
        self._routerboard: Optional[RouterBoardInfo] = None

    def _note_unreachable(self, error: Exception) -> None:
        """Open the circuit after a failure to reach the router."""
        was_open = self.is_unreachable
        self._unreachable_until = time.monotonic() + UNREACHABLE_COOLDOWN_SECONDS
        if not was_open:
            logger.warning(
                f"RouterOS at {self.host}:{self.port} is unreachable ({type(error).__name__}); "
                f"suppressing further attempts for {UNREACHABLE_COOLDOWN_SECONDS:.0f}s"
            )

    def _note_reachable(self) -> None:
        """Close the circuit: the router answered."""
        if self._unreachable_until:
            logger.info(f"RouterOS at {self.host}:{self.port} is reachable again")
        self._unreachable_until = 0.0

    @property
    def is_unreachable(self) -> bool:
        return self._unreachable_until > time.monotonic()

    def _build_client(self) -> httpx.AsyncClient:
        limits = httpx.Limits(max_keepalive_connections=4, max_connections=8, keepalive_expiry=60.0)
        return httpx.AsyncClient(
            base_url=self.base_url,
            auth=self.auth,
            timeout=self.timeout,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            # Keep-alive connections are the whole point: without them every
            # request repeats the TLS handshake, which is what made the polling
            # loop dominate router CPU.
            limits=limits,
            transport=_CircuitBreakerTransport(
                self, verify=self.verify_ssl, limits=limits, retries=0
            ),
        )

    @asynccontextmanager
    async def _get_client(self) -> AsyncIterator[httpx.AsyncClient]:
        """Yield the pooled HTTP client, creating it on first use.

        Fails fast while the router is known to be unreachable. Without this,
        every endpoint that touches the router waited out the full connect
        timeout on every request: with the router off the network, ``/routers``,
        ``/users``, ``/system/status`` and ``/system/interfaces`` each took
        ~4.9s, so the dashboard sat blank for five seconds on every load and
        every poll tick. One attempt per cooldown is enough to notice the router
        returning; the rest are pointless waiting.

        Deliberately does not close the client on exit: callers use
        ``async with self._get_client() as client`` for every request, and
        closing it there would discard the connection pool - and with it the
        keep-alive that avoids a TLS handshake per request.
        """
        if self.is_unreachable:
            raise RouterUnreachableError(
                f"RouterOS at {self.host}:{self.port} was unreachable moments ago; "
                f"not retrying for another "
                f"{self._unreachable_until - time.monotonic():.0f}s"
            )
        if self._client is None or self._client.is_closed:
            self._client = self._build_client()
        yield self._client

    async def aclose(self) -> None:
        """Release the pooled connections."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None
        self._routerboard = None
