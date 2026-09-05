"""Management services left open to any source address.

`/ip/service` takes an `address=` list of source prefixes. Empty - the RouterOS
default - means the service answers whatever can route to it. The audit reports
that as a fact ("no source-address restriction"), not as "exposed to the
internet", because reachability is not something the router can tell us.
"""
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import AlertLog, Base
from backend.app.services.security_audit import (
    ALERT_TYPE,
    check_and_alert,
    find_unrestricted_services,
)


class FakeHttp:
    def __init__(self, services):
        self.services = services

    async def get(self, path):
        class R:
            status_code = 200

            def json(_self):
                return self.services
        return R()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeClient:
    def __init__(self, services):
        self.services = services

    def _get_client(self):
        return FakeHttp(self.services)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def svc(name, **over):
    base = {"name": name, "port": "443", "disabled": "false"}
    base.update(over)
    return base


def test_a_service_with_no_address_list_is_a_finding():
    found = find_unrestricted_services([svc("www-ssl")])
    assert [f["name"] for f in found] == ["www-ssl"]


def test_an_address_restricted_service_is_not_a_finding():
    assert find_unrestricted_services([svc("www-ssl", address="192.168.88.0/24")]) == []


def test_an_all_zero_prefix_anywhere_in_the_list_defeats_the_restriction():
    # "192.168.88.0/24,0.0.0.0/0" reads restrictive but is not.
    found = find_unrestricted_services([svc("ssh", address="192.168.88.0/24,0.0.0.0/0")])
    assert [f["name"] for f in found] == ["ssh"]
    assert find_unrestricted_services([svc("api", address="::/0")])


def test_a_disabled_service_answers_nobody_and_is_not_a_finding():
    assert find_unrestricted_services([svc("telnet", disabled="true")]) == []


def test_non_management_services_are_ignored():
    assert find_unrestricted_services([svc("bandwidth-test"), svc("something-else")]) == []


@pytest.mark.asyncio
async def test_the_alert_names_every_open_service_and_its_port(session):
    client = FakeClient([
        svc("www-ssl", port="444"),
        svc("api-ssl", port="8729"),
        svc("winbox", port="8291", address="192.168.88.0/24"),
    ])

    findings = await check_and_alert(session, 1, client)
    assert {f["name"] for f in findings} == {"www-ssl", "api-ssl"}

    alert = (await session.execute(select(AlertLog))).scalars().one()
    assert alert.alert_type == ALERT_TYPE
    assert alert.router_id == 1
    assert "www-ssl:444" in alert.message
    assert "api-ssl:8729" in alert.message
    assert "winbox" not in alert.message
    assert "/ip/service address=" in alert.message


@pytest.mark.asyncio
async def test_it_does_not_re_alert_on_every_poll_tick(session):
    client = FakeClient([svc("www-ssl")])

    await check_and_alert(session, 1, client)
    await check_and_alert(session, 1, client)
    await check_and_alert(session, 1, client)

    # The condition changes only when someone edits the router; one entry a day
    # is enough, and more would bury the rest of the alert log.
    assert len((await session.execute(select(AlertLog))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_a_locked_down_router_produces_no_alert(session):
    client = FakeClient([svc("www-ssl", address="192.168.88.0/24")])
    assert await check_and_alert(session, 1, client) == []
    assert (await session.execute(select(AlertLog))).scalars().all() == []


@pytest.mark.asyncio
async def test_each_router_is_alerted_on_its_own(session):
    client = FakeClient([svc("ssh", port="22")])
    await check_and_alert(session, 1, client)
    await check_and_alert(session, 2, client)

    rows = (await session.execute(select(AlertLog))).scalars().all()
    assert sorted(r.router_id for r in rows) == [1, 2]


def test_repeated_rows_for_one_service_collapse_into_one_finding():
    """RouterOS 7.24 returns one row per listening socket, not per service.

    A live hAP be3 answered `/ip/service` with twenty rows: `www-ssl` five
    times and `winbox` twice, each with its own `.id`. Reported verbatim the
    alert read "www-ssl:443, www-ssl:443, www-ssl:443, ..." and buried
    everything else in the list.
    """
    services = [
        {".id": "*6", "name": "www-ssl", "port": "443", "disabled": "false"},
        {".id": "*1A", "name": "www-ssl", "port": "443", "disabled": "false"},
        {".id": "*15", "name": "www-ssl", "port": "443", "disabled": "false"},
        {".id": "*8", "name": "winbox", "port": "8291", "disabled": "false"},
        {".id": "*10", "name": "winbox", "port": "8291", "disabled": "false"},
        {".id": "*4", "name": "ssh", "port": "22", "disabled": "false"},
    ]
    found = find_unrestricted_services(services)
    assert sorted((f["name"], f["port"]) for f in found) == [
        ("ssh", "22"), ("winbox", "8291"), ("www-ssl", "443"),
    ]


def test_the_same_service_on_two_different_ports_stays_two_findings():
    # Collapsing is by name *and* port - a second listener on another port is a
    # separate thing to lock down.
    found = find_unrestricted_services([
        {"name": "www-ssl", "port": "443", "disabled": "false"},
        {"name": "www-ssl", "port": "8443", "disabled": "false"},
    ])
    assert sorted(f["port"] for f in found) == ["443", "8443"]


def test_router_only_services_are_not_management_findings():
    # 7.24 also lists resolver/dhcp/dhcpclient/reverse-proxy/btest/discover.
    # Those are not management access and must not pad the alert.
    assert find_unrestricted_services([
        {"name": "resolver", "port": "53", "disabled": "false"},
        {"name": "dhcp", "port": "67", "disabled": "false"},
        {"name": "dhcpclient", "port": "68", "disabled": "false"},
        {"name": "reverse-proxy", "port": "443", "disabled": "false"},
        {"name": "discover", "port": "5678", "disabled": "false"},
    ]) == []
