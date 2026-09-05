"""`/log` reads and `/system/logging` rule management on the client.

Three things had drifted apart and none of them was caught, because both the
log scraper and the API swallow transport errors: the scraper called
``get_logs`` while the mixin only defined ``get_log``, the API passed a
``prefix`` the mixin did not accept, and ``remove_logging_rule`` was called but
never existed. Each failure surfaced as "no logs", not as an error.
"""
import httpx
import pytest
import respx

from backend.app.schemas.router import RouterConfig
from backend.app.services.guards import WriteGuardViolation
from backend.app.services.routeros.client import RouterOSClient

BASE = "http://192.168.88.1:80/rest"


def make_client() -> RouterOSClient:
    return RouterOSClient(RouterConfig(host="192.168.88.1", username="admin", password="x"))


@pytest.mark.asyncio
@respx.mock
async def test_get_logs_reads_the_log_menu():
    """The scraper's name for it must reach the same RouterOS menu."""
    respx.get(f"{BASE}/log").mock(return_value=httpx.Response(200, json=[
        {".id": "*1", "time": "sep/04 10:00:00", "topics": "system,info", "message": "router rebooted"},
        {".id": "*2", "time": "sep/04 10:00:05", "topics": "dhcp,info", "message": "assigned 192.168.88.41"},
    ]))

    entries = await make_client().get_logs(limit=10)

    assert [e[".id"] for e in entries] == ["*1", "*2"]


@pytest.mark.asyncio
@respx.mock
async def test_get_logs_honours_the_limit_and_topic_filter():
    respx.get(f"{BASE}/log").mock(return_value=httpx.Response(200, json=[
        {".id": "*1", "topics": "system,info", "message": "a"},
        {".id": "*2", "topics": "dhcp,info", "message": "b"},
        {".id": "*3", "topics": "dhcp,info", "message": "c"},
    ]))
    client = make_client()

    assert len(await client.get_logs(limit=2)) == 2
    dhcp_only = await client.get_logs(limit=10, topics="dhcp")
    assert [e[".id"] for e in dhcp_only] == ["*2", "*3"]


@pytest.mark.asyncio
@respx.mock
async def test_add_logging_rule_stamps_its_own_comment():
    """The comment is what later lets the guard tell our rules from the admin's."""
    route = respx.put(f"{BASE}/system/logging").mock(
        return_value=httpx.Response(200, json={".id": "*9"})
    )

    rule_id = await make_client().add_logging_rule(topics="wireless", prefix="wifi")

    assert rule_id == "*9"
    sent = route.calls.last.request
    import json
    body = json.loads(sent.content)
    assert body["topics"] == "wireless"
    assert body["action"] == "memory"
    assert body["prefix"] == "wifi"
    assert body["comment"] == "mikroman:log:wireless"


@pytest.mark.asyncio
@respx.mock
async def test_removing_a_router_owned_rule_is_refused():
    """Deleting the built-in `info` rule would silence the log entirely."""
    respx.get(f"{BASE}/system/logging").mock(return_value=httpx.Response(200, json=[
        {".id": "*1", "topics": "info", "action": "memory"},
        {".id": "*7", "topics": "wireless", "action": "memory", "comment": "mikroman:log:wireless"},
    ]))
    deletion = respx.delete(f"{BASE}/system/logging/*1").mock(
        return_value=httpx.Response(204)
    )

    with pytest.raises(WriteGuardViolation) as exc:
        await make_client().remove_logging_rule("*1")

    assert "ForeignResourceGuard" in str(exc.value)
    assert not deletion.called  # refused before the packet, not after


@pytest.mark.asyncio
@respx.mock
async def test_removing_our_own_rule_is_allowed():
    respx.get(f"{BASE}/system/logging").mock(return_value=httpx.Response(200, json=[
        {".id": "*1", "topics": "info", "action": "memory"},
        {".id": "*7", "topics": "wireless", "action": "memory", "comment": "mikroman:log:wireless"},
    ]))
    deletion = respx.delete(f"{BASE}/system/logging/*7").mock(
        return_value=httpx.Response(204)
    )

    assert await make_client().remove_logging_rule("*7") is True
    assert deletion.called
