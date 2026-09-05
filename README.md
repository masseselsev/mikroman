# ⚡ MikroMan — MikroTik RouterOS Companion

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688.svg)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18.3-61DAFB.svg)](https://react.dev)
[![RouterOS](https://img.shields.io/badge/RouterOS-7.x-red.svg)](https://mikrotik.com)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**MikroMan** is a lightweight management, traffic accounting, and monitoring companion for MikroTik RouterOS gateways. It provides byte-accurate per-device accounting, automatic MAC-rotation tracking, parent-child bandwidth shaping, automated configuration backups with visual config drift, live connection observability, and safe firmware update orchestration.

---

## 🌟 Core Capabilities

* **📊 High-Precision Traffic Accounting**:
  * Measured via dedicated RouterOS firewall mangle `action=passthrough` counters, bypassing unreliable queue byte counters.
  * Accumulates traffic deltas against persisted baselines, surviving network outages and distinguishing hardware reboots.
  * Configurable monthly ISP billing cycle anchors with optional time-of-day boundary slicing.
  * Built-in tools to reconcile historical LAN-to-LAN overcounts.

* **🚦 Lockout Prevention & Write Guards**:
  * Pure validation layer (`guards.py`) intercepting all mutations before network packets are constructed.
  * Immune target protection: loopbacks, wildcards, management subnets, and container endpoints can never be throttled, blocked, or dropped.
  * Foreign resource isolation: configuration rules not created by MikroMan (`mikroman:`) are strictly protected from mutation or deletion.
  * Relational queue validation preventing invalid rate parameters and circular parentage.

* **🛡️ Multi-Router Management & Isolated Environments**:
  * Complete operational isolation: users, devices, queues, rollups, quotas, and timezone offsets exist strictly per-router.
  * Instant context switching in UI and WebSocket telemetry.
  * Seamless hardware swap workflow (`Change Router`) with data retention choices (`keep` vs `reset_hardware`).
  * Soft archive vs permanent purge router lifecycles.
  * Automated TLS/SSL certificate generation directly on RouterOS without modifying custom service ports.

* **🗂️ Config-Drift Backups & Visual Diff Viewer**:
  * Automated dual-pair exports: compact `.rsc` plain-text scripts and encrypted `.backup` recovery archives.
  * Zero-false-drift SHA-256 fingerprinting via volatile timestamp header stripping.
  * Interactive unified diff viewer with structured hunks, comparing historical revisions or live router state.
  * Flash write safety invariants: polling for stable file sizes and guaranteed temporary file cleanup sweeps.

* **⚡ Firmware & Update Intelligence**:
  * Multi-channel update tracking across `stable`, `long-term`, `testing`, and `development` channels.
  * RouterBOOT bootloader status tracking and one-click staging.
  * Bounded upstream changelog streaming client with in-memory caching and negative TTL.
  * Pre-upgrade safety invariant: mandatory automated pinned backup and strict router name confirmation gate before upgrade dispatch.
  * Autonomous 4-stage reboot reconnection state machine.

* **🌐 Real-Time Observability & Centralized Logs**:
  * Real-time `/ip/firewall/connection` tracker with device attribution and safe socket termination.
  * In-memory offline GeoIP engine resolving destination countries without external API dependencies.
  * Centralized terminal log viewer with regex event classification (auth, interface, DHCP, wireless, firewall, system).
  * 1-click RouterOS `/system/logging` topic management.

* **🤖 Dual-Mode Telegram Bot**:
  * Operates in both Long Polling (zero-config NAT) and Authenticated Webhook modes.
  * Proactive alerts for new device arrivals, CPU spikes, thermal thresholds, and WAN IP changes.
  * Interactive inline commands for gateway status, user limits, and pausing access.

---

## 📖 In-Depth Documentation (Wiki)

For detailed architectural specifications, algorithms, and configuration guides, refer to the [MikroMan Project Wiki](wiki/Home.md):

* [System Architecture & Design](wiki/Architecture-and-Design.md)
* [Traffic Accounting Engine Mechanics](wiki/Traffic-Accounting-Engine.md)
* [Lockout Prevention & Write Guards](wiki/Lockout-Prevention-Write-Guards.md)
* [Multi-Router Management & Lifecycle](wiki/Multi-Router-Management.md)
* [Backups, Config Drift & Visual Diff](wiki/Backups-and-Config-Drift.md)
* [Firmware & Update Intelligence](wiki/Firmware-and-Update-Intelligence.md)
* [Live Connections & Router Log Stream](wiki/Live-Connections-and-Router-Logs.md)
* [Deployment, Storage & Container Mode](wiki/Deployment-and-Container-Mode.md)

---

## 🚀 Quick Start (Docker)

Pre-built multi-architecture container images (`linux/amd64`, `linux/arm64`, `linux/arm/v7`) are automatically built and published to GitHub Container Registry upon every release.

### Option A: Run Pre-built Image (Recommended)
```bash
docker run -d \
  --name mikroman \
  --restart unless-stopped \
  -p 1928:1928 \
  -v mikroman_data:/data \
  ghcr.io/masseselsev/mikroman:latest
```

### Option B: Build & Run via Docker Compose
```bash
git clone https://github.com/masseselsev/mikroman.git
cd mikroman
docker compose up -d
```

### 2. Access Web Interface
Open **`http://localhost:1928`** in your browser. The first-run setup wizard will guide you through:
- Connecting to your MikroTik RouterOS gateway (REST API credentials).
- Configuring optional Telegram notifications.
- Selecting language (English / Russian) and theme (Dark / Light).

---

## 💻 Local Development Setup

### Backend (FastAPI)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --host 0.0.0.0 --port 1928 --reload
```

### Frontend (React + Vite)
```bash
cd frontend
npm install
npm run dev
```

---

## 🧪 Testing & Verification

Run the automated backend test suite:
```bash
.venv/bin/pytest -v
```

Run code formatting and linter checks:
```bash
.venv/bin/ruff check .
```

Run frontend unit tests and production build:
```bash
cd frontend
npm test
npm run build
```

---

## 📋 RouterOS Compatibility

MikroMan targets **RouterOS 7.x** (version 7.4 or higher recommended for REST API and container support).

| Architecture | Supported Devices | Notes |
|---|---|---|
| **ARM64** | RB5009, CCR2004, CCR2116, CCR2216, hAP ax², hAP ax³, cAP ax | Native container support |
| **ARM** | RB4011, RB3011, RB1100AHx4, hAP ac², hAP ac³ | Native container support |
| **MMIPS** | hEX (RB750Gr3), hEX S, wAP R | Remote management mode |
| **x86 / CHR** | Cloud Hosted Router, Custom PC x86_64 | Full capability |
| **TILE** | CCR1009, CCR1016, CCR1036, CCR1072 | Full capability |

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
