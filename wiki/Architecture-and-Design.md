# 🧱 System Architecture & Design

MikroMan is a lightweight companion system for MikroTik RouterOS gateways, providing traffic accounting, device discovery, bandwidth shaping, configuration backups, and real-time observability.

---

## 📐 High-Level Architecture

```
                      +-----------------------------------+
                      |         React 18 Dashboard        |
                      |  (Vite, Tailwind, Lucide, Vitest) |
                      +-----------------+-----------------+
                                        | HTTP / WebSocket
                                        v
                      +-----------------------------------+
                      |          FastAPI Backend          |
                      |  - Async REST API Endpoints       |
                      |  - Background Poller & Scrapers   |
                      |  - Pure Write Guards Layer        |
                      +--------+-----------------+--------+
                               |                 |
                SQLAlchemy     |                 | HTTPX Async
               (AsyncSession)  v                 v (Pooled REST)
               +---------------+---+     +-------+---------------+
               |    SQLite (WAL)   |     |    MikroTik RouterOS  |
               |  - Daily Rollups  |     |  - Mangle Counters    |
               |  - Devices & Users|     |  - Simple Queues      |
               |  - Stored Backups |     |  - System Health      |
               +-------------------+     +-----------------------+
```

---

## 🖥️ Backend Architecture

The backend is built with **Python 3.12** and **FastAPI**, structured modularly:

### 1. API Routing Layer (`backend/app/api/v1/`)
- Organized into dedicated resource routers:
  - `routers.py` — Router lifecycle, hardware swap, archive/restore, and SSL provisioning.
  - `traffic.py` & `analytics.py` — Traffic rollups, quota analytics, and accounting health checks.
  - `devices.py` & `users.py` — Device inventory, profile assignments, and speed limits.
  - `connections.py` — Real-time conntrack monitoring and socket management.
  - `backups.py` — Configuration backups, diff computation, and download handlers.
  - `firmware.py` — Package updates, RouterBOOT staging, and changelog proxy.
  - `logs.py` — Terminal log streaming, historical queries, and topic management.
  - `metrics.py` & `system.py` — System health telemetry and hardware metrics.

### 2. Service & Business Logic Layer (`backend/app/services/`)
- **Write Guards (`guards.py`)**: Pure, stateless validation layer enforcing non-destructive mutation invariants before building RouterOS requests.
- **RouterOS Client (`routeros/client.py`)**: Composed of specialized mixins:
  - `FirewallMixin`: Manages mangle accounting rules and blocking address lists.
  - `QueueMixin`: Manages hierarchical simple queues and bandwidth limits.
  - `BackupMixin`: Handles `.rsc` exports, `.backup` binary generation, and flash cleanup sweeps.
  - `FirmwareMixin`: Interfaces with `/system/package/update` and `/system/routerboard`.
  - `ConnectionsMixin`: Queries and manages `/ip/firewall/connection`.
- **Background Tasks**:
  - `traffic_accounting.py`: High-cadence counter differential accumulator.
  - `log_collector.py`: Periodic log scraper with deduplication.
  - `destination_collector.py`: Conntrack destination sampler.
  - `backup_scheduler.py`: Automated backup runner and milestone pruner.

---

## 💾 Database Architecture

Persistence is handled by **SQLite** using **SQLAlchemy 2.0 Async**:
- **WAL Journaling**: Runs in `journal_mode=WAL` with `synchronous=NORMAL` to allow concurrent background writing and API reads without locking.
- **Data Model**:
  - `routers`: Gateway connection parameters, status, serials, and comments.
  - `users` & `devices`: Grouped profiles and network hardware inventory.
  - `router_traffic_rollups` & `interface_traffic_rollups`: Daily historical volume aggregations.
  - `user_traffic_buckets` & `device_traffic_buckets`: 30-minute resolution intraday traffic metrics (14-day rolling retention).
  - `router_backups`: Snapshot records with normalized SHA-256 fingerprints.
  - `router_logs`: Categorized and severity-tagged log records.
  - `user_destination_stats`: Cumulative destination hits and byte counters.

---

## 🌐 Frontend Architecture

Built with **React 18** and bundled with **Vite**:
- **Design System**: Strict CSS variable tokens ensuring consistent spacing, radius, and typography across light and dark modes.
- **State & Communication**:
  - WebSocket telemetry hook (`useWebSocketTelemetry.js`) for streaming live throughput, CPU, RAM, and temperature.
  - REST client (`client.js`) handling API requests, error formatting, and authentication.
- **Internationalization**: Zero-dependency bilingual context (`I18nContext.jsx`) supporting English (`en`) and Russian (`ru`) with semantic length parity.
