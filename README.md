# ⚡ MikroMan — MikroTik RouterOS Companion

An ultra-lightweight, high-performance companion app and Telegram bot for **MikroTik RouterOS 7.24+**, designed to run smoothly in RouterOS native containers or standard Docker hosts.

---

## 🌟 Key Features

* **👥 Hierarchical User & Device Traffic Control:**
  * **Parent-Child Simple Queues**: Set user-level shared bandwidth pools (`mikroman-{user}`) with nested per-device caps (`mikroman-{user}-{device}`).
  * **Device-Level Precision**: Configure individual device limits (e.g. 5M, 15M, 50M) or inherit the user group limit.
  * **Configurable Quarantine Limit for Unassigned / Rotated MAC Devices**: Automatically caps all new, unassigned, or randomized-MAC devices to a configurable speed limit (e.g. 5 Mbps default, 1M, 2M, 10M, or Unlimited) on RouterOS Simple Queues until explicitly assigned to a user profile.
  * **Instant Pause / Resume**: Freeze internet access for an entire user profile or a single rogue device using dynamic RouterOS Firewall Address Lists (`mikroman_blocked`).
  * **Idempotent Queue Synchronisation**: RouterOS normalises the values it stores (`192.168.88.10` → `192.168.88.10/32`, `5M/5M` → `5000000/5000000`). All comparisons are made in that normalised form, so a queue that is already correct is never rewritten. Managed objects are identified by stable id-based tags (`mikroman:managed:user_{id}`, `mikroman:managed:dev_{id}`) matched exactly, so renames never orphan a queue and no profile name can be mistaken for another whose name it prefixes.
  * **FastTrack Firewall Exemption**: Automatically patches default FastTrack rule with `!mikroman_queued`, guaranteeing strict queue enforcement while unshaped clients maintain maximum hardware throughput.
  * **Real-time Live Telemetry**: Live rate meters (download/upload) and daily volume counters updated via WebSocket. Rates are differentiated from the per-device firewall counters rather than read from Simple Queue `rate`, which can freeze on RouterOS 7.x; today's volume is read from the same rollups the analytics view uses, so the dashboard and the reports can never disagree.
  * **Queue Reconciliation**: Managed queues whose owning user or device no longer exists — or which no longer need their own queue after a device reverts to *Inherit User* — are removed automatically. A stranded queue keeps its old target and `max-limit`, so if that address is later reused it would silently throttle the new host. Queues MikroMan did not create are never touched.

* **📊 Historical Traffic Accounting & ISP Billing Cycles:**
  * **Firewall-Counter Accounting Engine**: Per-device volume is measured with dedicated RouterOS `/ip/firewall/mangle action=passthrough` counter rules (one for upload, one for download per device), tagged `mikroman:acct:dev_{id}:{up|down}`. `passthrough` only increments a counter and forwards the packet — it never drops, alters or reroutes traffic, and MikroMan never touches mangle rules it did not create.
  * **Why not Simple Queue counters**: On RouterOS 7.x the `bytes` counter of a Simple Queue can silently stay frozen at zero while traffic flows (verified on a hAP be^3 / RouterOS 7.25: a freshly created queue placed first in the queue order, targeting the busiest client, counted 0 bytes through a 4.9 MB burst). Simple Queues are therefore used for **bandwidth shaping only**; all accounting comes from firewall counters, which tracked 243.8 MB against 246 MB of real WAN throughput (99.1%) in the same measurement.
  * **Accounting Health Cross-Check**: Every analytics response carries an `accounting_health` block comparing gateway volume (WAN interface counters) against the sum of per-device counters — reported as `ok`, `partial` (range predates the accounting rules), `degraded` (accounting active but attributing almost nothing) or `no_data`. A broken accounting path is surfaced as a dashboard banner instead of being hidden behind a plausible-looking total.
  * **ISP Billing Cycle Anchor**: Set the exact day of the month (1–31) when your provider quota resets.
  * **Flexible Date Filtering**: Presets for *Today*, *Yesterday*, *Last 7 Days*, *Last 30 Days*, *Current Billing Cycle*, *Previous Billing Cycle*, and *Custom Date Ranges*.
  * **3-Level Synchronized Accounting**:
    * **Gateway Level**: Total bandwidth consumed, download/upload split, and peak rates across all monitored WAN interfaces.
    * **User Group Level**: Aggregated consumption, active device counts, and percentage share of total gateway bandwidth.
    * **Individual Device Level**: Searchable and filterable table with MAC, IP, vendor, assigned user, total bytes, and custom speed limits.
  * **Visual Daily Timeline**: Interactive daily volume charts with download/upload color-coded stacks.

* **📈 Hardware & Multi-Interface Performance Graphs:**
  * Interactive time-series charts for CPU load %, RAM usage %, Board Temperature (°C), and Board Voltage (V).
  * **Dynamic Temperature Scaling**: Automatically scales the Y-axis to zoom in on actual router operating temperatures (e.g. 68°C–76°C) rather than squishing values on a fixed 20°C–75°C range.
  * **Configurable Temperature Warning Threshold**: Custom thermal alert threshold (e.g. 75°C, 80°C, 85°C) with dashed visual threshold markers and telemetry warnings.
  * Multi-interface aggregate bandwidth monitoring (e.g. WAN `ether1` + `sfp-plus1`) with selectable interface checkboxes and live throughput sum.

* **🔍 Device Discovery, Auto-Scan Toggle & Hidden Devices:**
  * **Background Auto-Scan Toggle**: Control automatic network polling with a single switch; can be paused directly from the Unassigned Devices inbox or Settings modal.
  * **Hidden Devices for Technical / IoT Infrastructure**: Mark infrastructure hardware (modems, smart home gateways, IoT sensors) as hidden so they don't clutter default views, with an unselected-by-default "Show Hidden Devices" filter.
  * Automatic network scanner pulling ARP, DHCP leases, and Wireless registration tables.
  * **WAN-Side Filtering**: ARP entries seen on the monitored uplink interface (typically the ISP gateway on `ether1`) are excluded from discovery, so upstream hardware is never ingested as a LAN client or given a quarantine queue.
  * **Stale ARP Rejection**: RouterOS keeps unresolved (`complete=false`) ARP entries after a host leaves. These no longer count as proof of presence, so departed devices are correctly shown as offline instead of lingering as *Active* with a stale signal reading.
  * Automatic MAC OUI vendor resolution (Apple, Samsung, Sony, Intel, etc.).
  * Complete lifecycle event log: Discovery, Hostname changes, IP shifts, and Private / Randomized MAC rotations.
  * One-click **Smart Merge** suggestions to link rotated MACs back into original device profiles.

* **🛡️ Multi-Router Management & Automated SSL Provisioning:**
  * Manage and switch between multiple MikroTik routers from a unified dashboard.
  * **Auto-Configure SSL**: Automatically generates a TLS certificate directly on RouterOS and enables HTTPS REST API (port 443).
  * Support for custom CA root certificates for enterprise PKI environments.

* **🤖 Dual-Mode Telegram Bot:**
  * Works in both **Long Polling** (zero-config behind NAT) and **Webhook** modes.
  * Remote router status snapshot: `/status`.
  * Interactive traffic management with inline buttons: `/users`, `/pause`, `/limit`, `/reboot`.
  * Proactive alerts for newly discovered devices, high CPU load, temperature warnings, and WAN IP changes.

* **🎨 Modern Responsive Web Dashboard:**
  * **Two-Line Device Rows**: Each device shows identity, badges and *live* download/upload on the first line, and IP, vendor, interface, signal, last-seen and today's consumed volume on the second — so names are never truncated and every row answers both "what is using bandwidth now" and "how much has it used".
  * **Per-Device Live Metrics**: Live rate and daily volume are exposed per device, not only per user profile, so the specific device saturating the link is named directly.
  * **Compact Telemetry Strip**: Download, Upload, CPU, RAM and Temperature carry inline **sparklines** built from the live telemetry stream (no extra requests, no charting dependency), alongside the **WAN IP**, **active client count** and uptime. Temperature is coloured against your configured warning threshold.
  * **Sortable Analytics Tables** with share-of-traffic bars, defaulting to the heaviest consumer first.
  * **Interface Link Health**: per-interface RX/TX volume plus error and drop counters — the earliest warning of a failing cable or saturated link.
  * **Discovery Context**: unassigned devices show first-seen time and today's consumed volume, so an unknown client that moved gigabytes stands out from one that moved nothing.
  * Native **RouterOS Dark Mode** (WinBox slate/blue) and **RouterOS Light Mode** (WebFig).
  * Full bilingual **English (`en`)** and **Russian (`ru`)** language switching.
  * Compact, responsive layouts tailored for mobile, tablet, and desktop viewports.

* **🪶 Ultra-Lightweight Footprint:**
  * Consumes **< 45MB RAM** and negligible CPU.
  * Multi-stage Docker build with container image size **< 80MB**.

---

## 🚀 Quick Start (Docker / Docker Compose)

### 1. Clone & Configure
```bash
git clone https://github.com/your-org/mikroman.git
cd mikroman
cp .env.example .env
# Edit .env with your RouterOS IP and admin password
```

### 2. Run with Docker Compose
```bash
docker compose up -d
```
Open **`http://localhost:1928`** in your web browser.

---

## 📦 Running in MikroTik RouterOS 7.24+ Container

1. **Enable Container Mode on RouterOS:**
   ```routeros
   /system/device-mode/update container=yes
   ```
2. **Execute Setup Script:**
   Import or run the commands from [`scripts/setup_ros_container.rsc`](file:///home/masse/projects/mikroman/scripts/setup_ros_container.rsc).
3. Access the dashboard via your router LAN IP at port `1928` (e.g. `http://192.168.88.1:1928`).

---

## 🧪 Testing & Verification

Run the automated test suite with pytest:
```bash
. .venv/bin/activate
pytest -v
```

Run code formatting and lint check:
```bash
ruff check backend tests
```

Build the frontend React bundle:
```bash
cd frontend && npm run build
```
