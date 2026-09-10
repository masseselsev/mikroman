import time

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app.core.auth import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    create_csrf_token,
    create_session_token,
    hash_password,
    verify_csrf_token,
    verify_password,
    verify_session_or_api_key,
    verify_session_token,
)
from backend.app.core.config import settings
from backend.app.db.models import Base
from backend.app.db.session import get_db
from backend.app.main import app

# ---------------------------------------------------------------------------
# Unit tests: Cryptographic Primitives
# ---------------------------------------------------------------------------

def test_password_hashing_and_verification():
    secret = "SuperSecretPassword123!"
    hashed = hash_password(secret)

    # Format check: pbkdf2_sha256$<rounds>$<salt>$<derived>
    parts = hashed.split("$")
    assert len(parts) == 4
    assert parts[0] == "pbkdf2_sha256"
    assert int(parts[1]) == 600_000

    # Verification
    assert verify_password(secret, hashed) is True
    assert verify_password("WrongPassword", hashed) is False
    assert verify_password("", hashed) is False
    assert verify_password(secret, "") is False
    assert verify_password(secret, "corrupted$hash$value") is False

    # Salt uniqueness: hashing the same password twice produces different hashes
    hashed2 = hash_password(secret)
    assert hashed != hashed2
    assert verify_password(secret, hashed2) is True


def test_session_token_creation_and_expiration(monkeypatch):
    token = create_session_token(username="admin", expires_days=7)
    payload = verify_session_token(token)
    assert payload is not None
    assert payload["sub"] == "admin"
    assert payload["exp"] > time.time()

    # Expired token
    expired_token = create_session_token(username="admin", expires_days=-1)
    assert verify_session_token(expired_token) is None

    # Tampered token
    tampered = token[:-4] + "AAAA"
    assert verify_session_token(tampered) is None
    assert verify_session_token("") is None


def test_verify_session_or_api_key(monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", "secret-test-key-456")

    # Matching API key
    res = verify_session_or_api_key("secret-test-key-456")
    assert res is not None
    assert res["sub"] == "api_key"

    # Invalid API key
    assert verify_session_or_api_key("wrong-key") is None

    # Valid session token passes through
    session_token = create_session_token(username="admin")
    res_session = verify_session_or_api_key(session_token)
    assert res_session is not None
    assert res_session["sub"] == "admin"


def test_csrf_tokens():
    t1 = create_csrf_token()
    t2 = create_csrf_token()
    assert len(t1) >= 32
    assert t1 != t2

    assert verify_csrf_token(t1, t1) is True
    assert verify_csrf_token(t1, t2) is False
    assert verify_csrf_token(None, t1) is False
    assert verify_csrf_token(t1, None) is False
    assert verify_csrf_token("", "") is False


# ---------------------------------------------------------------------------
# Integration tests: HTTP Endpoints & Middleware Enforcement
# ---------------------------------------------------------------------------

@pytest.fixture
async def auth_test_client(monkeypatch):
    """Client with AUTH_ENABLED=True and an in-memory SQLite database."""
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", None)
    monkeypatch.setattr(settings, "API_KEY", "test-api-token-789")

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
    await engine.dispose()


@pytest.mark.asyncio
async def test_health_endpoints_always_accessible(auth_test_client):
    client, _ = auth_test_client
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    resp2 = await client.get("/api/v1/health")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_unauthenticated_requests_blocked(auth_test_client):
    client, _ = auth_test_client
    # Protected endpoint without cookie or bearer token
    resp = await client.get("/api/v1/users")
    assert resp.status_code == 401
    assert "Authentication required" in resp.text


@pytest.mark.asyncio
async def test_initial_setup_flow(auth_test_client):
    client, _ = auth_test_client

    # 1. Check status - initially needs setup
    status_resp = await client.get("/api/v1/auth/status")
    assert status_resp.status_code == 200
    status_data = status_resp.json()["data"]
    assert status_data["auth_enabled"] is True
    assert status_data["authenticated"] is False
    assert status_data["needs_setup"] is True
    assert CSRF_COOKIE_NAME in status_resp.cookies

    # 2. Reject short passwords (< 8 chars)
    short_resp = await client.post("/api/v1/auth/setup", json={"password": "short"})
    assert short_resp.status_code == 422

    # 3. Successful initial setup
    setup_resp = await client.post("/api/v1/auth/setup", json={"password": "ValidAdminPassword123!"})
    assert setup_resp.status_code == 200
    assert setup_resp.json()["data"]["username"] == "admin"
    assert SESSION_COOKIE_NAME in setup_resp.cookies
    assert CSRF_COOKIE_NAME in setup_resp.cookies

    session_cookie = setup_resp.cookies[SESSION_COOKIE_NAME]
    csrf_cookie = setup_resp.cookies[CSRF_COOKIE_NAME]
    assert len(csrf_cookie) > 0

    # 4. Subsequent setup attempt rejected
    dupe_resp = await client.post("/api/v1/auth/setup", json={"password": "AnotherPassword123!"})
    assert dupe_resp.status_code == 400
    assert "already configured" in dupe_resp.text

    # 5. Protected GET request using session cookie succeeds
    get_resp = await client.get("/api/v1/users", cookies={SESSION_COOKIE_NAME: session_cookie})
    assert get_resp.status_code == 200


@pytest.mark.asyncio
async def test_csrf_protection_on_mutating_requests(auth_test_client):
    client, _ = auth_test_client

    # Complete setup to get valid credentials
    setup_resp = await client.post("/api/v1/auth/setup", json={"password": "StrongPassword789!"})
    session_cookie = setup_resp.cookies[SESSION_COOKIE_NAME]
    csrf_cookie = setup_resp.cookies[CSRF_COOKIE_NAME]

    # Mutating POST without X-CSRF-Token header -> 403 Forbidden
    post_no_csrf = await client.post(
        "/api/v1/users",
        json={"name": "Alice"},
        cookies={SESSION_COOKIE_NAME: session_cookie, CSRF_COOKIE_NAME: csrf_cookie},
    )
    assert post_no_csrf.status_code == 403
    assert "CSRF token" in post_no_csrf.text

    # Mutating POST with mismatched X-CSRF-Token header -> 403 Forbidden
    post_bad_csrf = await client.post(
        "/api/v1/users",
        json={"name": "Alice"},
        headers={"X-CSRF-Token": "invalid-csrf-token"},
        cookies={SESSION_COOKIE_NAME: session_cookie, CSRF_COOKIE_NAME: csrf_cookie},
    )
    assert post_bad_csrf.status_code == 403

    # Mutating POST with correct X-CSRF-Token header -> allowed (reaches endpoint logic)
    post_valid = await client.post(
        "/api/v1/users",
        json={"name": "Alice"},
        headers={"X-CSRF-Token": csrf_cookie},
        cookies={SESSION_COOKIE_NAME: session_cookie, CSRF_COOKIE_NAME: csrf_cookie},
    )
    assert post_valid.status_code == 201


@pytest.mark.asyncio
async def test_login_and_logout_lifecycle(auth_test_client):
    client, _ = auth_test_client

    # Setup admin
    await client.post("/api/v1/auth/setup", json={"password": "MySecretPassword123!"})

    # Wrong password -> 401
    bad_login = await client.post("/api/v1/auth/login", json={"password": "IncorrectPassword"})
    assert bad_login.status_code == 401

    # Correct password -> 200 and sets cookies
    login_resp = await client.post("/api/v1/auth/login", json={"password": "MySecretPassword123!"})
    assert login_resp.status_code == 200
    assert SESSION_COOKIE_NAME in login_resp.cookies
    session_cookie = login_resp.cookies[SESSION_COOKIE_NAME]

    # Status shows authenticated
    status_resp = await client.get("/api/v1/auth/status", cookies={SESSION_COOKIE_NAME: session_cookie})
    assert status_resp.json()["data"]["authenticated"] is True

    # Logout clears cookies
    logout_resp = await client.post("/api/v1/auth/logout")
    assert logout_resp.status_code == 200


@pytest.mark.asyncio
async def test_api_key_bearer_authentication(auth_test_client):
    client, _ = auth_test_client

    # Valid Bearer token allows GET
    resp = await client.get(
        "/api/v1/users",
        headers={"Authorization": "Bearer test-api-token-789"},
    )
    assert resp.status_code == 200

    # Valid Bearer token allows mutating POST without CSRF header
    post_resp = await client.post(
        "/api/v1/users",
        json={"name": "Bob"},
        headers={"Authorization": "Bearer test-api-token-789"},
    )
    assert post_resp.status_code == 201

    # Invalid Bearer token rejected
    bad_resp = await client.get(
        "/api/v1/users",
        headers={"Authorization": "Bearer bad-token"},
    )
    assert bad_resp.status_code == 401


def test_websocket_authentication_handshake(monkeypatch):
    """Test WebSocket telemetry authentication via TestClient."""
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "API_KEY", "ws-api-key-999")

    client = TestClient(app)

    # 1. Unauthenticated WebSocket rejected
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/telemetry"):
            pass
    assert exc_info.value.code == 1008

    # 2. WebSocket with valid query token accepted
    with client.websocket_connect("/ws/telemetry?token=ws-api-key-999") as ws:
        # Connection succeeds without 1008 policy violation
        assert ws is not None

    # 3. WebSocket with valid session cookie accepted
    session_token = create_session_token(username="admin")
    client.cookies.set(SESSION_COOKIE_NAME, session_token)
    with client.websocket_connect("/ws/telemetry") as ws:
        assert ws is not None
