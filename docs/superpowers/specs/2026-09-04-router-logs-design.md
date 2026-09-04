# Live Router Log Stream, Smart Event Highlighting & Global Topic Management Design Spec

**Date:** 2026-09-04  
**Status:** Approved  
**Author:** Pair Programmer Agent & Operator  

---

## 1. Overview & Goals

This specification defines the architecture, data models, wire protocols, API endpoints, classification rules, and UI components for:
1. **Live Router Log Streaming**: High-contrast, monospace terminal log viewer streaming active log entries from RouterOS (`/log`), with sticky auto-scroll, freeze-on-scroll-up, and Copy/Export tools.
2. **Smart Event Classification & Highlighting**: Deterministic pattern matching categorizing and badging high-value events:
   - 🚨 `AUTH FAIL`: Login failures, brute-force attempts via SSH, API, WebFig, or WinBox.
   - 🔌 `LINK EVENT`: Interface link down / link up flapping.
   - ⚡ `DHCP`: Lease renewals, grants, pool exhaustion, conflict alerts.
   - 📶 `WIRELESS`: Client associations, disassociations, signal anomalies.
   - 🛡️ `FIREWALL`: Input/forward chain packet drops.
   - ❌ `ERROR/CRITICAL`: System-level failures and crash alerts.
3. **Background Log Scraping & Persistence**: Optional, configurable continuous background scraping across all active routers storing historical logs in SQLite (`RouterLog`), with retention pruning.
4. **Global Logging Topic Management (`/system/logging`)**: Direct UI management of RouterOS logging rules, allowing operators to activate logging for topics RouterOS ignores by default (e.g. `wireless`, `firewall`, `wireguard`, `dns`, `script`) with Write Guard safety.

---

## 2. Architecture & Components

```
+-------------------------------------------------------------+
|                      React Frontend                         |
|  +-------------------------+   +--------------------------+  |
|  |    RouterLogsModal      |   |   LoggingTopicManager    |  |
|  | (Live Stream, Category  |   | (RouterOS /system/log    |  |
|  |  Chips, Smart Badges)   |   |  Presets & Custom Topics)|  |
|  +-------------------------+   +--------------------------+  |
+------------------------------+------------------------------+
                               | REST API
+------------------------------v------------------------------+
|                    FastAPI Backend                          |
|  +-------------------------------------------------------+  |
|  | Endpoints:                                            |  |
|  |  * GET    /api/v1/logs                                |  |
|  |  * GET    /api/v1/logs/stats                          |  |
|  |  * GET    /api/v1/logs/rules                          |  |
|  |  * POST   /api/v1/logs/rules                          |  |
|  |  * DELETE /api/v1/logs/rules/{id}                     |  |
|  +-------------------------------------------------------+  |
|         |                     |                     |       |
|  +------v------+       +------v------+       +------v-----+ |
|  | Classifier  |       | Background  |       |   SQLite   | |
|  | (Severity & |       | Log Scraper |       | (RouterLog | |
|  |  Category)  |       | Worker      |       |  Table)    | |
|  +-------------+       +-------------+       +------------+ |
|         |                                           |       |
|  +------v-------------------------------------------v-----+ |
|  |         RouterOSClient (SystemMixin / Log API)         | |
|  +--------------------------------------------------------+ |
+------------------------------+------------------------------+
                               | REST (.proplist)
+------------------------------v------------------------------+
|                     MikroTik RouterOS                       |
|           /log  ·  /system/logging  ·  REST API             |
+-------------------------------------------------------------+
```

---

## 3. Detailed Specifications

### 3.1 Persistent Log Table & Settings (`backend/app/db/models.py`)
- New table `RouterLog`:
  - `id`: Integer primary key, autoincrement.
  - `router_id`: Integer, ForeignKey(`routers.id`, ondelete="CASCADE"), nullable=False, index=True.
  - `external_id`: String(32), nullable=True (RouterOS internal `.id`, e.g. `*1`, `*1A`).
  - `timestamp`: DateTime, nullable=False, index=True.
  - `topics`: String(255), nullable=False, index=True (e.g. `"system,error,critical"`).
  - `message`: Text, nullable=False.
  - `severity`: String(16), default=`"info"`, index=True (`"info"`, `"warning"`, `"error"`, `"critical"`).
  - `category`: String(32), default=`"system"`, index=True (`"auth"`, `"interface"`, `"dhcp"`, `"wireless"`, `"firewall"`, `"system"`).
  - `created_at`: DateTime, default=func.now().
- Composite indexes: `(router_id, timestamp DESC)` and `(router_id, severity)`.
- Settings additions:
  - `log_scraping_enabled`: Boolean, default=`True`.
  - `log_retention_days`: Integer, default=`14`.

### 3.2 Smart Event Classifier (`backend/app/services/log_classifier.py`)
- **Severity Mapping**:
  - `critical`: Topics or message containing `"critical"`.
  - `error`: Topics or message containing `"error"` or `"failure"`.
  - `warning`: Topics containing `"warning"`.
  - `info`: Default operational log lines.
- **Category Mapping**:
  - `auth`: Topics containing `"account"` or message matching `login failure`, `logged in`, `logged out`, `authentication failed`.
  - `interface`: Topics containing `"interface"` or message matching `link down`, `link up`.
  - `dhcp`: Topics containing `"dhcp"` or message matching `assigned`, `deassigned`, `offered`, `lease`.
  - `wireless`: Topics containing `"wireless"` or `"capsman"` or message matching `associated`, `disassociated`, `roamed`.
  - `firewall`: Topics containing `"firewall"` or `"raw"`.
  - `system`: Fallback category.

### 3.3 RouterOS Logging Topic Management
- Integrated into `SystemMixin`:
  - `get_logging_rules() -> List[Dict[str, Any]]`: Returns configured `/system/logging` rules.
  - `add_logging_rule(topics: str, action: str = "memory") -> str`: Adds a logging rule with comment `mikroman:log:<topics>`.
  - `remove_logging_rule(rule_id: str, comment: Optional[str] = None) -> bool`: Deletes logging rule. Enforces WriteGuard: rules not starting with `mikroman:` cannot be deleted.

### 3.4 API Endpoints (`backend/app/api/v1/endpoints/logs.py`)
- `GET /api/v1/logs`:
  - Query params: `router_id` (optional), `source` (`"db"` or `"live"`), `severity`, `category`, `search`, `limit` (default 250, max 1000).
  - Returns `APIResponse[List[RouterLogItem]]`.
- `GET /api/v1/logs/stats`:
  - Returns count of warnings, errors, and auth failures for the router in the past 24 hours.
- `GET /api/v1/logs/rules`:
  - Returns configured `/system/logging` rules on the router.
- `POST /api/v1/logs/rules`:
  - Adds a new topic rule to `/system/logging` (`{"topics": "wireless", "action": "memory"}`).
- `DELETE /api/v1/logs/rules/{rule_id}`:
  - Deletes a logging rule (guarded by WriteGuard).

### 3.5 Frontend UI Components
- **`RouterLogsModal.jsx`**:
  - Full-screen modal with monospace terminal viewport.
  - Source switch: `[Live Stream (2.5s)]` vs `[Stored History (DB)]`.
  - Stream controls: Pause/Play toggle, manual refresh, auto-scroll freeze indicator.
  - Category Pills: `[All]`, `[🚨 Security / Auth]`, `[🔌 Interfaces]`, `[⚡ DHCP]`, `[📶 Wireless]`, `[🛡️ Firewall]`, `[⚠️ Errors]`.
  - Dynamic topic pills extracted from current entries.
  - Actions: "Copy Logs" & "Export .log".
  - "Configure Router Logging" drawer to manage `/system/logging` topics with 1-click presets (`Wireless`, `Firewall`, `WireGuard`, `DNS`).
- **`Navbar.jsx`**:
  - Adds "Logs" button with terminal icon and error badge.
- **`SettingsModal.jsx`**:
  - Adds toggle for "Background Log Scraping" and "Retention Days".

---

## 4. Verification Plan

### 4.1 Automated Tests
1. **Classifier & Models (`tests/test_router_logs.py`)**:
   - Verify pattern matching for auth, interface, dhcp, wireless, firewall, and system topics.
   - Verify `RouterLog` database CRUD and retention pruning.
2. **RouterOS Client & Logging Rules**:
   - Verify `get_logging_rules` and `add_logging_rule`.
   - Verify Write Guard blocks deletion of foreign/default rules.
3. **API Endpoints**:
   - Verify `GET /api/v1/logs` with `source=live` and `source=db`, category filtering, search, and stats.
   - Verify `POST /api/v1/logs/rules` and `DELETE /api/v1/logs/rules/{id}`.
4. **Frontend Component Tests (`frontend/src/components/RouterLogsModal.test.jsx`)**:
   - Render terminal stream, verify badges, test category filter chips, search filtering, and copy/export.
5. **Regression Gate**:
   - Run `.venv/bin/pytest`.
   - Run `.venv/bin/ruff check`.
   - Run `npm test`.
