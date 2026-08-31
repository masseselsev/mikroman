"""The test suite must never open a real socket.

This guards a defect that was invisible from inside the suite: it passed, all
199 of it, while quietly making REST calls to the author's live MikroTik. The
router recorded three of them per run as

    login failure for user admin from 192.168.88.233 via rest-api

because the fixtures used the real router's address with placeholder
credentials, and the request paths outside the respx blocks were never mocked.
Nothing in the suite could fail as a result - the calls were made inside
``try/except`` blocks whose whole purpose is to tolerate an unreachable router -
so the only evidence was in the router's own log.

These tests assert the guard is armed, because a guard nobody checks is a guard
that silently stops working.
"""

import httpx
import pytest
import respx

from tests.conftest import OutboundNetworkBlocked


@pytest.mark.asyncio
async def test_an_unmocked_request_is_refused():
    """The exact shape of the original leak: a request nothing mocked."""
    async with httpx.AsyncClient() as client:
        with pytest.raises(OutboundNetworkBlocked):
            await client.get("https://192.0.2.1/rest/system/resource")


@pytest.mark.asyncio
async def test_the_refusal_names_the_host_that_escaped():
    """A blocked call has to say where it was going, or the next person to hit
    this has the same archaeology to do."""
    async with httpx.AsyncClient() as client:
        with pytest.raises(OutboundNetworkBlocked, match=r"192\.0\.2\.1"):
            await client.get("https://192.0.2.1/rest/interface")


def test_synchronous_clients_are_blocked_too():
    # The app is async throughout, but a helper or a library reaching for the
    # sync API would otherwise slip straight past the guard.
    with httpx.Client() as client:
        with pytest.raises(OutboundNetworkBlocked):
            client.get("https://192.0.2.1/rest/system/resource")


@pytest.mark.asyncio
async def test_the_guard_does_not_interfere_with_mocked_requests():
    """The reason the guard sits at the socket backend and not higher.

    An earlier version patched the httpx transport, and a later one the httpcore
    connection pool. Both are layers respx patches for itself, so both blocked
    the very requests respx was there to answer - fifteen tests failed that had
    nothing wrong with them. A mocked request never reaches a socket, so a guard
    on the socket cannot get in its way.
    """
    with respx.mock:
        respx.get("https://192.0.2.1/rest/system/resource").respond(
            200, json={"board-name": "hAP ax3"}
        )
        async with httpx.AsyncClient() as client:
            response = await client.get("https://192.0.2.1/rest/system/resource")

    assert response.status_code == 200
    assert response.json()["board-name"] == "hAP ax3"
