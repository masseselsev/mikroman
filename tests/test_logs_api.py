from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base, Router, RouterLog
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services.guards import WriteGuardViolation
from backend.app.services.router_manager import router_manager


@pytest.fixture
async def async_client():
    await router_manager.aclose()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, session_factory

    app.dependency_overrides.clear()
    await router_manager.aclose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_logs_api_endpoints(async_client):
    api_client, session_factory = async_client

    async with session_factory() as db_session:
        r = Router(name="LogsRouter", host="192.168.88.1", is_active=True)
        db_session.add(r)
        await db_session.commit()
        await db_session.refresh(r)

        # Seed some DB logs
        log1 = RouterLog(
            router_id=r.id,
            external_id="*1",
            timestamp=datetime.now(),
            topics="system,error,critical",
            message="login failure for user admin from 10.0.0.1",
            severity="critical",
            category="auth",
        )
        log2 = RouterLog(
            router_id=r.id,
            external_id="*2",
            timestamp=datetime.now(),
            topics="wireless,info",
            message="client connected to wifi",
            severity="info",
            category="wireless",
        )
        db_session.add_all([log1, log2])
        await db_session.commit()

        router_id = r.id

    # 1. GET /api/v1/logs from DB (default)
    resp = await api_client.get(f"/api/v1/logs?router_id={router_id}&source=db")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 2

    # 2. Filter by category
    resp_cat = await api_client.get(f"/api/v1/logs?router_id={router_id}&category=auth")
    assert resp_cat.status_code == 200
    assert len(resp_cat.json()["data"]) == 1
    assert resp_cat.json()["data"][0]["category"] == "auth"

    # 3. GET /api/v1/logs/stats
    resp_stats = await api_client.get(f"/api/v1/logs/stats?router_id={router_id}")
    assert resp_stats.status_code == 200
    stats = resp_stats.json()["data"]
    assert stats["critical_count"] == 1
    assert stats["auth_failures_count"] == 1

    # 4. Live logs from router
    mock_router_client = AsyncMock()
    mock_router_client.get_logs.return_value = [
        {".id": "*L1", "time": "2026-09-04 12:00:00", "topics": "interface,warning", "message": "ether1 link down"}
    ]
    with patch("backend.app.api.v1.endpoints.logs.get_client_for_router", return_value=mock_router_client):
        resp_live = await api_client.get(f"/api/v1/logs?router_id={router_id}&source=live")
        assert resp_live.status_code == 200
        live_data = resp_live.json()["data"]
        assert len(live_data) == 1
        assert live_data[0]["category"] == "interface"
        assert live_data[0]["severity"] == "warning"

    # 5. Logging rules
    mock_router_client.get_logging_rules.return_value = [
        {".id": "*R1", "topics": "info", "action": "memory", "comment": "default"},
        {".id": "*R2", "topics": "wireless", "action": "memory", "comment": "mikroman:log:wireless"},
    ]
    mock_router_client.add_logging_rule.return_value = "*R3"
    mock_router_client.remove_logging_rule.side_effect = lambda rule_id, **kw: True if rule_id == "*R2" else (_ for _ in ()).throw(
        WriteGuardViolation("ForeignResourceGuard", "Cannot delete foreign rule", "*R1")
    )

    with patch("backend.app.api.v1.endpoints.logs.get_client_for_router", return_value=mock_router_client):
        # List rules
        resp_rules = await api_client.get(f"/api/v1/logs/rules?router_id={router_id}")
        assert resp_rules.status_code == 200
        assert len(resp_rules.json()["data"]) == 2

        # Add rule
        resp_add = await api_client.post(
            f"/api/v1/logs/rules?router_id={router_id}",
            json={"topics": "wireguard", "action": "memory"},
        )
        assert resp_add.status_code == 200

        # Delete rule - managed rule succeeds
        resp_del = await api_client.delete(f"/api/v1/logs/rules/*R2?router_id={router_id}")
        assert resp_del.status_code == 200

        # Delete rule - foreign rule returns 400
        resp_fail = await api_client.delete(f"/api/v1/logs/rules/*R1?router_id={router_id}")
        assert resp_fail.status_code == 400


@pytest.mark.asyncio
async def test_hide_self_api_filters_only_the_matching_login_pair(async_client, monkeypatch):
    """`hide_self_api=true` drops every api/rest-api login line for the router
    account MikroMan connects with - both the shape carrying a source address
    and the sibling without one - while leaving logins over other transports,
    and everything that is not a login, untouched.

    The source address is deliberately not part of the match: in a container
    the address MikroMan can learn for itself is the container's, while the
    router records the host's from behind NAT, so requiring them to be equal
    meant the filter never hid anything at all.
    """
    api_client, session_factory = async_client

    async with session_factory() as db_session:
        r = Router(name="SelfApiRouter", host="192.168.123.1", username="rest", is_active=True)
        db_session.add(r)
        await db_session.commit()
        await db_session.refresh(r)

        db_session.add_all([
            RouterLog(
                router_id=r.id, external_id="*S1", timestamp=datetime.now(),
                topics="system,info,account",
                message="user rest logged in from 192.168.123.250 via rest-api",
                severity="info", category="auth",
            ),
            RouterLog(
                router_id=r.id, external_id="*S2", timestamp=datetime.now(),
                topics="system,info,account",
                message="user rest logged out from 192.168.123.250 via rest-api",
                severity="info", category="auth",
            ),
            # The address-less sibling RouterOS writes for the same event.
            RouterLog(
                router_id=r.id, external_id="*S3", timestamp=datetime.now(),
                topics="system,info,account",
                message="user rest logged in via api",
                severity="info", category="auth",
            ),
            # Same account over an interactive transport - a person, not us.
            RouterLog(
                router_id=r.id, external_id="*S5", timestamp=datetime.now(),
                topics="system,info,account",
                message="user rest logged in from 203.0.113.9 via winbox",
                severity="info", category="auth",
            ),
            RouterLog(
                router_id=r.id, external_id="*S4", timestamp=datetime.now(),
                topics="wireless,info", message="client connected to wifi",
                severity="info", category="wireless",
            ),
        ])
        await db_session.commit()
        router_id = r.id

    resp_all = await api_client.get(f"/api/v1/logs?router_id={router_id}&source=db")
    assert len(resp_all.json()["data"]) == 5

    resp_hidden = await api_client.get(f"/api/v1/logs?router_id={router_id}&source=db&hide_self_api=true")
    messages = [item["message"] for item in resp_hidden.json()["data"]]
    assert len(messages) == 2
    assert "user rest logged in from 203.0.113.9 via winbox" in messages
    assert "client connected to wifi" in messages


@pytest.mark.asyncio
async def test_log_stats_counts_only_entries_after_since(async_client):
    """`/logs/stats` must be able to answer "what is new since I last looked".

    It counted a fixed trailing 24 hours, so the navbar badge showed the day's
    error total and could never be cleared by reading the log - a router with a
    few hundred routine errors a day sat on "99+" permanently, which says
    nothing and trains the reader to ignore it.
    """
    api_client, session_factory = async_client

    async with session_factory() as db_session:
        r = Router(name="StatsRouter", host="192.168.90.1", username="admin", is_active=True)
        db_session.add(r)
        await db_session.commit()
        await db_session.refresh(r)

        now = datetime.now()
        db_session.add_all([
            RouterLog(
                router_id=r.id, external_id="*O1", timestamp=now - timedelta(hours=6),
                topics="system,error", message="old failure",
                severity="error", category="system",
            ),
            RouterLog(
                router_id=r.id, external_id="*N1", timestamp=now - timedelta(minutes=5),
                topics="system,error", message="fresh failure",
                severity="error", category="system",
            ),
        ])
        await db_session.commit()
        router_id = r.id

    # No `since`: the historical 24-hour window, both entries.
    resp_day = await api_client.get(f"/api/v1/logs/stats?router_id={router_id}")
    assert resp_day.json()["data"]["error_count"] == 2

    # Since an hour ago: only what arrived after the reader last looked.
    cutoff = (datetime.now() - timedelta(hours=1)).isoformat()
    resp_since = await api_client.get(
        f"/api/v1/logs/stats?router_id={router_id}&since={cutoff}"
    )
    data = resp_since.json()["data"]
    assert data["error_count"] == 1
    assert data["total_logs"] == 1
