# ⚡ MikroMan — MikroTik RouterOS Companion

An ultra-lightweight, high-performance companion app and Telegram bot for **MikroTik RouterOS 7.1+**, designed to run smoothly in RouterOS native containers or standard Docker hosts.

### RouterOS compatibility

| | Version | Why |
|---|---|---|
| **Minimum** | **7.1** | The release that first shipped the REST API this app speaks. Every other menu it uses predates RouterOS v7. |
| **Recommended** | **7.13+** | The `wifiwave2` menu was renamed to `wifi` in 7.13. Below it the app falls back to the legacy `/interface/wireless` menu, which works but reports less. |
| **RouterOS container deployment** | **7.4** | The `container` package. Not needed when running on a Docker host. |
| **Verified against** | **7.25** | hAP be³ Media. Newer releases are expected to work but are unverified — the app says so in the connection result. |

The floor is **derived from the code**, not asserted here:
[`backend/app/services/routeros_compat.py`](backend/app/services/routeros_compat.py)
declares every RouterOS menu the app touches and the release that introduced it,
and `MINIMUM_VERSION` is computed from that table. A router below the floor, or
above the highest verified version, is reported in the connection response and
in `GET /api/v1/system/status` — as a warning, never as a refusal. See
[`docs/ROUTEROS_API_POLICY.md`](docs/ROUTEROS_API_POLICY.md) for the rules that
govern adding new router calls.

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
  * **Wireless Association as Presence**: an authorized entry in the Wi-Fi registration table proves a client is online — more reliably than ARP, since a client can hold a stable radio link while its ARP entry expires or roam on without a DHCP lease of its own.
  * **📶 WiFi 7 Multi-Link (MLO) Awareness**: RouterOS reports a multi-link client as a single `mld*` entry that names no actual radio. MikroMan expands `mld-interfaces` and `mld-link-addresses` into the individual radio links and shows **each link with its own interface, band and signal** (e.g. `wifi2 5G·BE −62`). The headline signal is the strongest link, so a weak secondary link never makes a well-connected device look bad, and per-link readings are never invented when the router reports fewer than there are links.
  * **🔗 Multi-Adapter Devices**: a machine that reaches the network over more than one adapter — a laptop docked over Ethernet and roaming over Wi-Fi — has a different MAC per adapter and was previously discovered as several unrelated devices with its traffic split between them. Adapters can be **linked into one logical device**: the row appears once, marked `N×`, listing every live connection, with rate and volume summed across them and pause applied to all adapters at once (otherwise a paused machine simply hops media). Matching hostnames on different media are proposed automatically. This is distinct from **merging**, which exists for MAC rotation and collapses two records because only one address was ever real — here both remain valid and both are kept.
  * Automatic MAC OUI vendor resolution (Apple, Samsung, Sony, Intel, etc.).
  * Complete lifecycle event log: Discovery, Hostname changes, IP shifts, and Private / Randomized MAC rotations.
  * One-click **Smart Merge** suggestions to link rotated MACs back into original device profiles.

* **🛡️ Multi-Router Management & Automated SSL Provisioning:**
  * Manage and switch between multiple MikroTik routers from a unified dashboard.
  * **Auto-Configure SSL**: Automatically generates a TLS certificate directly on RouterOS and enables HTTPS REST API (port 443).
  * Support for custom CA root certificates for enterprise PKI environments.

* **🤖 Dual-Mode Telegram Bot:**
  * Works in both **Long Polling** (zero-config behind NAT) and **Webhook** modes.
  * **Authenticated Webhooks**: MikroMan registers a per-process secret with `setWebhook` and verifies the `X-Telegram-Bot-Api-Secret-Token` header on every delivery, rejecting anything unsigned — without which any host able to reach the endpoint could inject bot commands such as `/reboot`. It **fails closed**: if no secret has been established, deliveries are refused.
  * **Webhook setup**: Telegram must reach the URL from the internet over HTTPS on port 443, 80, 88 or 8443 with a valid certificate, so a LAN address will not work — point it at this app's `/api/v1/telegram/webhook` through a reverse proxy or tunnel. Switching back to Long Polling clears the registered webhook automatically, since Telegram refuses polling while one is set.
  * Remote router status snapshot: `/status`.
  * Interactive traffic management with inline buttons: `/users`, `/pause`, `/limit`, `/reboot`.
  * Proactive alerts for newly discovered devices, high CPU load, temperature warnings, and WAN IP changes.

* **🎨 Modern Responsive Web Dashboard:**
  * **Three-Column Device Rows**: Each device row is a text column (name and badges, IP and vendor, radio links and signal), a fixed-width figures column (live download/upload and today's volume) and fixed actions. Only the text column shrinks, so a device jumping from `0 bps` to `12.4 Mbps` can neither reflow the name nor push the numbers past the card edge. Radio bands (`5G·BE`, `5G·AX`) are tagged in a dedicated accent colour, deliberately outside the status palette — a band is not good or bad news, but it should be readable at a glance.
  * **Per-Device Live Metrics**: Live rate and daily volume are exposed per device, not only per user profile, so the specific device saturating the link is named directly.
  * **Compact Telemetry Strip**: Download, Upload, CPU, RAM and Temperature carry inline **sparklines** built from the live telemetry stream (no extra requests, no charting dependency), alongside the **WAN IP**, **active client count** and uptime. Temperature is coloured against your configured warning threshold.
  * **WAN Identity Tile**: shows the interface address, the address the internet actually sees (they differ under carrier-grade NAT), and the **provider name** — resolved together in one cached lookup, since an AS number and operator name are the only way to tell two links apart when both hand out CGNAT addresses. Failure is silent: the router may legitimately have no internet.
  * **Single Design System**: sizes, widths and corner radii come from one set of CSS tokens — an eight-step type scale, a five-step radius scale and two control heights — instead of the 29 font sizes, 8 raw pixel radii and 40 ad-hoc paddings the components had each invented for themselves. Segmented selectors, panels, list rows, setting rows, dropdowns and toolbar controls are shared classes, so a control cannot look different in two places.
  * **Sortable Analytics Tables** with share-of-traffic bars, defaulting to the heaviest consumer first.
  * **Interface Link Health**: per-interface RX/TX volume plus error and drop counters — the earliest warning of a failing cable or saturated link.
  * **Discovery Context**: unassigned devices show first-seen time and today's consumed volume, so an unknown client that moved gigabytes stands out from one that moved nothing.
  * Native **RouterOS Dark Mode** (WinBox slate/blue) and **RouterOS Light Mode** (WebFig).
  * Full bilingual **English (`en`)** and **Russian (`ru`)** language switching.
  * Compact, responsive layouts tailored for mobile, tablet, and desktop viewports.

* **⚙️ Router-Friendly Polling:**
  * **Pooled keep-alive connections**: the RouterOS client holds one connection instead of opening a new TLS session per request. Measured on a live hAP be^3, this cut the app's CPU cost on the router from **+6.6 to +2.4 percentage points** over idle (median 8% → 5% against a 2% baseline), with peaks halved.
  * **Configurable telemetry interval** (1–10s) in Settings: each poll costs several REST calls, so a longer interval trades responsiveness for router CPU.
  * Rarely-changing values — WAN IP, router clock — are cached rather than re-read every frame.

* **📉 ISP Cycle Data Limit:**
  * Set an allowance for the billing cycle and watch consumption against it, with remaining bytes and the **daily budget** needed to stay inside it.
  * **Multiple alert thresholds** (50/75/80/90/100%) fire **once per cycle** each and re-arm automatically when the cycle resets — checked on the background tick, so a warning arrives even with no browser open, optionally to Telegram.

* **🕒 Router-Local Time:**
  * **Daily boundaries follow the router's calendar.** The container almost always runs UTC while the router sits in a local zone, so on a UTC+5 router everything after 19:00 local was filed under the previous day and "today" meant a different day than the router's clock showed. Rollups, billing cycles and range presets are all keyed to the router's date.
  * The navbar shows the **router's own clock and timezone**, since every figure on the dashboard (lease ages, billing cycles, daily rollups) is anchored to the router while the container usually runs UTC. The offset is fetched once a minute and the browser advances the clock itself, so a live time costs no extra polling.

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

## 📦 Running in MikroTik RouterOS 7.4+ Container

> ### ⚠️ Put the container on external storage
>
> **Store the container and its data on a USB flash drive or, preferably, a USB
> SSD — not on the router's internal storage.** This is a strong
> recommendation, not a preference.
>
> RouterOS's internal storage is NAND flash with a finite number of write
> cycles and no wear levelling worth relying on. MikroMan writes continuously
> by design: telemetry samples, interface metrics, daily traffic rollups and
> device history all land in SQLite, and the container image itself consumes a
> large share of the free space on most boards. Running that workload against
> internal flash wears it out and can eventually take the router's own
> configuration storage with it.
>
> A USB SSD is preferred over flash for the same reason: it has real wear
> levelling and far higher endurance under the sustained small writes a
> database produces.
>
> ```routeros
> # Verify the external disk is mounted, then point containers at it
> /disk print
> /container/config/set registry-url=https://registry-1.docker.io \
>     tmpdir=usb1/pull ram-high=256M
> # ...and give the container's root-dir and any mount a path on usb1
> ```

1. **Enable Container Mode on RouterOS:**
   ```routeros
   /system/device-mode/update container=yes
   ```
2. **Attach external storage** and confirm it appears under `/disk print`.
3. **Execute Setup Script:**
   Import or run the commands from [`scripts/setup_ros_container.rsc`](scripts/setup_ros_container.rsc),
   adjusting `root-dir` and the data mount to your USB disk.
4. Access the dashboard via your router LAN IP at port `1928` (e.g. `http://192.168.88.1:1928`).

### HTTP or HTTPS?

**Running inside the router's own container: use plain HTTP.** The REST session
never leaves the device, so TLS protects nothing and only adds a certificate to
manage and renew.

**Running on a separate host: use HTTPS.** The session crosses your network,
and RouterOS REST authenticates with HTTP Basic — credentials in every request.

TLS is *not* a performance argument either way. Measured on a hAP be³ /
RouterOS 7.25, 8 req/s of `GET /interface` over 40 s per phase, sampled by an
identical low-rate sampler in every phase:

| Transport | Median CPU | p90 | Max | Requests completed |
|---|---|---|---|---|
| Idle baseline | 2% | 3% | 3% | — |
| HTTP, keep-alive | 5% | 7% | 10% | 268 |
| **HTTPS, keep-alive** | **5%** | **6%** | **7%** | **268** |
| HTTPS, new connection per request | 12% | 24% | 27% | 193 |

Encrypting the *stream* is free; the expensive part is the TLS *handshake*.
Because MikroMan pools connections, it performs one handshake and reuses it —
so HTTPS and HTTP cost the router the same. Without pooling the same workload
costs **2.4× the CPU and completes 28% fewer requests**, which is what the
connection-reuse work fixed.

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
