# ⚡ MikroMan — MikroTik RouterOS Companion

An ultra-lightweight, high-performance companion app and Telegram bot for **MikroTik RouterOS 7.24+**, designed to run smoothly in RouterOS native containers or standard Docker hosts.

---

## 🌟 Key Features

* **👥 Per-User Bandwidth & Traffic Control:**
  * Group multiple network devices (MAC / IP) under user profiles.
  * Real-time download/upload speed gauges with live WebSocket updates.
  * Instant speed limit presets (e.g. 5M, 10M, 25M, 50M, 100M, Unlimited) dynamically managed via RouterOS Simple Queues.
  * One-click internet pause / resume toggle using dynamic RouterOS Firewall Address Lists (`mikroman_blocked`).
* **🎨 Modern Responsive Web Dashboard:**
  * Native **RouterOS Dark Mode** (WinBox slate/blue) and **RouterOS Light Mode** (WebFig).
  * Full bilingual **English (`en`)** and **Russian (`ru`)** language switching.
  * Unassigned device discovery inbox with automatic MAC OUI vendor resolution (Apple, Sony, Samsung, etc.).
  * Router health gauges: CPU load %, RAM free, Board Temperature, Voltage, Uptime, and Gateway throughput.
* **🤖 Dual-Mode Telegram Bot:**
  * Works in both **Long Polling** (zero-config behind NAT) and **Webhook** modes.
  * Remote router status snapshot: `/status`.
  * Interactive user traffic management with inline buttons: `/users`, `/pause`, `/limit`, `/reboot`.
  * Proactive alerts for newly discovered devices, high CPU load, and WAN IP changes.
* **🪶 Ultra-Lightweight Footprint:**
  * Consumes **< 45MB RAM** and negligible CPU.
  * Multi-stage build with container image size **< 80MB**.

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
