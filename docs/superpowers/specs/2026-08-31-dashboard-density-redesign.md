# Dashboard Density & Informativeness Redesign

**Date:** 2026-08-31
**Status:** Approved
**Scope:** Full dashboard pass, delivered in three stages

---

## 1. Problem

The dashboard shows less than the system already knows.

* **Device rows truncate.** Everything sits on one line — status dot, icon, name, badges, signal, IP, three buttons — so names ellipsize (`Nama...`) while the row still carries no traffic information at all.
* **Per-device traffic is computed and thrown away.** `LiveRateTracker.sample()` returns a live rate per device; only the per-user aggregate survives. `DeviceTrafficRollup` stores per-device daily volume; nothing reads it back for display.
* **`bytes_today_in` / `bytes_today_out` are fetched and never rendered.** `UserDTO` carries them, `App.jsx` receives them, no component shows them.
* **The telemetry bar spends six large tiles on six scalars** and omits the WAN IP, the active client count, and any history, despite `SystemMetric` and `InterfaceMetric` holding time series.
* **Cards stretch to equal height**, so a profile with one device shows a large dead gap beside a profile with three.

## 2. Goals

1. Every device row shows what it is, where it is, how fast it is going now, and how much it has used — without truncation.
2. No datum that the backend already computes is discarded before reaching the screen.
3. Higher information density per vertical pixel, not merely smaller text.

**Non-goals:** new collection mechanisms, schema changes to the accounting tables, changes to shaping behaviour.

## 3. Information hierarchy

The user ranked all four use cases equally, so the ordering below is by *decision urgency* rather than stated preference:

| Rank | Question | Where it is answered |
|---|---|---|
| 1 | Who is saturating the link right now? | Device row, line 1, right-aligned |
| 2 | Who has used how much? | Device row line 2 right; card header |
| 3 | What is this device? | Device row, line 2, left |
| 4 | Is the router healthy? | Telemetry bar (ambient, always visible) |

## 4. Stage 1 — Enabling data and user cards

### 4.1 API surface

`DeviceDTO` gains four fields, populated the same way `UserDTO`'s equivalents already are:

| Field | Type | Source |
|---|---|---|
| `current_rate_in` | int (bps) | `LiveRateTracker.sample()` per-device result |
| `current_rate_out` | int (bps) | as above |
| `bytes_today_in` | int | `DeviceTrafficRollup` for `date.today()` |
| `bytes_today_out` | int | as above |

`TrafficController.get_realtime_traffic_stats` already samples the tracker and reads today's user rollups. It is extended to return the per-device breakdown alongside the per-user totals, so no additional RouterOS call is made. A `_todays_device_volume` helper mirrors the existing `_todays_user_volume`.

Devices absent from the tracker (offline, or first sample) report `0`, never a stale value.

### 4.2 Device row

Two lines, no truncation:

```
● 📱 NamasT3k            PRIVATE          ↓ 1.5 Mbps  ↑ 220 Kbps   ⏸ ⚙
  192.168.88.242 · MikroTik · wifi2 · −65 dBm · 2m ago    ↓ 2.1 GB  ↑ 45 MB
```

* Line 1: status dot, type icon, full name, badges (Private / custom limit / hidden), live rate.
* Line 2 (muted, smaller): IP, vendor, interface, signal, relative last-seen, today's volume.
* Live rate is dimmed to `--text-muted` when zero, so an idle device is visibly idle rather than merely small.
* Offline devices render line 2 without signal (the stale reading is meaningless once the device has gone).

### 4.3 Card header and grid

* Header gains today's combined volume and the profile's share of gateway traffic.
* Avatar shrinks 38px → 30px; the `N devices` subtitle merges into the badge row.
* The RX/TX gauge block loses vertical padding.
* The card grid uses `align-items: start` so cards size to their content.

### 4.4 Device row controls

Pause stays inline. Hide and settings move into the device modal, which a row click already opens. Rationale: three buttons per device is nine controls of chrome on a three-device card, and both relocated actions are already reachable one click away.

## 5. Stage 2 — Telemetry bar

Replace six fixed tiles with a denser strip driven by the existing history endpoints (`/api/v1/metrics/system`, `/api/v1/metrics/interfaces`):

* WAN download / upload with an inline sparkline.
* CPU with sparkline; RAM; temperature with its configured warning threshold marked.
* Newly surfaced: WAN IP, uptime, active client count.

Sparklines are rendered as inline SVG polylines from data already stored; no new dependency.

## 6. Stage 3 — Analytics, inbox, router health

* **Traffic Analytics:** sortable columns, per-row share bars, accounting-health banner integrated into the summary rather than floating above it.
* **Unassigned Devices:** vendor and first-seen promoted; merge suggestions made more prominent.
* **Router Health:** per-interface error and drop counters.

## 7. Testing

* Backend: per-device rate and volume appear in `DeviceDTO`; devices with no sample report `0`; user totals remain the sum of their devices.
* Frontend: `npm run build` clean.
* Live verification against the running container after each stage.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Two-line rows are taller, so tall profiles grow | Line 2 is smaller and muted; non-stretching grid recovers space elsewhere |
| Relocating hide/settings may frustrate | Flagged to the user at design time; both remain one click away |
| Per-device rate needs two samples | Renders `0` until the second sample, never a stale figure |
