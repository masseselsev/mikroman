# Multi-Router Support & First-Run Setup Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove mandatory `.env` requirements, add a zero-config First-Run Setup Wizard on the web frontend, and implement full multi-router architecture allowing users to connect, monitor, and manage multiple MikroTik routers concurrently or switch between them seamlessly.

**Architecture:** 
1. Database: Add `Router` model in SQLAlchemy and link `Device`, `TrafficRollup`, `AlertLog` to `router_id` via Alembic migration.
2. Backend Services: Refactor `RouterOSClient` into a dynamic `RouterOSClientManager` managing multiple router instances, background polling per router, and `/api/v1/routers` REST CRUD endpoints with connection testing.
3. Bot & WebSocket: Update Telegram Bot (`/routers`, router selection keyboards) and WebSocket telemetry to stream per-router stats with active router switching.
4. Frontend: Implement `SetupWizard.jsx` for zero-config onboarding, `RouterSelector.jsx` in the Navbar, and router management in `SettingsModal.jsx`.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Pydantic v2, Aiogram 3, React 18, Vite 6, Lucide icons, Vanilla CSS.

## Global Constraints
- Only use English in code, comments, and messages.
- Always use Alembic migrations for DB changes.
- Always use Pydantic models for request/response serialization.
- Keep files under 500 lines where possible.
- 100% test pass rate on verification (`pytest -v` and `ruff check`).

---

### Task 1: Database Migration for Multi-Router Architecture
**Files:**
- Modify: `backend/app/db/models.py`
- Create: `backend/migrations/versions/002_multi_router_support.py`
- Test: `tests/test_alembic_migrations.py`

**Interfaces:**
- Produces: `Router` model with `id`, `name`, `host`, `port`, `use_ssl`, `ssl_verify`, `username`, `password`, `is_active`, `is_default`, `created_at`, `updated_at`.
- Updates: `Device.router_id`, `TrafficRollup.router_id`, `AlertLog.router_id` foreign keys with nullable=True for backwards compatibility.

- [x] **Step 1: Write the failing test for Router model and migration**
- [x] **Step 2: Run test to verify it fails**
- [x] **Step 3: Add `Router` model and Alembic migration `002_multi_router_support.py`**
- [x] **Step 4: Run test to verify it passes**

---

### Task 2: Multi-Router REST API Schemas & Client Manager
**Files:**
- Create: `backend/app/schemas/router.py`
- Create: `backend/app/services/router_manager.py`
- Modify: `backend/app/services/device_manager.py`
- Modify: `backend/app/services/traffic_controller.py`
- Test: `tests/test_router_manager.py`

**Interfaces:**
- Produces: `RouterCreate`, `RouterUpdate`, `RouterResponse`, `RouterTestConnectionRequest`, `RouterTestConnectionResponse` Pydantic schemas.
- Produces: `RouterManager` class providing `get_client(router_id)`, `test_connection(credentials)`, `get_all_active_clients()`, and connection pooling.

- [x] **Step 1: Write test for Router schemas, RouterManager, and connection testing**
- [x] **Step 2: Run test to verify failure**
- [x] **Step 3: Implement `RouterManager` and schemas**
- [x] **Step 4: Run test to verify pass**

---

### Task 3: Multi-Router REST Endpoints & WebSocket Telemetry
**Files:**
- Create: `backend/app/api/v1/endpoints/routers.py`
- Modify: `backend/app/api/v1/router.py`
- Modify: `backend/app/api/v1/endpoints/ws.py`
- Modify: `backend/app/main.py`
- Test: `tests/test_routers_api.py`

**Interfaces:**
- `GET /api/v1/routers`: List all configured routers.
- `POST /api/v1/routers`: Add a new router.
- `GET /api/v1/routers/{id}`: Get router details.
- `PUT /api/v1/routers/{id}`: Update router credentials/settings.
- `DELETE /api/v1/routers/{id}`: Remove router.
- `POST /api/v1/routers/test`: Live connection test endpoint without saving.
- `POST /api/v1/routers/{id}/activate`: Set router as active/default for telemetry and traffic commands.
- WebSocket `/ws/telemetry?router_id={id}`: Dynamically stream stats for the requested or default active router.

- [x] **Step 1: Write integration tests for router endpoints**
- [x] **Step 2: Implement `/api/v1/routers` endpoints and update `ws.py`**
- [x] **Step 3: Update `main.py` background discovery for multi-router polling**
- [x] **Step 4: Run tests to verify pass**

---

### Task 4: Telegram Bot Multi-Router Support
**Files:**
- Modify: `backend/app/services/telegram_bot.py`
- Modify: `backend/app/core/i18n.py`
- Test: `tests/test_telegram_bot.py`

**Interfaces:**
- `/routers` command: List all configured routers with active status indicators.
- Inline button router switching callback: `router:select:{id}`.

- [x] **Step 1: Write tests for bot router switching**
- [x] **Step 2: Implement `/routers` handler and router callback in `TelegramBotService`**
- [x] **Step 3: Add Russian & English translation strings for multi-router commands**
- [x] **Step 4: Run tests to verify pass**

---

### Task 5: Frontend First-Run Setup Wizard & Router Selector
**Files:**
- Modify: `frontend/src/api/client.js`
- Create: `frontend/src/components/SetupWizard.jsx`
- Create: `frontend/src/components/RouterSelector.jsx`
- Modify: `frontend/src/components/Navbar.jsx`
- Modify: `frontend/src/components/SettingsModal.jsx`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/i18n/translations.js`

**Interfaces:**
- `SetupWizard.jsx`: 3-step first-run wizard (Router Connection -> Telegram Bot -> Theme/Language Preferences).
- `RouterSelector.jsx`: Dropdown in navbar displaying active router and allowing fast switching between routers.
- `SettingsModal.jsx`: "Routers" tab for full CRUD, connection testing, and switching active routers.

- [x] **Step 1: Add router endpoints to `api/client.js` and i18n keys**
- [x] **Step 2: Create `SetupWizard.jsx` and `RouterSelector.jsx`**
- [x] **Step 3: Update `Navbar.jsx`, `SettingsModal.jsx`, and `App.jsx`**
- [x] **Step 4: Build and test frontend (`npm run build`)**

---

### Task 6: End-to-End Verification & Container Validation
**Files:**
- Modify: `README.md`
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Test: Full pytest suite & Docker smoke test

- [ ] **Step 1: Run full pytest suite (`pytest -v`)**
- [ ] **Step 2: Run linter check (`ruff check backend tests`)**
- [ ] **Step 3: Test local server with zero `.env` values and verify Setup Wizard detection**
