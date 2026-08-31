"""An unreachable router must fail fast, not wait out the timeout every time.

Measured against the running app with the router off the network: `/routers`
took 4.65s, `/users` 4.93s, `/system/status` 4.91s and `/system/interfaces`
4.96s, while endpoints that touch only the database answered in under a
millisecond. Every one of those was the RouterOS connect timeout being paid
again, on every request and every poll tick, for an answer already known.

The breaker records the failure once and short-circuits until a short cooldown
expires. The risk it introduces is the opposite failure - suppressing a router
that has come back - so most of these tests are about when the circuit must
*not* be open.
"""

import httpx
import pytest
import respx

from backend.app.core.config import Settings
from backend.app.services.routeros import (
    UNREACHABLE_COOLDOWN_SECONDS,
    RouterOSClient,
    RouterUnreachableError,
)


def make_client() -> RouterOSClient:
    return RouterOSClient(Settings(
        ROUTEROS_HOST="192.0.2.1",
        ROUTEROS_PORT=443,
        ROUTEROS_USE_SSL=True,
        ROUTEROS_SSL_VERIFY=False,
        ROUTEROS_USER="mikroman",
        ROUTEROS_PASSWORD="secret",
    ))


class TestOpeningTheCircuit:
    @pytest.mark.asyncio
    async def test_a_connection_failure_suppresses_the_next_attempt(self):
        client = make_client()
        with respx.mock:
            respx.get("https://192.0.2.1/rest/system/resource").mock(
                side_effect=httpx.ConnectTimeout("timed out")
            )
            with pytest.raises(Exception):
                await client.get_system_resource()

        assert client.is_unreachable

        # The second call must not reach the network at all - no mock is
        # installed here, so anything that tried would fail differently.
        with pytest.raises(RouterUnreachableError):
            async with client._get_client():
                pass

    @pytest.mark.asyncio
    async def test_a_refused_connection_opens_it_too(self):
        client = make_client()
        with respx.mock:
            respx.get("https://192.0.2.1/rest/system/resource").mock(
                side_effect=httpx.ConnectError("refused")
            )
            with pytest.raises(Exception):
                await client.get_system_resource()

        assert client.is_unreachable

    @pytest.mark.asyncio
    async def test_the_error_is_a_connection_error(self):
        """Call sites already tolerate ConnectionError, so behaviour elsewhere
        is unchanged - they see a failure of the same kind, sooner."""
        assert issubclass(RouterUnreachableError, ConnectionError)


class TestWhatMustNotOpenIt:
    """Anything that proves the router answered means it is reachable."""

    @pytest.mark.asyncio
    async def test_an_authentication_failure_does_not(self):
        # Wrong credentials are a configuration problem, not an unreachable
        # router - and suppressing requests here would make the operator's own
        # correction appear not to take effect.
        client = make_client()
        with respx.mock:
            respx.get("https://192.0.2.1/rest/system/resource").mock(
                return_value=httpx.Response(401, json={"error": 401})
            )
            try:
                await client.get_system_resource()
            except Exception:
                pass

        assert not client.is_unreachable

    @pytest.mark.asyncio
    async def test_a_server_error_does_not(self):
        client = make_client()
        with respx.mock:
            respx.get("https://192.0.2.1/rest/system/resource").mock(
                return_value=httpx.Response(500, text="boom")
            )
            try:
                await client.get_system_resource()
            except Exception:
                pass

        assert not client.is_unreachable

    @pytest.mark.asyncio
    async def test_a_fresh_client_starts_closed(self):
        assert not make_client().is_unreachable


class TestRecovery:
    @pytest.mark.asyncio
    async def test_the_circuit_closes_once_the_cooldown_expires(self):
        client = make_client()
        with respx.mock:
            respx.get("https://192.0.2.1/rest/system/resource").mock(
                side_effect=httpx.ConnectTimeout("timed out")
            )
            with pytest.raises(Exception):
                await client.get_system_resource()

        assert client.is_unreachable
        # Rewind the deadline rather than sleeping through it.
        client._unreachable_until -= UNREACHABLE_COOLDOWN_SECONDS + 1
        assert not client.is_unreachable

        async with client._get_client() as pooled:
            assert pooled is not None

    @pytest.mark.asyncio
    async def test_a_successful_request_closes_it_immediately(self):
        """A router that answers must be trusted at once.

        This is what makes correcting the connection details in Settings take
        effect straight away instead of after a cooldown.
        """
        client = make_client()
        client._unreachable_until = 0.0

        with respx.mock:
            respx.get("https://192.0.2.1/rest/system/resource").mock(
                return_value=httpx.Response(200, json={
                    "board-name": "hAP be3", "version": "7.25", "cpu-load": "3",
                    "free-memory": "100", "total-memory": "200", "uptime": "1d",
                })
            )
            await client.get_system_resource()

        assert not client.is_unreachable

    @pytest.mark.asyncio
    async def test_the_cooldown_is_short_enough_to_notice_a_router_returning(self):
        # The dashboard polls every few seconds; a long cooldown would leave it
        # blank well after the router was healthy again.
        assert UNREACHABLE_COOLDOWN_SECONDS <= 30
