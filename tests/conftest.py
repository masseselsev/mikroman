"""Shared pytest fixtures.

``vendor_service`` is a module-level singleton whose OUI cache persists for the
whole test session. Any test that triggers a MAC lookup therefore leaks a cached
vendor into every later test, which made results depend on file ordering. The
cache is snapshotted and restored around each test so they stay independent.
"""
import pytest

from backend.app.services.vendor_lookup import vendor_service


@pytest.fixture(autouse=True)
def isolate_vendor_cache():
    """Restore the shared OUI cache after every test."""
    snapshot = dict(vendor_service._cache)
    yield
    vendor_service._cache.clear()
    vendor_service._cache.update(snapshot)
