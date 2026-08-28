# MikroTik Companion (MikroMan) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a lightweight, high-performance Docker companion application for MikroTik RouterOS 7.24+ featuring per-user traffic control, real-time bandwidth analytics, dual-mode Telegram bot, and a bilingual React SPA with native RouterOS Dark and Light themes.

**Architecture:** A FastAPI (Python 3.12+) async backend with SQLite/Alembic interfacing directly with RouterOS 7.24+ REST API for lease discovery, simple queue bandwidth tracking/limiting, and firewall-based internet pausing. A modern React (Vite 6) SPA served statically with real-time WebSocket telemetry and RouterOS color palettes, plus an `aiogram` Telegram bot running in polling/webhook mode.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.0 (async), Alembic, HTTPX, Aiogram 3.17+, React 18/19, Vite 6, Lucide-React, Docker.

## Global Constraints
- RouterOS Compatibility: RouterOS 7.24+ REST API `/rest/`
- Target Container Footprint: < 45MB RAM, < 80MB Image Size
- Themes: RouterOS Dark (WinBox slate/blue) & RouterOS Light (WebFig)
- Localization: Complete English (`en`) and Russian (`ru`) support
- Database Schema: Managed strictly with Alembic migrations and Pydantic models for serialization
- File Size Guideline: Keep individual files under 500 lines

---

### Task 1: Project Scaffolding, Configuration & Database ORM with Alembic

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/app/core/config.py`
- Create: `backend/app/db/session.py`
- Create: `backend/app/db/models.py`
- Create: `backend/app/schemas/common.py`
- Create: `backend/alembic.ini`
- Create: `backend/migrations/env.py`
- Create: `backend/migrations/script.py.mako`
- Create: `backend/migrations/versions/001_initial_schema.py`
- Create: `tests/test_db_models.py`

**Interfaces:**
- Produces: `Settings`, `get_db()`, `Base`, `User`, `Device`, `TrafficRollup`, `AppSetting`, `AlertLog`

- [ ] **Step 1: Write the failing database models and session test**
```python
# tests/test_db_models.py
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from backend.app.db.models import Base, User, Device, TrafficRollup, AppSetting

@pytest.mark.asyncio
async def test_create_user_and_device():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        user = User(name="Alex", speed_limit="50M", is_paused=False, priority=1)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        
        device = Device(user_id=user.id, mac_address="AA:BB:CC:DD:EE:FF", ip_address="192.168.88.100", hostname="Alex-MacBook")
        session.add(device)
        await session.commit()
        await session.refresh(device)
        
        assert user.id is not None
        assert device.id is not None
        assert device.user_id == user.id
        assert device.mac_address == "AA:BB:CC:DD:EE:FF"
    await engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**
Run: `python3 -m pytest tests/test_db_models.py -v`  
Expected: FAIL with ModuleNotFoundError or import error

- [ ] **Step 3: Implement dependencies, config, models, and Alembic migration**
Create `backend/requirements.txt`, `backend/app/core/config.py`, `backend/app/db/session.py`, `backend/app/db/models.py`, `backend/alembic.ini`, and `backend/migrations/...`

- [ ] **Step 4: Run test to verify it passes**
Run: `python3 -m pytest tests/test_db_models.py -v`  
Expected: PASS

- [ ] **Step 5: Commit documentation & internal plan updates**

---

### Task 2: RouterOS Async REST Client & Device Discovery Engine

**Files:**
- Create: `backend/app/services/routeros.py`
- Create: `backend/app/schemas/routeros.py`
- Create: `backend/app/services/device_manager.py`
- Create: `tests/test_routeros_client.py`

**Interfaces:**
- Consumes: `Settings`, `User`, `Device`
- Produces: `RouterOSClient`, `DeviceManager`, `DHCPLeaseDTO`, `RouterHealthDTO`, `InterfaceDTO`

- [ ] **Step 1: Write test for RouterOS REST Client with mocked HTTP responses**
```python
# tests/test_routeros_client.py
import pytest
import respx
import httpx
from backend.app.services.routeros import RouterOSClient
from backend.app.core.config import Settings

@pytest.mark.asyncio
async def test_fetch_system_resource_and_leases():
    settings = Settings(ROUTEROS_HOST="192.168.88.1", ROUTEROS_USER="admin", ROUTEROS_PASSWORD="password")
    client = RouterOSClient(settings)
    
    with respx.mock(base_url="https://192.168.88.1/rest") as respx_mock:
        respx_mock.get("/system/resource").respond(
            200, json={"board-name": "hAP ax3", "version": "7.24", "cpu-load": "12", "free-memory": "512000000", "total-memory": "1024000000", "uptime": "1d 04:20:00"}
        )
        respx_mock.get("/ip/dhcp-server/lease").respond(
            200, json=[{".id": "*1", "address": "192.168.88.50", "mac-address": "00:11:22:33:44:55", "host-name": "Phone", "status": "bound"}]
        )
        
        resource = await client.get_system_resource()
        assert resource.board_name == "hAP ax3"
        assert resource.cpu_load == 12
        
        leases = await client.get_dhcp_leases()
        assert len(leases) == 1
        assert leases[0].mac_address == "00:11:22:33:44:55"
```

- [ ] **Step 2: Run test to verify failure**
Run: `python3 -m pytest tests/test_routeros_client.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement RouterOSClient and DeviceManager**
Implement `/rest/` endpoint handlers:
- `get_system_resource()`
- `get_system_health()`
- `get_dhcp_leases()`
- `get_arp_table()`
- `get_wifi_registrations()`
- `get_interfaces()`

- [ ] **Step 4: Run test to verify it passes**
Run: `python3 -m pytest tests/test_routeros_client.py -v`  
Expected: PASS

---

### Task 3: Simple Queue Traffic Control & Firewall Pause/Block Engine

**Files:**
- Create: `backend/app/services/traffic_controller.py`
- Create: `backend/app/schemas/traffic.py`
- Create: `tests/test_traffic_controller.py`

**Interfaces:**
- Consumes: `RouterOSClient`, `User`, `Device`
- Produces: `TrafficController`, `QueueStatsDTO`, `UserTrafficDTO`

- [ ] **Step 1: Write test for Queue Synchronization and Rate Limiting**
```python
# tests/test_traffic_controller.py
import pytest
import respx
from backend.app.services.routeros import RouterOSClient
from backend.app.services.traffic_controller import TrafficController
from backend.app.core.config import Settings

@pytest.mark.asyncio
async def test_sync_user_queue_and_pause():
    settings = Settings(ROUTEROS_HOST="192.168.88.1", ROUTEROS_USER="admin", ROUTEROS_PASSWORD="password")
    client = RouterOSClient(settings)
    controller = TrafficController(client)
    
    with respx.mock(base_url="https://192.168.88.1/rest") as respx_mock:
        # Check existing queues
        respx_mock.get("/queue/simple").respond(200, json=[])
        # Create new queue
        respx_mock.put("/queue/simple").respond(200, json={".id": "*Q1"})
        
        queue_id = await controller.sync_user_queue(user_id=1, user_name="Alex", ip_addresses=["192.168.88.50"], speed_limit="20M/50M")
        assert queue_id == "*Q1"
```

- [ ] **Step 2: Run test to verify failure**
Run: `python3 -m pytest tests/test_traffic_controller.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement TrafficController**
Implement:
- `sync_user_queue(user_id, user_name, ip_addresses, speed_limit)`
- `set_user_speed_limit(user_id, speed_limit)`
- `pause_user_internet(user_id, ip_addresses)`
- `resume_user_internet(user_id, ip_addresses)`
- `get_realtime_traffic_stats()`

- [ ] **Step 4: Run test to verify it passes**
Run: `python3 -m pytest tests/test_traffic_controller.py -v`  
Expected: PASS

---

### Task 4: FastAPI REST API & WebSocket Real-Time Telemetry Hub

**Files:**
- Create: `backend/app/schemas/user.py`
- Create: `backend/app/schemas/device.py`
- Create: `backend/app/api/v1/endpoints/users.py`
- Create: `backend/app/api/v1/endpoints/devices.py`
- Create: `backend/app/api/v1/endpoints/system.py`
- Create: `backend/app/api/v1/endpoints/ws.py`
- Create: `backend/app/api/v1/router.py`
- Create: `backend/app/main.py`
- Create: `tests/test_api_endpoints.py`

**Interfaces:**
- Consumes: All services, DB models, schemas
- Produces: FastAPI Application with OpenAPI routes & WebSocket `/ws/telemetry`

- [ ] **Step 1: Write integration tests for API endpoints**
```python
# tests/test_api_endpoints.py
import pytest
from httpx import AsyncClient, ASGITransport
from backend.app.main import app

@pytest.mark.asyncio
async def test_get_system_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/v1/system/status")
        assert response.status_code in [200, 503] # 503 if router not configured yet
```

- [ ] **Step 2: Run test to verify failure**
Run: `python3 -m pytest tests/test_api_endpoints.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement API endpoints, routers, and WebSocket streaming manager**

- [ ] **Step 4: Run test to verify it passes**
Run: `python3 -m pytest tests/test_api_endpoints.py -v`  
Expected: PASS

---

### Task 5: Bilingual Telegram Bot Engine (Polling & Webhook Support)

**Files:**
- Create: `backend/app/core/i18n.py`
- Create: `backend/app/services/telegram_bot.py`
- Create: `backend/app/api/v1/endpoints/telegram.py`
- Create: `tests/test_telegram_bot.py`

**Interfaces:**
- Consumes: `TrafficController`, `RouterOSClient`, `Settings`
- Produces: `TelegramBotService`, Webhook Endpoint `/api/v1/telegram/webhook`

- [ ] **Step 1: Write test for Telegram Bot localization and commands**
```python
# tests/test_telegram_bot.py
import pytest
from backend.app.core.i18n import get_text

def test_i18n_translations():
    assert "Router Status" in get_text("status_title", lang="en")
    assert "Статус роутера" in get_text("status_title", lang="ru")
    assert "Pause" in get_text("btn_pause", lang="en")
    assert "Пауза" in get_text("btn_pause", lang="ru")
```

- [ ] **Step 2: Run test to verify failure**
Run: `python3 -m pytest tests/test_telegram_bot.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement i18n dictionary and Aiogram bot dispatcher with commands `/status`, `/users`, `/pause`, `/limit`, `/reboot` and webhook receiver**

- [ ] **Step 4: Run test to verify it passes**
Run: `python3 -m pytest tests/test_telegram_bot.py -v`  
Expected: PASS

---

### Task 6: React Frontend Scaffolding, RouterOS Dark/Light Theme & i18n System

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.js`
- Create: `frontend/index.html`
- Create: `frontend/src/index.css`
- Create: `frontend/src/i18n/translations.js`
- Create: `frontend/src/context/ThemeContext.jsx`
- Create: `frontend/src/context/I18nContext.jsx`
- Create: `frontend/src/App.jsx`
- Create: `frontend/src/main.jsx`

- [ ] **Step 1: Set up Vite React project and verify initial build**
Run: `cd frontend && npm install && npm run build`  
Expected: Build succeeds with output in `frontend/dist/`

- [ ] **Step 2: Implement RouterOS Dark/Light CSS design system tokens and i18n translation context**

- [ ] **Step 3: Verify theme switching and language toggling in UI**

---

### Task 7: React Dashboard Components (Telemetry Bar, Users & Traffic, Device Inbox, Settings)

**Files:**
- Create: `frontend/src/components/Navbar.jsx`
- Create: `frontend/src/components/TelemetryBar.jsx`
- Create: `frontend/src/components/UserCard.jsx`
- Create: `frontend/src/components/Speedometer.jsx`
- Create: `frontend/src/components/DeviceInbox.jsx`
- Create: `frontend/src/components/SettingsModal.jsx`
- Create: `frontend/src/hooks/useWebSocketTelemetry.js`
- Create: `frontend/src/api/client.js`

- [ ] **Step 1: Implement components with live WebSocket hooks and API client**
- [ ] **Step 2: Verify responsive design and all interactive controls (Pause, Limit, Assign Device)**
- [ ] **Step 3: Verify build bundle size is minimal (< 400KB gzip)**

---

### Task 8: Multi-Stage Dockerfile & RouterOS Container Deployment Setup

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `scripts/setup_ros_container.rsc`
- Create: `.env.example`
- Create: `README.md`

- [ ] **Step 1: Create multi-stage Dockerfile (Node Vite build -> Python Alpine runtime)**
- [ ] **Step 2: Build Docker image and verify image size < 80MB**
Run: `docker build -t mikroman:latest .`
- [ ] **Step 3: Run container smoke test verifying FastAPI + React static bundle serve**
Run: `docker run --rm -d -p 8000:8000 --name mikroman-test mikroman:latest`
Verify: `curl -f http://localhost:8000/api/v1/system/status || true`
Cleanup: `docker stop mikroman-test`
- [ ] **Step 4: Document RouterOS 7.24+ `/container` setup script and environment variables**
