# Per-Device Live Connection Tracker, Offline Geo-IP & User Destination Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build real-time per-device live connection tracking, offline microsecond Geo-IP resolution, safety-guarded connection termination, and persistent user destination/domain analytics with multi-column sorting.

**Architecture:**
- Backend: Lightweight in-memory Geo-IP engine (`geoip.py`) mapping IPs to ISO country codes and flag emojis. RouterOS `ConnectionsMixin` fetching active connections (`/ip/firewall/connection`) with `.proplist` to reduce router load and DNS cache mapping (`/ip/dns/cache`).
- Persistence: New SQLite table `UserDestinationStat` tracking traffic volume and connection counts per destination IP and domain per user/device.
- Wire Safety: `remove_firewall_connection` enforces Write Guards to ensure management sessions (MikroMan REST client, router SSH, app container egress) cannot be killed.
- Frontend: Full-screen responsive `LiveConnectionsModal.jsx` with 3-second auto-poll, instant search, protocol filter chips, live bandwidth rates, and kill confirmation; plus a sortable "Destinations & Domains" table in user analytics.

**Tech Stack:** FastAPI, SQLAlchemy (Async), Alembic, Pydantic v2, RouterOS REST API, React 18, Lucide Icons, Vitest / React Testing Library.

## Global Constraints
- Pure offline Geo-IP: Zero external network HTTP calls during connection inspection; all queries run in-memory in microseconds.
- Write Guard protection: Refuse terminating connections to/from immune hosts (router management IP, local host/container IP, loopback).
- RouterOS performance: Must query `/rest/ip/firewall/connection` with explicit `.proplist` parameters.
- All Python tests must run cleanly under `.venv/bin/pytest` and pass `.venv/bin/ruff check`.
- All frontend tests must pass under `npm test`.

---

### Task 1: Offline Geo-IP Engine & Database Models

**Files:**
- Create: `backend/app/services/geoip.py`
- Modify: `backend/app/db/models.py`
- Create: `backend/migrations/versions/20260904_add_user_destination_stat.py`
- Create: `tests/test_geoip_and_models.py`

**Interfaces:**
- Produces:
  - `GeoLocation`: Pydantic dataclass (`country_code: str`, `country_name: str`, `flag_emoji: str`)
  - `resolve_ip_location(ip: str) -> GeoLocation`
  - `UserDestinationStat`: SQLAlchemy model (`id`, `user_id`, `device_id`, `destination_ip`, `domain`, `country_code`, `bytes_in`, `bytes_out`, `total_bytes`, `hit_count`, `last_seen`)

- [ ] **Step 1: Write the failing test for Geo-IP resolution and UserDestinationStat**

Create `tests/test_geoip_and_models.py`:
```python
import pytest
from backend.app.services.geoip import resolve_ip_location, GeoLocation
from backend.app.db.models import UserDestinationStat, User, Device, Base
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

def test_resolve_ip_location_private_and_loopback():
    for ip in ["127.0.0.1", "::1", "192.168.88.1", "10.0.0.5", "172.16.0.1"]:
        loc = resolve_ip_location(ip)
        assert loc.country_code == "LOCAL"
        assert loc.flag_emoji == "🏠"

def test_resolve_ip_location_public():
    loc = resolve_ip_location("8.8.8.8")
    assert isinstance(loc, GeoLocation)
    assert loc.country_code != "LOCAL"
    assert len(loc.country_code) == 2
    assert loc.flag_emoji != ""

@pytest.mark.asyncio
async def test_user_destination_stat_model_crud():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    
    async with factory() as s:
        stat = UserDestinationStat(
            user_id=None,
            destination_ip="142.250.190.46",
            domain="youtube.com",
            country_code="US",
            bytes_in=1000,
            bytes_out=500,
            total_bytes=1500,
            hit_count=5,
        )
        s.add(stat)
        await s.commit()

        loaded = (await s.execute(select(UserDestinationStat).where(UserDestinationStat.domain == "youtube.com"))).scalar_one()
        assert loaded.total_bytes == 1500
        assert loaded.hit_count == 5
        assert loaded.country_code == "US"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_geoip_and_models.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.app.services.geoip'`

- [ ] **Step 3: Implement `backend/app/services/geoip.py` and `UserDestinationStat`**

Implement `backend/app/services/geoip.py`:
- In-memory CIDR lookup with fallback country ranges.
- Helper `country_code_to_flag(code: str) -> str`.
- Private / loopback / RFC 1918 short-circuit to `"LOCAL"` and `"🏠"`.

Update `backend/app/db/models.py`:
- Add `UserDestinationStat` with appropriate indices on `total_bytes`, `hit_count`, `last_seen`, `domain`, `destination_ip`.

Create Alembic migration:
Run or create migration script in `backend/migrations/versions/`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_geoip_and_models.py -v`  
Expected: PASS (3 passed)

- [ ] **Step 5: Run ruff check**

Run: `.venv/bin/ruff check backend/app/services/geoip.py backend/app/db/models.py tests/test_geoip_and_models.py`  
Expected: `All checks passed!`

---

### Task 2: RouterOS Connection Transport & Wire-Level Safety

**Files:**
- Create: `backend/app/services/routeros/connections.py`
- Modify: `backend/app/services/routeros/client.py`
- Create: `tests/test_routeros_connections.py`

**Interfaces:**
- Consumes:
  - `WriteGuardViolation`, `guard_immune_targets` from `backend.app.services.guards`
  - `get_immune_ips` from `RouterOSClient`
- Produces:
  - `ConnectionsMixin.get_active_connections(proplist: Optional[List[str]] = None) -> List[Dict[str, Any]]`
  - `ConnectionsMixin.get_dns_cache_entries() -> Dict[str, str]`
  - `ConnectionsMixin.remove_firewall_connection(connection_id: str, src_ip: Optional[str] = None, dst_ip: Optional[str] = None) -> bool`

- [ ] **Step 1: Write the failing test for ConnectionsMixin and WriteGuard enforcement**

Create `tests/test_routeros_connections.py`:
```python
import pytest
from backend.app.services.routeros.client import RouterOSClient
from backend.app.schemas.router import RouterConfig
from backend.app.services.guards import WriteGuardViolation

@pytest.mark.asyncio
async def test_kill_connection_refuses_immune_targets():
    cfg = RouterConfig(host="192.168.88.1", username="admin", password="x")
    client = RouterOSClient(cfg)
    
    # Targeting the router itself
    with pytest.raises(WriteGuardViolation) as exc:
        await client.remove_firewall_connection(
            connection_id="*1",
            src_ip="192.168.88.100",
            dst_ip="192.168.88.1"
        )
    assert "WriteGuard" in str(exc.value)

    # Targeting loopback
    with pytest.raises(WriteGuardViolation) as exc:
        await client.remove_firewall_connection(
            connection_id="*2",
            src_ip="127.0.0.1",
            dst_ip="1.1.1.1"
        )
    assert "WriteGuard" in str(exc.value)

@pytest.mark.asyncio
async def test_get_active_connections_calls_rest():
    cfg = RouterConfig(host="192.168.88.1", username="admin", password="x")
    client = RouterOSClient(cfg)
    assert hasattr(client, "get_active_connections")
    assert hasattr(client, "get_dns_cache_entries")
    assert hasattr(client, "remove_firewall_connection")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_routeros_connections.py -v`  
Expected: FAIL (`AttributeError: 'RouterOSClient' object has no attribute 'remove_firewall_connection'`)

- [ ] **Step 3: Implement `ConnectionsMixin` and attach to `RouterOSClient`**

Create `backend/app/services/routeros/connections.py`:
- `get_active_connections(proplist=None)` querying `/rest/ip/firewall/connection`.
- `get_dns_cache_entries()` querying `/rest/ip/dns/cache`.
- `remove_firewall_connection(connection_id, src_ip, dst_ip)`:
  - Checks `guard_immune_targets(src_ip, immune, action="kill_connection")` and `guard_immune_targets(dst_ip, immune, action="kill_connection")`.
  - Executes removal via `/rest/ip/firewall/connection/remove`.

Update `backend/app/services/routeros/client.py`:
- Inherit `ConnectionsMixin`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_routeros_connections.py -v`  
Expected: PASS

- [ ] **Step 5: Run ruff check**

Run: `.venv/bin/ruff check backend/app/services/routeros/connections.py backend/app/services/routeros/client.py tests/test_routeros_connections.py`  
Expected: `All checks passed!`

---

### Task 3: Backend API Endpoints & Attribution

**Files:**
- Create: `backend/app/schemas/connection.py`
- Create: `backend/app/api/v1/endpoints/connections.py`
- Modify: `backend/app/api/v1/endpoints/analytics.py`
- Modify: `backend/app/api/v1/router.py`
- Create: `tests/test_connections_api.py`

**Interfaces:**
- Consumes:
  - `RouterOSClient.get_active_connections`, `get_dns_cache_entries`, `remove_firewall_connection`
  - `resolve_ip_location`
  - `UserDestinationStat`
- Produces:
  - `GET /api/v1/connections`
  - `POST /api/v1/connections/{connection_id}/kill`
  - `GET /api/v1/analytics/users/{user_id}/destinations`

- [ ] **Step 1: Write the failing test for connection endpoints and destination analytics**

Create `tests/test_connections_api.py`:
```python
import pytest
from httpx import AsyncClient, ASGITransport
from backend.app.main import app
from backend.app.db.models import Base, Device, User, UserDestinationStat
from backend.app.db.session import get_db
from backend.app.services.router_manager import router_manager
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

@pytest.fixture
async def api_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async def override_db():
        async with factory() as s:
            yield s
    app.dependency_overrides[get_db] = override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.session_factory = factory
        yield client
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_get_user_destinations_endpoint_sorting(api_client):
    async with api_client.session_factory() as s:
        user = User(name="TestUser")
        s.add(user)
        await s.commit()
        uid = user.id

        s.add_all([
            UserDestinationStat(user_id=uid, destination_ip="1.1.1.1", domain="one.one", country_code="US", total_bytes=500, hit_count=10),
            UserDestinationStat(user_id=uid, destination_ip="8.8.8.8", domain="dns.google", country_code="US", total_bytes=2000, hit_count=2),
        ])
        await s.commit()

    # Sort by total_bytes desc
    res = await api_client.get(f"/api/v1/analytics/users/{uid}/destinations?sort_by=total_bytes&order=desc")
    assert res.status_code == 200
    data = res.json()["data"]
    assert len(data) == 2
    assert data[0]["domain"] == "dns.google"
    assert data[0]["total_bytes"] == 2000

    # Sort by hit_count desc
    res_hits = await api_client.get(f"/api/v1/analytics/users/{uid}/destinations?sort_by=hit_count&order=desc")
    assert res_hits.status_code == 200
    data_hits = res_hits.json()["data"]
    assert data_hits[0]["domain"] == "one.one"
    assert data_hits[0]["hit_count"] == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_connections_api.py -v`  
Expected: FAIL (`404 Not Found`)

- [ ] **Step 3: Implement connection schemas and API endpoints**

1. Create `backend/app/schemas/connection.py`:
   - `LiveConnectionItem`, `KillConnectionRequest`, `UserDestinationStatItem`.
2. Create `backend/app/api/v1/endpoints/connections.py`:
   - `GET /api/v1/connections`: fetches connections, enriches with Geo-IP and device/user mapping.
   - `POST /api/v1/connections/{connection_id}/kill`: catches `WriteGuardViolation` and returns HTTP 400.
3. Update `backend/app/api/v1/endpoints/analytics.py`:
   - Add `GET /users/{user_id}/destinations` with sorting (`total_bytes`, `bytes_in`, `bytes_out`, `hit_count`, `last_seen`).
4. Register router in `backend/app/api/v1/router.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_connections_api.py -v`  
Expected: PASS

- [ ] **Step 5: Run ruff check**

Run: `.venv/bin/ruff check backend/app/schemas/connection.py backend/app/api/v1/endpoints/connections.py backend/app/api/v1/endpoints/analytics.py tests/test_connections_api.py`  
Expected: `All checks passed!`

---

### Task 4: Frontend UI Components (`LiveConnectionsModal`, Filter Bars, Device Linking)

**Files:**
- Create: `frontend/src/components/LiveConnectionsModal.jsx`
- Create: `frontend/src/components/LiveConnectionsModal.test.jsx`
- Modify: `frontend/src/api/client.js`
- Modify: `frontend/src/components/Navbar.jsx`
- Modify: `frontend/src/components/UserCard.jsx`
- Modify: `frontend/src/components/DeviceModal.jsx`
- Modify: `frontend/src/components/TrafficHistoryModal.jsx`

**Interfaces:**
- Consumes:
  - `api.getLiveConnections(params)`
  - `api.killConnection(id, payload)`
  - `api.getUserDestinations(userId, params)`
- Produces:
  - `LiveConnectionsModal` component supporting:
    - Auto-polling (3s toggle)
    - Device filtering & search
    - Protocol filter chips
    - Connection kill action with confirmation
  - "Destinations & Domains" tab in `TrafficHistoryModal`

- [ ] **Step 1: Write frontend test for `LiveConnectionsModal`**

Create `frontend/src/components/LiveConnectionsModal.test.jsx`:
- Mock `api.getLiveConnections` and `api.killConnection`.
- Render `LiveConnectionsModal`.
- Verify connection list renders with protocol, source device, destination country flag, and rates.
- Test search filtering by domain / IP.
- Test clicking "Kill" triggers confirmation and API call.

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- frontend/src/components/LiveConnectionsModal.test.jsx`  
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `LiveConnectionsModal.jsx` and integrations**

1. Add API client helpers in `frontend/src/api/client.js`:
   - `getLiveConnections`, `killConnection`, `getUserDestinations`.
2. Create `frontend/src/components/LiveConnectionsModal.jsx`:
   - Full dialog layout, header metrics, auto-refresh toggle, search and protocol filters.
   - Kill button with inline confirmation.
3. Add entry points:
   - `Navbar.jsx`: "Connections" button in navigation.
   - `UserCard.jsx`: `Activity` icon on `DeviceRow`.
   - `DeviceModal.jsx`: "Live Connections" button.
4. Add "Destinations & Domains" tab to `TrafficHistoryModal.jsx` with clickable sort headers.

- [ ] **Step 4: Run frontend tests**

Run: `npm test -- frontend/src/components/LiveConnectionsModal.test.jsx`  
Expected: PASS

---

### Task 5: Full Regression & Integration Quality Gate

**Files:**
- Modify: `docs/LESSONS.md`

- [ ] **Step 1: Run full Python test suite**

Run: `.venv/bin/pytest`  
Expected: PASS (all 485+ tests pass)

- [ ] **Step 2: Run linter**

Run: `.venv/bin/ruff check backend/ tests/`  
Expected: `All checks passed!`

- [ ] **Step 3: Run frontend test suite**

Run: `npm test -- --watchAll=false`  
Expected: PASS (all test suites pass)

- [ ] **Step 4: Record lessons learned in `docs/LESSONS.md`**

Format: `[2026-09-04] Problem: ... -> Solution: ...`
