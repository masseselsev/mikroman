import pytest

from backend.app.schemas.router import RouterConfig
from backend.app.services.guards import WriteGuardViolation
from backend.app.services.routeros.client import RouterOSClient


@pytest.mark.asyncio
async def test_kill_connection_refuses_immune_targets():
    cfg = RouterConfig(host="192.168.88.1", username="admin", password="x")
    client = RouterOSClient(cfg)

    # Targeting the router itself as destination
    with pytest.raises(WriteGuardViolation) as exc:
        await client.remove_firewall_connection(
            connection_id="*1",
            src_ip="192.168.88.100",
            dst_ip="192.168.88.1"
        )
    assert "WriteGuard" in str(exc.value)

    # Targeting loopback as source
    with pytest.raises(WriteGuardViolation) as exc:
        await client.remove_firewall_connection(
            connection_id="*2",
            src_ip="127.0.0.1",
            dst_ip="1.1.1.1"
        )
    assert "WriteGuard" in str(exc.value)

    # Targeting wildcard as destination
    with pytest.raises(WriteGuardViolation) as exc:
        await client.remove_firewall_connection(
            connection_id="*3",
            src_ip="192.168.88.50",
            dst_ip="0.0.0.0"
        )
    assert "WriteGuard" in str(exc.value)


@pytest.mark.asyncio
async def test_connections_mixin_methods_exist():
    cfg = RouterConfig(host="192.168.88.1", username="admin", password="x")
    client = RouterOSClient(cfg)
    assert hasattr(client, "get_active_connections")
    assert hasattr(client, "get_dns_cache_entries")
    assert hasattr(client, "remove_firewall_connection")

