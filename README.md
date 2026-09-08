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

* **📈 Peak-Preserving Hardware & Bandwidth Graphs**:
  * Router Health tab charts interface RX/TX, CPU load, RAM and board temperature/voltage over 1 h / 6 h / 24 h / 7 d / 30 d ranges.
  * Every display bucket carries its mean *and* its worst case, so a burst shorter than the bucket is still on the chart: solid line = average, shaded band = peak (min–max on the voltage view).
  * Downsampling runs inside SQLite (`strftime` bucket grid, two-level grouping), so a 30-day window returns ~180 rows instead of pulling a million raw samples through the ORM.
  * Rates are summed per sample before the peak is taken, so a multi-interface selection cannot invent a combined spike out of two unrelated moments.
  * Outages are drawn as blanks, not ramps: `/api/v1/metrics/{system,interfaces}` report `bucket_seconds`, and any gap wider than 2.5 buckets ends the line's current run, so hours nobody sampled stay visibly empty.
  * The range selector shows the timestamp of the newest reading, because a collector that stopped with its host still prints a plausible "current" rate.
  * Axes scale to the peak rather than to the tallest average, and points are placed by timestamp rather than by array index.
  * A stalled router leaves one WARNING on state change and one INFO on recovery, instead of a debug line nobody reads or a warning every 25 seconds.

* **📦 Self-Hosting on the Router (RouterOS Containers)**:
  * MikroMan can run as a container on the RouterOS device it manages: `POST /api/v1/routers/{id}/containers/setup/plan` shows every change it would make (storage paths, bridge, veth, gateway address, masquerade, mount, the container itself) and writes nothing; `.../setup/apply` executes that same plan.
  * Storage is chosen from `/disk`, not typed. The router reports which devices are mounted, which are read-only, which have no filesystem and which have room for the image, so a plan is refused with the reason before 340 MB is half-downloaded (`GET .../containers/storage`).
  * A device that cannot be used as-is can be formatted from the same panel (`POST .../containers/storage/format`). It is destructive and shaped like it: the slot name must be typed back, and anything holding `layer-dir`, `tmpdir` or a mount is refused — including the storage a running MikroMan booted its own database from.
  * The Containers page shows what each container costs — CPU share, cgroup memory, unpacked image size, restart count — next to the router's own totals, because on a board that also routes, a figure without a denominator is not an answer.
  * Idempotent and defensive: each step checks what the router already has, refuses to modify objects it did not create (`mikroman:` comments), blocks before creating anything when the storage is unusable or the chosen subnet is already in use, and stops at the first refused command while reporting which steps landed.
  * The web port forward is only ever created bound to one interface; an unbounded `dstnat` would expose the administrative UI on WAN.
  * No credentials are written into `/container/envs`: router logins and the bot token already travel inside the encrypted database, and copying them to env would put them in plaintext in the running config and every exported `.rsc`.
  * Manual path for a bare router: `scripts/setup_ros_container.rsc`.

* **🧭 Bounded Footprint and Self-Diagnostics**:
  * MikroMan's own log goes to `<data dir>/mikroman.log` — the same directory as the database, so on a router container it lands on the USB stick and survives a restart. Size-capped rotation (4 MB × 3 by default, `LOG_FILE_MAX_BYTES` / `LOG_FILE_BACKUP_COUNT`), and a data directory that cannot be written to degrades to console logging instead of failing to start.
  * `GET /api/v1/logs?source=app` serves that file back to the browser, since a RouterOS container has no `docker logs`. System Events shows it as a third source next to *Live Stream* and *Stored History*.
  * Per-request logging is off at the source (`httpx`, `httpcore`, `uvicorn.access`, `aiogram` sit at WARNING). On the live device those four loggers were 995 of the 1000 lines in the router's log ring — which meant the ring turned over in about five minutes and real device events were evicted before the 60-second scraper could copy them.
  * The background tick is split: hardware/bandwidth samples every `POLL_INTERVAL_SECONDS` (10 s), and device discovery, queue/mangle reconciliation, rollups and quota checks every `HEAVY_SYNC_INTERVAL_SECONDS` (60 s), staggered per router. UI actions apply their changes inline, so nothing waits on the slower clock. Set it to `10` to restore the previous behaviour.
  * Retention pruning is batched and runs hourly, never per tick. SQLite allows one writer; a range delete over a 116 MB database held that lock past the 5-second `busy_timeout` and every other worker failed with `database is locked`.
  * `GET /api/v1/system/diagnostics` answers "is this much CPU normal?" without a shell: resident set and peak (the process, not the cgroup's page-cache-inflated figure), RouterOS requests per device, and count/avg/max duration of each background pass. It needs neither a router nor the database.
  * History and chart reads are indexed for their actual shape. Composite indexes on `(router_id, timestamp)`, `(device_id, record_date)`, `(device_id, created_at)` and friends are created by migration `024_query_indexes` and, for installs that never run Alembic, at start-up; planner statistics (`ANALYZE`) are refreshed exactly when indexes are added. Measured on a copy of a live 691 142-row database: a one-hour interface chart went from 296 403 index entries visited to 6 778 (`51.6 ms → 1.4 ms`), and switching a preset stopped paying 484 ms for device event logs it never reads.
  * Tuning knobs live in the UI, not in the environment: background sample interval, housekeeping interval, telemetry stream rate, temperature and CPU alert lines, log retention. The stored value wins and the environment is its default — which matters because a RouterOS container has no `.env` to edit, no shell and no `docker exec`.

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

<details>
<summary><b>How the multi-architecture image is built</b></summary>

The `Dockerfile` uses three stages so that a single build serves 64-bit servers
and 32-bit ARM routers (RB4011, RB3011, hAP ac²) alike:

| Stage | Runs on | Purpose |
|---|---|---|
| `frontend` | Build host (`$BUILDPLATFORM`) | Compiles the static JS/CSS bundle natively at full speed, never under emulation. |
| `wheelbuilder` | Target architecture | Carries `build-essential` + `libffi-dev` and resolves every dependency into a local wheelhouse. |
| `runtime` | Target architecture | Installs from that wheelhouse with `--no-index`; ships without a compiler. |

The `wheelbuilder` stage exists because four hard dependencies publish **no
`linux/arm/v7` wheels** on PyPI and ship source distributions only: `cffi` (via
`cryptography`), `greenlet` (via SQLAlchemy's asyncio support), `MarkupSafe`
(via Mako/alembic) and `PyYAML`. Since `python:3.12-slim` contains no compiler,
they are compiled once in the throwaway builder stage and the finished wheels
are bind-mounted into the runtime stage, which keeps the shipped image slim.

Two optional C accelerators - `uvloop` and `httptools` - are excluded on ARMv7
by environment marker instead. Neither is required for correctness: uvicorn
falls back to the standard asyncio event loop and the `h11` parser.

</details>

### Two ways to run it

| | **On the router itself** (RouterOS container) | **On separate hardware** (Docker Compose) |
|---|---|---|
| Needs | RouterOS **v7.13+** with the `container` package installed and enabled, external USB/NVMe storage, container support turned on in `/system/device-mode` | Any Docker host; `linux/amd64`, `linux/arm64` or `linux/arm/v7` |
| Setup | Containers page → **Prepare this router for a container** → *Plan*, then *Apply* | `docker compose up -d` |
| Data lives in | `<storage>/mikroman_data/` mounted at `/data` | named volume `mikroman_data` |
| Restart behaviour | `start-on-boot=yes`, survives a router reboot | `restart: unless-stopped` |
| Updates | Containers page → update the image, then start it again | `docker compose pull && docker compose up -d` |
| Same image | `ghcr.io/masseselsev/mikroman:latest` — nothing is built on the device in either case | |

**On the router (option 1).** Everything the setup needs is done by the app from
inside itself: it reads `/disk` to judge the storage you point it at (mounted,
writable, room for a ~340 MB image), sets `layer-dir`/`tmpdir` off internal
flash, creates the bridge, veth, gateway address, masquerade and a LAN-bound web
forward, writes the data directory, and finally registers the container without
starting it. Each step is idempotent, refuses objects it did not create
(`mikroman:` comments), and *Plan* shows the exact list *Apply* will execute. A
device that cannot be used as-is can be formatted from the same panel — guarded
by typing the slot name back, and refused outright for storage that holds image
layers, `tmp` or an existing mount. The manual fallback for a router with no
working MikroMan on it is [`scripts/setup_ros_container.rsc`](scripts/setup_ros_container.rsc).

**Off the router (option 2).** Use `docker compose` (Option A below) or plain
`docker run` (Option B). Both are the recommended path for boards with little
memory, and neither needs an env file: the router credentials, the bot token and
every tuning knob are stored in the database and edited in the UI.

### Option A: Docker Compose (Recommended)
```bash
git clone https://github.com/masseselsev/mikroman.git
cd mikroman
docker compose up -d
```

Compose pulls the published multi-architecture image and brings up the named
data volume with it — nothing is compiled on the device, which matters on the
32-bit ARM boards this runs on. Upgrading is `docker compose pull && docker
compose up -d`.

**Building from source instead.** Contributors, and anyone running a change that
has not been released yet, add the build overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

The overlay tags the result `mikroman:local`, so a local build can never be
mistaken for — or silently shadow — a published release in the same image store.
Note that building on the target device is only realistic on amd64/arm64; an
armv7 board does not have the memory to compile the frontend bundle.

### Option B: Plain `docker run`
```bash
docker run -d \
  --name mikroman \
  --restart unless-stopped \
  -p 1928:1928 \
  -v mikroman_data:/data \
  ghcr.io/masseselsev/mikroman:latest
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
