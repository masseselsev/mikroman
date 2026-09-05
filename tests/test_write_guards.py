import pytest

from backend.app.services.guards import (
    WriteGuardViolation,
    guard_foreign_resources,
    guard_immune_targets,
    guard_queue_invariants,
    parse_bps,
)


def test_parse_bps():
    assert parse_bps("0") == 0
    assert parse_bps("5M") == 5_000_000
    assert parse_bps("100k") == 100_000
    assert parse_bps("1G") == 1_000_000_000
    assert parse_bps("500") == 500
    with pytest.raises(ValueError):
        parse_bps("invalid")

def test_guard_immune_targets():
    immune = {"192.168.88.1", "192.168.88.250"}

    # Allowed regular targets
    guard_immune_targets("192.168.88.45", immune, action="block")
    guard_immune_targets("192.168.88.45", immune, action="queue")

    # Refused immune targets
    with pytest.raises(WriteGuardViolation) as exc:
        guard_immune_targets("127.0.0.1", immune, action="block")
    assert "loopback" in str(exc.value).lower()

    with pytest.raises(WriteGuardViolation) as exc:
        guard_immune_targets("0.0.0.0/0", immune, action="queue")
    assert "wildcard" in str(exc.value).lower()

    with pytest.raises(WriteGuardViolation) as exc:
        guard_immune_targets("192.168.88.1", immune, action="block")
    assert "protected" in str(exc.value).lower()

def test_guard_foreign_resources():
    # Managed resource passes
    guard_foreign_resources("mikroman:managed:user_1", action="delete", resource_type="queue")
    guard_foreign_resources("mikroman:paused:Alex", action="delete", resource_type="address-list")

    # Foreign resource refused
    with pytest.raises(WriteGuardViolation) as exc:
        guard_foreign_resources("Admin Winbox rule", action="delete", resource_type="queue")
    assert "foreign" in str(exc.value).lower()

    with pytest.raises(WriteGuardViolation):
        guard_foreign_resources(None, action="delete", resource_type="queue")

def test_guard_queue_invariants():
    # Valid queue
    guard_queue_invariants(target="192.168.88.45", max_limit="10M/20M", limit_at="2M/5M", parent="none", name="dev_1")
    guard_queue_invariants(target="192.168.88.45", max_limit="0/0", limit_at="0/0")

    # Invalid speed format
    with pytest.raises(WriteGuardViolation) as exc:
        guard_queue_invariants(target="192.168.88.45", max_limit="invalid_rate")
    assert "format" in str(exc.value).lower()

    # limit_at exceeds max_limit
    with pytest.raises(WriteGuardViolation) as exc:
        guard_queue_invariants(target="192.168.88.45", max_limit="5M/5M", limit_at="10M/2M")
    assert "cannot exceed" in str(exc.value).lower()

    # Circular parent
    with pytest.raises(WriteGuardViolation) as exc:
        guard_queue_invariants(target="192.168.88.45", max_limit="10M/10M", parent="dev_1", name="dev_1")
    assert "circular" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_client_refuses_blocking_immune_host():
    from backend.app.schemas.router import RouterConfig
    from backend.app.services.routeros.client import RouterOSClient

    cfg = RouterConfig(host="192.168.88.1", username="admin", password="x")
    client = RouterOSClient(cfg)
    client._immune_ips = {"192.168.88.1", "192.168.88.24"}

    # Attempting to add router IP to mikroman_blocked must raise WriteGuardViolation
    with pytest.raises(WriteGuardViolation) as exc:
        await client.add_to_address_list(address="192.168.88.1", list_name="mikroman_blocked")
    assert "protected management" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_client_refuses_deleting_foreign_queue():
    from backend.app.schemas.router import RouterConfig
    from backend.app.services.routeros.client import RouterOSClient

    cfg = RouterConfig(host="192.168.88.1", username="admin", password="x")
    client = RouterOSClient(cfg)

    # Attempting to delete queue without mikroman: comment
    with pytest.raises(WriteGuardViolation) as exc:
        await client.delete_simple_queue(queue_id="*A", comment="Manual-Queue")
    assert "foreign" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_client_refuses_throttling_immune_host():
    from backend.app.schemas.router import RouterConfig
    from backend.app.services.routeros.client import RouterOSClient

    cfg = RouterConfig(host="192.168.88.1", username="admin", password="x")
    client = RouterOSClient(cfg)
    client._immune_ips = {"192.168.88.1"}

    # Attempting to throttle immune host in create_simple_queue
    with pytest.raises(WriteGuardViolation) as exc:
        await client.create_simple_queue(name="q1", target="192.168.88.1", max_limit="10M/10M")
    assert "protected management" in str(exc.value).lower()

    # Attempting to throttle immune host in update_simple_queue
    with pytest.raises(WriteGuardViolation) as exc:
        await client.update_simple_queue(queue_id="*1", target="192.168.88.1", max_limit="5M/5M")
    assert "protected management" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_client_refuses_foreign_firewall_deletion():
    from backend.app.schemas.router import RouterConfig
    from backend.app.services.routeros.client import RouterOSClient

    cfg = RouterConfig(host="192.168.88.1", username="admin", password="x")
    client = RouterOSClient(cfg)

    with pytest.raises(WriteGuardViolation) as exc:
        await client.remove_from_address_list(entry_id="*1", comment="user_custom_rule")
    assert "foreign" in str(exc.value).lower()

    with pytest.raises(WriteGuardViolation) as exc:
        await client.delete_firewall_filter_rule(rule_id="*1", comment="winbox_filter")
    assert "foreign" in str(exc.value).lower()

    with pytest.raises(WriteGuardViolation) as exc:
        await client.delete_firewall_raw_rule(rule_id="*1", comment="winbox_raw")
    assert "foreign" in str(exc.value).lower()


def test_client_get_immune_ips_resolution():
    from backend.app.schemas.router import RouterConfig
    from backend.app.services.routeros.client import RouterOSClient

    cfg = RouterConfig(host="192.168.88.1", username="admin", password="x")
    client = RouterOSClient(cfg)
    immune = client.get_immune_ips()
    assert "192.168.88.1" in immune


@pytest.mark.asyncio
async def test_client_queue_invariants_validation():
    from backend.app.schemas.router import RouterConfig
    from backend.app.services.routeros.client import RouterOSClient

    cfg = RouterConfig(host="192.168.88.1", username="admin", password="x")
    client = RouterOSClient(cfg)

    # Invalid rate format
    with pytest.raises(WriteGuardViolation) as exc:
        await client.create_simple_queue(name="q1", target="192.168.88.50", max_limit="bad_format")
    assert "format" in str(exc.value).lower()

    # limit_at > max_limit
    with pytest.raises(WriteGuardViolation) as exc:
        await client.create_simple_queue(
            name="q1",
            target="192.168.88.50",
            max_limit="5M/5M",
            limit_at="10M/10M",
        )
    assert "cannot exceed" in str(exc.value).lower()

    # Circular parentage
    with pytest.raises(WriteGuardViolation) as exc:
        await client.create_simple_queue(
            name="q1",
            target="192.168.88.50",
            max_limit="5M/5M",
            parent="q1",
        )
    assert "circular" in str(exc.value).lower()

    # Update with limit_at > max_limit
    with pytest.raises(WriteGuardViolation) as exc:
        await client.update_simple_queue(
            queue_id="*1",
            max_limit="2M/2M",
            limit_at="5M/5M",
        )
    assert "cannot exceed" in str(exc.value).lower()




def test_a_limit_violation_is_reported_once_not_nested():
    """`WriteGuardViolation` subclasses `ValueError`.

    So a violation raised inside a `try` whose `except ValueError` wraps parse
    failures got caught by that handler and re-wrapped, producing
    "[WriteGuard] ... Refused write for u1: [WriteGuard] ... Refused write for
    u1: Upload limit-at ..." and a `reason` that was itself a whole message.
    Only the parse belongs inside the try.
    """
    with pytest.raises(WriteGuardViolation) as exc:
        guard_queue_invariants(target="u1", max_limit="5M/5M", limit_at="10M/10M")

    err = exc.value
    assert str(err).count("[WriteGuard]") == 1
    assert err.guard_name == "QueueInvariantGuard"
    assert err.target == "u1"
    assert err.reason == "Upload limit-at (10000000 bps) cannot exceed max-limit (5000000 bps)"


def test_an_unparseable_limit_at_still_reports_the_parse_error():
    with pytest.raises(WriteGuardViolation) as exc:
        guard_queue_invariants(target="u1", max_limit="5M/5M", limit_at="fast")

    assert "Invalid rate pair format" in exc.value.reason
    assert str(exc.value).count("[WriteGuard]") == 1


def test_configured_exemptions_widen_the_guard_and_never_narrow_it():
    """`immune_ips` from Settings adds to the built-ins; it cannot replace them.

    A guard that could be narrowed by configuration is not a guard - an operator
    listing one jump host must not thereby make the router's own address
    blockable.
    """
    from backend.app.schemas.router import RouterConfig
    from backend.app.services.routeros.client import RouterOSClient

    client = RouterOSClient(RouterConfig(host="192.168.88.1", username="admin", password="x"))
    client._immune_ips = {"10.10.0.5"}

    immune = client.get_immune_ips()
    assert "10.10.0.5" in immune       # the operator's addition
    assert "192.168.88.1" in immune    # and still the router itself
