# ⚡ MikroMan — MikroTik RouterOS Companion

An ultra-lightweight, high-performance companion app and Telegram bot for **MikroTik RouterOS 7.24+**, designed to run smoothly in RouterOS native containers or standard Docker hosts.

---

## 🌟 Key Features

* **👥 Hierarchical User & Device Traffic Control:**
  * **Parent-Child Simple Queues**: Set user-level shared bandwidth pools (`mikroman-{user}`) with nested per-device caps (`mikroman-{user}-{device}`).
  * **Device-Level Precision**: Configure individual device limits (e.g. 5M, 15M, 50M) or inherit the user group limit.
  * **Configurable Quarantine Limit for Unassigned / Rotated MAC Devices**: Automatically caps all new, unassigned, or randomized-MAC devices to a configurable speed limit (e.g. 5 Mbps default, 1M, 2M, 10M, or Unlimited) on RouterOS Simple Queues until explicitly assigned to a user profile.
  * **Instant Pause / Resume**: Freeze internet access for an entire user profile or a single rogue device using dynamic RouterOS Firewall Address Lists (`mikroman_blocked`).
  * **FastTrack Firewall Exemption**: Automatically patches default FastTrack rule with `!mikroman_queued`, guaranteeing strict queue enforcement while unshaped clients maintain maximum hardware throughput.
  * **Real-time Live Telemetry**: Live rate meters (download/upload) and daily volume counters updated via WebSocket.

* **📊 Historical Traffic Accounting & ISP Billing Cycles:**
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
