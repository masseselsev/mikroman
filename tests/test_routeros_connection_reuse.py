"""The RouterOS client must hold one pooled connection, not one per request.

Every call previously built a fresh ``httpx.AsyncClient``, so each of the four
requests the telemetry loop makes every second opened a new TCP connection and
completed a full TLS handshake. Measured on a live hAP be^3, that polling
quadrupled idle router CPU (median 8% with the app running against 2% with it
stopped). Reusing one pooled, keep-alive client removes the handshakes.
"""
import httpx
import pytest
import respx

from backend.app.core.config import Settings
from backend.app.services.routeros import RouterOSClient


@pytest.fixture
def client():
    return RouterOSClient(Settings(
        ROUTEROS_HOST="192.168.88.1",
        ROUTEROS_PORT=443,
        ROUTEROS_USE_SSL=True,
        ROUTEROS_SSL_VERIFY=False,
        ROUTEROS_USER="admin",
        ROUTEROS_PASSWORD="password",
    ))


@pytest.mark.asyncio
async def test_same_transport_is_reused_across_requests(client):
    """Successive calls must share one AsyncClient instance."""
    async with client._get_client() as first:
        pass
    async with client._get_client() as second:
        pass
    assert first is second, "a new HTTP client per request means a TLS handshake per request"


@pytest.mark.asyncio
async def test_client_survives_use_and_is_not_closed_between_calls(client):
    """Leaving the context manager must not close the shared client."""
    with respx.mock(base_url="https://192.168.88.1:443/rest") as mock:
        mock.get("/system/resource").respond(200, json={
            "board-name": "hAP", "version": "7.25", "cpu-load": "5",
            "free-memory": "1000", "total-memory": "2000", "uptime": "1d",
        })
        await client.get_system_resource()
        async with client._get_client() as shared:
            assert not shared.is_closed
        # A second call must still work on the same client.
        await client.get_system_resource()
        assert not (await _peek(client)).is_closed


async def _peek(client) -> httpx.AsyncClient:
    async with client._get_client() as c:
        return c


@pytest.mark.asyncio
async def test_aclose_releases_the_pooled_client(client):
    async with client._get_client() as before:
        pass
    await client.aclose()
    assert before.is_closed

    # A later call must transparently rebuild the pool rather than fail.
    async with client._get_client() as after:
        assert not after.is_closed
    assert after is not before
