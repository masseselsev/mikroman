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


def test_is_self_api_login_matches_the_same_account_and_address():
    assert is_self_api_login(
        "user rest logged in from 192.168.123.250 via rest-api", "rest", "192.168.123.250"
    )
    assert is_self_api_login(
        "user rest logged out from 192.168.123.250 via api", "rest", "192.168.123.250"
    )


def test_is_self_api_login_requires_both_the_account_and_the_address():
    # Same account, different source - a human or another script reusing the
    # credential from somewhere unexpected. Must stay visible.
    assert not is_self_api_login(
        "user rest logged in from 203.0.113.9 via rest-api", "rest", "192.168.123.250"
    )
    # Same address, different account.
    assert not is_self_api_login(
        "user admin logged in from 192.168.123.250 via rest-api", "rest", "192.168.123.250"
    )


def test_is_self_api_login_leaves_the_no_address_sibling_line_alone():
    # RouterOS logs "... via api" (no address) alongside "... via rest-api
    # from <ip>" for the same event. There is nothing to compare an IP
    # against on the first one, so it is never hidden.
    assert not is_self_api_login("user rest logged in via api", "rest", "192.168.123.250")


def test_is_self_api_login_ignores_unrelated_auth_lines():
    assert not is_self_api_login(
        "login failure for user admin from 198.51.100.22 via ssh", "rest", "192.168.123.250"
    )


def test_is_self_api_login_is_inert_with_nothing_to_compare():
    assert not is_self_api_login("user rest logged in from 192.168.123.250 via rest-api", None, "192.168.123.250")
    assert not is_self_api_login("user rest logged in from 192.168.123.250 via rest-api", "rest", None)
