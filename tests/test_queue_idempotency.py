"""Regression tests for RouterOS Simple Queue synchronisation.

Background: the background sync worker re-issued a PATCH for every managed queue
on every poll tick (~43,000 RouterOS config writes per day), because the values
read back from RouterOS are normalised by the router and were compared verbatim
against the values the application had computed:

    target     app "192.168.88.10"  vs  RouterOS "192.168.88.10/32"
    max-limit  app "5M/5M"          vs  RouterOS "5000000/5000000"

Both comparisons were permanently unequal, so ``needs_update`` never became
False. These tests pin the normalisation so the sync is genuinely idempotent.
"""
import pytest
import respx

from backend.app.core.config import Settings
from backend.app.services.queue_identity import (
    normalize_rate_limit,
    normalize_target,
    queue_matches_device,
    queue_matches_user,
)
from backend.app.services.routeros import RouterOSClient
from backend.app.services.traffic_controller import TrafficController


@pytest.fixture
def mock_settings():
    return Settings(
        ROUTEROS_HOST="192.168.88.1",
        ROUTEROS_PORT=443,
        ROUTEROS_USE_SSL=True,
        ROUTEROS_SSL_VERIFY=False,
        ROUTEROS_USER="admin",
        ROUTEROS_PASSWORD="password",
    )


# --- normalisation primitives -------------------------------------------------

def test_normalize_target_treats_bare_ip_and_cidr_as_equal():
    """RouterOS echoes back '/32' on every host target; the app sends bare IPs."""
    assert normalize_target("192.168.88.10") == normalize_target("192.168.88.10/32")
    assert normalize_target("192.168.88.10,192.168.88.11") == normalize_target(
        "192.168.88.10/32,192.168.88.11/32"
    )
    # Order must not matter - RouterOS may reorder the target list.
    assert normalize_target("192.168.88.11,192.168.88.10") == normalize_target(
        "192.168.88.10/32,192.168.88.11/32"
    )
    # Genuinely different targets must still compare unequal.
    assert normalize_target("192.168.88.10") != normalize_target("192.168.88.12")
    # A real subnet must not be silently rewritten to /32.
    assert normalize_target("192.168.88.0/24") != normalize_target("192.168.88.0/32")


def test_normalize_rate_limit_converts_units_to_bits_per_second():
    """RouterOS stores rates in bps; the UI and DB use '5M/5M' shorthand."""
    assert normalize_rate_limit("5M/5M") == normalize_rate_limit("5000000/5000000")
    assert normalize_rate_limit("10M/50M") == normalize_rate_limit("10000000/50000000")
    assert normalize_rate_limit("512k/1M") == normalize_rate_limit("512000/1000000")
    assert normalize_rate_limit("0/0") == normalize_rate_limit("0/0")
    # Different limits must remain distinguishable.
    assert normalize_rate_limit("5M/5M") != normalize_rate_limit("10M/10M")


def test_queue_comment_matching_is_exact_not_substring():
    """User 'M' must never match user 'Mark' via substring comparison.

    ':managed:M' is a prefix of ':managed:Mark', which previously allowed user M
    to adopt (and rewrite) Mark's queue depending on RouterOS list ordering.
    """

    class Q:
        def __init__(self, name, comment):
            self.name = name
            self.comment = comment

    marks_queue = Q("mikroman-Mark", "mikroman:managed:Mark")

    assert queue_matches_user(marks_queue, user_id=2, user_name="Mark") is True
    assert queue_matches_user(marks_queue, user_id=1, user_name="M") is False

    ms_queue = Q("mikroman-M", "mikroman:managed:M")
    assert queue_matches_user(ms_queue, user_id=1, user_name="M") is True
    assert queue_matches_user(ms_queue, user_id=2, user_name="Mark") is False

    # Device queues must not be adopted by a user whose name prefixes them.
    dev_queue = Q("mikroman-M-phone", "mikroman:managed:dev_7")
    assert queue_matches_user(dev_queue, user_id=1, user_name="M") is False
    assert queue_matches_device(dev_queue, device_id=7) is True
    assert queue_matches_device(dev_queue, device_id=1) is False


# --- end-to-end idempotency ---------------------------------------------------

@pytest.mark.asyncio
async def test_sync_user_queue_issues_no_write_when_already_correct(mock_settings):
    """An already-correct user queue must not be rewritten on every poll tick."""
    ctrl = TrafficController(RouterOSClient(mock_settings))

    # assert_all_called=False: ensure_fasttrack_exemption is rate-limited to one
    # check per router every 5 minutes, so the firewall route may not be hit.
    with respx.mock(base_url="https://192.168.88.1:443/rest", assert_all_called=False) as mock:
        mock.get("/queue/simple").respond(
            200,
            json=[{
                ".id": "*1",
                "name": "mikroman-Mark",
                # RouterOS normalises host targets to /32 ...
                "target": "192.168.88.242/32,192.168.88.243/32",
                # ... and unlimited to 0/0
                "max-limit": "0/0",
                "parent": "none",
                "comment": "mikroman:managed:user_2",
            }],
        )
        mock.get("/ip/firewall/address-list").respond(
            200,
            json=[
                {".id": "*A1", "list": "mikroman_queued", "address": "192.168.88.242",
                 "comment": "mikroman:queued:user_2"},
                {".id": "*A2", "list": "mikroman_queued", "address": "192.168.88.243",
                 "comment": "mikroman:queued:user_2"},
            ],
        )
        mock.get("/ip/firewall/filter").respond(
            200,
            json=[{".id": "*8", "action": "fasttrack-connection",
                   "src-address-list": "!mikroman_queued",
                   "dst-address-list": "!mikroman_queued"}],
        )
        patch_route = mock.patch("/queue/simple/*1").respond(200, json={})

        await ctrl.sync_user_queue(
            user_id=2,
            user_name="Mark",
            ip_addresses=["192.168.88.242", "192.168.88.243"],
            speed_limit="unlimited",
        )

        assert not patch_route.called, (
            "sync_user_queue rewrote an already-correct queue - this is the "
            "43k-writes-per-day storm that froze RouterOS queue accounting"
        )


@pytest.mark.asyncio
async def test_sync_user_queue_still_writes_when_target_actually_changed(mock_settings):
    """Idempotency must not suppress genuine changes."""
    ctrl = TrafficController(RouterOSClient(mock_settings))

    # assert_all_called=False: ensure_fasttrack_exemption is rate-limited to one
    # check per router every 5 minutes, so the firewall route may not be hit.
    with respx.mock(base_url="https://192.168.88.1:443/rest", assert_all_called=False) as mock:
        mock.get("/queue/simple").respond(
            200,
            json=[{
                ".id": "*1",
                "name": "mikroman-Mark",
                "target": "192.168.88.242/32",
                "max-limit": "0/0",
                "parent": "none",
                "comment": "mikroman:managed:user_2",
            }],
        )
        mock.get("/ip/firewall/address-list").respond(200, json=[])
        mock.put("/ip/firewall/address-list").respond(200, json={".id": "*A9"})
        mock.get("/ip/firewall/filter").respond(200, json=[])
        patch_route = mock.patch("/queue/simple/*1").respond(200, json={})

        await ctrl.sync_user_queue(
            user_id=2,
            user_name="Mark",
            # a second device joined
            ip_addresses=["192.168.88.242", "192.168.88.243"],
            speed_limit="unlimited",
        )

        assert patch_route.called, "a real target change must still be pushed"
