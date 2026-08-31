"""Shared pytest fixtures.

``vendor_service`` is a module-level singleton whose OUI cache persists for the
whole test session. Any test that triggers a MAC lookup therefore leaks a cached
vendor into every later test, which made results depend on file ordering. The
cache is snapshotted and restored around each test so they stay independent.

The second fixture here is a hard guard against the test suite touching the real
network. See ``no_real_network`` for why it exists.
"""
import httpcore._backends.anyio as anyio_backend
import httpcore._backends.sync as sync_backend
import pytest

from backend.app.services.vendor_lookup import vendor_service


@pytest.fixture(autouse=True)
def isolate_vendor_cache():
    """Restore the shared OUI cache after every test."""
    snapshot = dict(vendor_service._cache)
    yield
    vendor_service._cache.clear()
    vendor_service._cache.update(snapshot)


class OutboundNetworkBlocked(RuntimeError):
    """A test tried to open a real connection. See ``no_real_network``."""


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Refuse every HTTP request that is not intercepted by a mock.

    The suite is written against ``respx``, but a mock only covers the requests
    it was told about. Anything else falls through to the real transport, and
    because the fixtures were written using the developer's own router address
    (``192.168.88.1``) with placeholder credentials, "anything else" meant a
    genuine REST call to a genuine MikroTik.

    The router recorded each one as::

        login failure for user admin from 192.168.88.233 via rest-api

    Three per run of ``tests/test_routers_api.py`` - measured, not inferred, by
    counting the router's log before and after. Two effects, both bad: the log
    is polluted with what looks exactly like a brute-force attempt against the
    default account, and a router carrying an anti-bruteforce rule will
    eventually blacklist the development machine.

    The guard deliberately sits at the very bottom of the stack - the socket
    backend - rather than at the transport or the connection pool. Both of
    those are layers respx itself patches, and a guard there either fights
    respx for the same attribute or short-circuits the requests respx was
    supposed to answer. Opening a socket is the one thing a mocked request
    never does, so refusing to open one blocks exactly the real calls and
    nothing else, whatever respx does above it.

    A test that genuinely needs a socket can request ``allow_real_network``.
    """
    def refusal(host, port) -> OutboundNetworkBlocked:
        return OutboundNetworkBlocked(
            f"Test attempted a real network connection to {host}:{port}. Mock it "
            f"with respx, or use the allow_real_network fixture if the connection "
            f"is the point of the test."
        )

    async def refuse_async(self, host, port, *args, **kwargs):
        raise refusal(host, port)

    def refuse_sync(self, host, port, *args, **kwargs):
        raise refusal(host, port)

    for backend, method, replacement in (
        (anyio_backend.AnyIOBackend, "connect_tcp", refuse_async),
        (anyio_backend.AnyIOBackend, "connect_unix_socket", refuse_async),
        (sync_backend.SyncBackend, "connect_tcp", refuse_sync),
        (sync_backend.SyncBackend, "connect_unix_socket", refuse_sync),
    ):
        monkeypatch.setattr(backend, method, replacement, raising=False)


@pytest.fixture
def allow_real_network(monkeypatch):
    """Opt back out of ``no_real_network`` for a test that truly needs a socket.

    Nothing in the suite uses this today. It exists so that adding such a test
    is a deliberate, visible act rather than a silent regression.
    """
    monkeypatch.undo()
