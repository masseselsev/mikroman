# Live Router Log Stream, Smart Event Highlighting & Global Topic Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a real-time terminal log viewer for RouterOS logs with smart pattern highlighting (Auth failures, Link flapping, DHCP, Wireless, Firewall drops), optional background scraping into SQLite with retention pruning, and direct management of RouterOS `/system/logging` topics.

**Architecture:**
- Backend: Deterministic regex event classifier (`log_classifier.py`). Extended `SystemMixin` with `/system/logging` management and WriteGuard foreign-rule protection. Periodic background log scraper worker (`log_collector.py`) controlled by configurable setting `log_scraping_enabled`.
- Persistence: SQLite table `RouterLog` storing timestamped, categorized, severity-tagged router logs.
- API: `GET /api/v1/logs` (supporting both `source=live` direct from router and `source=db` for historical queries), `GET /api/v1/logs/stats`, and `/api/v1/logs/rules` for RouterOS topic management.
- Frontend: Monospace terminal console `RouterLogsModal.jsx` with sticky auto-scroll, category filter chips (`[All]`, `[Wireless]`, `[Security]`, `[Interfaces]`, `[DHCP]`, `[Firewall]`, `[Errors]`), dynamic topic tags, Copy/Export buttons, and a Logging Topic Config drawer with 1-click presets.

**Tech Stack:** FastAPI, SQLAlchemy (Async), Alembic, Pydantic v2, RouterOS REST API, React 18, Lucide Icons, Vitest / React Testing Library.

## Global Constraints
- RouterOS performance: Fetch logs using `.proplist=.id,time,topics,message`.
- WriteGuard safety: Rules not tagged with `mikroman:log:` cannot be removed or modified.
- High-efficiency scraping: Deduplicate by `(router_id, external_id, timestamp)` to avoid duplicate rows.
- Full test pass: `.venv/bin/pytest` and `npm test`.
- Linter clean: `.venv/bin/ruff check`.

---

### Task 1: Smart Event Classifier & Database Models

**Files:**
- Create: `backend/app/services/log_classifier.py`
- Modify: `backend/app/db/models.py`
- Create: `backend/migrations/versions/021_router_logs.py`
- Create: `tests/test_router_logs_classifier_and_models.py`

**Interfaces:**
- Produces:
  - `classify_log_entry(topics: str, message: str) -> Tuple[str, str]` (severity: info/warning/error/critical, category: auth/interface/dhcp/wireless/firewall/system)
  - `RouterLog` model (`id`, `router_id`, `external_id`, `timestamp`, `topics`, `message`, `severity`, `category`, `created_at`)

- [ ] **Step 1: Write the failing test for log classifier and RouterLog model**

Create `tests/test_router_logs_classifier_and_models.py`:
```python
import pytest
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

from backend.app.db.models import Base, RouterLog, Router
from backend.app.services.log_classifier import classify_log_entry


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_router_logs_classifier_and_models.py -v`  
Expected: FAIL (`ModuleNotFoundError: No module named 'backend.app.services.log_classifier'`)

- [ ] **Step 3: Implement `log_classifier.py` and `RouterLog` model**

1. Create `backend/app/services/log_classifier.py` with regex pattern recognition for severity and category.
2. Update `backend/app/db/models.py` with `RouterLog`.
3. Create Alembic migration `backend/migrations/versions/021_router_logs.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_router_logs_classifier_and_models.py -v`  
Expected: PASS

- [ ] **Step 5: Run ruff check**

Run: `.venv/bin/ruff check backend/app/services/log_classifier.py backend/app/db/models.py tests/test_router_logs_classifier_and_models.py`  
Expected: `All checks passed!`

---

### Task 2: RouterOS Logging Rules & Scraper Service

**Files:**
- Modify: `backend/app/services/routeros/system.py`
- Create: `backend/app/services/log_collector.py`
- Create: `tests/test_routeros_logging_rules.py`

**Interfaces:**
- Consumes:
  - `RouterOSClient.get_logging_rules()`, `add_logging_rule()`, `remove_logging_rule()`
  - `guard_foreign_resources` from `backend.app.services.guards`
- Produces:
  - `SystemMixin.remove_logging_rule(rule_id: str, comment: Optional[str] = None) -> bool`
  - `LogCollector.collect_logs_for_router(session, router_id, client) -> int`

- [ ] **Step 1: Write failing test for logging rules and collector**

Create `tests/test_routeros_logging_rules.py`:
- Test `remove_logging_rule` refuses deleting foreign rule (missing `mikroman:log:` prefix).
- Test `add_logging_rule` prefixes comment with `mikroman:log:<topic>`.
- Test `collect_logs_for_router` deduplication.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_routeros_logging_rules.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement `remove_logging_rule` and `log_collector.py`**

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_routeros_logging_rules.py -v`  
Expected: PASS

- [ ] **Step 5: Run ruff check**

Run: `.venv/bin/ruff check backend/app/services/routeros/system.py backend/app/services/log_collector.py tests/test_routeros_logging_rules.py`  
Expected: `All checks passed!`

---

### Task 3: Backend API Endpoints & Schemas

**Files:**
- Create: `backend/app/schemas/log.py`
- Create: `backend/app/api/v1/endpoints/logs.py`
- Modify: `backend/app/api/v1/router.py`
- Create: `tests/test_logs_api.py`

**Interfaces:**
- Produces:
  - `GET /api/v1/logs`
  - `GET /api/v1/logs/stats`
  - `GET /api/v1/logs/rules`
  - `POST /api/v1/logs/rules`
  - `DELETE /api/v1/logs/rules/{rule_id}`
  - `DELETE /api/v1/logs`

- [ ] **Step 1: Write failing test for logs API endpoints**

Create `tests/test_logs_api.py`:
- Mock `require_client` returning sample logs and rules.
- Test `GET /api/v1/logs?source=live` and `source=db`.
- Test `GET /api/v1/logs?category=auth` and `severity=error`.
- Test `GET /api/v1/logs/stats`.
- Test `POST /api/v1/logs/rules` and `DELETE /api/v1/logs/rules/{id}`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_logs_api.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement schemas and endpoints**

1. Create `backend/app/schemas/log.py`.
2. Create `backend/app/api/v1/endpoints/logs.py`.
3. Register in `backend/app/api/v1/router.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_logs_api.py -v`  
Expected: PASS

- [ ] **Step 5: Run ruff check**

Run: `.venv/bin/ruff check backend/app/schemas/log.py backend/app/api/v1/endpoints/logs.py tests/test_logs_api.py`  
Expected: `All checks passed!`

---

### Task 4: Frontend UI (`RouterLogsModal`, Terminal Stream, Category Filters & Topic Management)

**Files:**
- Create: `frontend/src/components/RouterLogsModal.jsx`
- Create: `frontend/src/components/RouterLogsModal.test.jsx`
- Modify: `frontend/src/api/client.js`
- Modify: `frontend/src/components/Navbar.jsx`
- Modify: `frontend/src/components/SettingsModal.jsx`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/i18n/translations.js`

**Interfaces:**
- Consumes:
  - `api.getRouterLogs(params)`
  - `api.getRouterLogStats(routerId)`
  - `api.getLoggingRules(routerId)`
  - `api.addLoggingRule(routerId, payload)`
  - `api.deleteLoggingRule(routerId, ruleId)`
- Produces:
  - `RouterLogsModal` component supporting:
    - Live stream (2.5s polling) and DB history views
    - Sticky auto-scroll with freeze-on-scroll-up
    - Category pills (`All`, `Wireless`, `Security`, `Interfaces`, `DHCP`, `Firewall`, `Errors`)
    - Dynamic topic pills
    - Copy & Export actions
    - RouterOS logging rules config drawer with 1-click presets

- [ ] **Step 1: Write frontend test for `RouterLogsModal`**

Create `frontend/src/components/RouterLogsModal.test.jsx`.

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- src/components/RouterLogsModal.test.jsx`  
Expected: FAIL

- [ ] **Step 3: Implement `RouterLogsModal.jsx`, API methods, translations, and entry points**

- [ ] **Step 4: Run frontend tests**

Run: `npm test -- src/components/RouterLogsModal.test.jsx`  
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

Run: `npm test`  
Expected: PASS (all 22+ suites pass)

- [ ] **Step 4: Record lessons learned in `docs/LESSONS.md`**
