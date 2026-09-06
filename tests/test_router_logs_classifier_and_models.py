from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base, Router, RouterLog
from backend.app.services.log_classifier import classify_log_entry, is_self_api_login


def test_classify_auth_failure():
    sev, cat = classify_log_entry("system,error,critical", "login failure for user admin from 198.51.100.54 via api")
    assert sev == "critical"
    assert cat == "auth"


def test_classify_link_flapping():
    sev, cat = classify_log_entry("interface,warning", "ether1 link down")
    assert sev == "warning"
    assert cat == "interface"

    sev2, cat2 = classify_log_entry("interface,info", "ether1 link up (speed 1G, full duplex)")
    assert sev2 == "info"
    assert cat2 == "interface"


def test_classify_wireless_and_dhcp():
    sev, cat = classify_log_entry("wireless,info", "AA:BB:CC:11:22:33@wifi1: connected, signal strength -54")
    assert cat == "wireless"

    sev_d, cat_d = classify_log_entry("dhcp,warning", "dhcp1: conflict detected for 192.168.88.100")
    assert sev_d == "warning"
    assert cat_d == "dhcp"


def test_classify_firewall_and_fallback():
    sev, cat = classify_log_entry("firewall,info", "forward: in:ether1 out:bridge, proto TCP (SYN), 198.51.100.10:1234->192.168.88.50:80")
    assert cat == "firewall"

    sev_sys, cat_sys = classify_log_entry("system,info", "router rebooted")
    assert sev_sys == "info"
    assert cat_sys == "system"


@pytest.mark.asyncio
async def test_router_log_model_crud():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with factory() as s:
        r = Router(name="TestRouter", host="192.168.88.1")
        s.add(r)
        await s.commit()

        log = RouterLog(
            router_id=r.id,
            external_id="*A1",
            timestamp=datetime(2026, 9, 4, 12, 0, 0),
            topics="system,error,critical",
            message="login failure for user admin from 198.51.100.22 via ssh",
            severity="critical",
            category="auth",
        )
        s.add(log)
        await s.commit()

        loaded = (await s.execute(select(RouterLog).where(RouterLog.router_id == r.id))).scalar_one()
        assert loaded.external_id == "*A1"
        assert loaded.category == "auth"
        assert loaded.severity == "critical"


def test_is_self_api_login_matches_every_api_login_for_the_account():
    """Both shapes RouterOS logs, with and without a source address.

    The address was dropped from the comparison deliberately. MikroMan runs in
    a container, so the local address it can discover for itself is the
    container's (172.17.x.x) while the router, past NAT, records the host's -
    they never matched, and the filter therefore never hid anything.
    """
    assert is_self_api_login("user rest logged in from 192.168.123.250 via rest-api", "rest")
    assert is_self_api_login("user rest logged out from 192.168.123.250 via api", "rest")
    # The sibling line for the same event carries no address at all.
    assert is_self_api_login("user rest logged in via api", "rest")
    assert is_self_api_login("user rest logged out via api", "rest")


def test_is_self_api_login_still_requires_the_account_to_match():
    assert not is_self_api_login("user admin logged in from 192.168.123.250 via rest-api", "rest")
    assert not is_self_api_login("user admin logged in via api", "rest")


def test_is_self_api_login_ignores_logins_over_other_transports():
    """Only api/rest-api sessions are MikroMan's. A winbox or ssh login using
    the same account is a person, and stays visible."""
    assert not is_self_api_login("user rest logged in from 192.168.123.250 via winbox", "rest")
    assert not is_self_api_login("user rest logged in from 192.168.123.250 via ssh", "rest")


def test_is_self_api_login_needs_a_username():
    assert not is_self_api_login("user rest logged in via api", "")
    assert not is_self_api_login("user rest logged in via api", None)


def test_is_self_api_login_ignores_unrelated_auth_lines():
    assert not is_self_api_login(
        "login failure for user admin from 198.51.100.22 via ssh", "rest"
    )
