# Billing-cycle reset time (hours + minutes)

**Status:** approved 2026-09-01

## Problem

The ISP billing cycle anchor is a single day of the month (`billing_cycle_anchor_day`,
1–31). Some ISPs reset the counter at a specific time of day, not at midnight, so
"96.7 GB / 2 TB · 3 days left" and the end-of-cycle forecast can be up to a day
off around the reset.

## Constraint

Every traffic figure MikroMan stores is a **daily** total (`record_date` on
`TrafficRollup` / `DeviceTrafficRollup` / `RouterTrafficRollup` /
`RouterSelfTrafficRollup`). A mid-day reset splits the boundary day between two
cycles and those rollups cannot express that split.

`interface_metrics`, however, samples each interface's **cumulative**
`rx_bytes_total` / `tx_bytes_total` with a `timestamp`, about every 1.5 s,
pruned at 30 days. The quota is billed on the gateway/WAN total
(`build_quota_status` uses `data.gateway.total_bytes`), so the boundary day of
the **current** cycle — whose start is always < 30 days old — can be sliced at
the exact reset minute from those samples. No "assume uniform traffic" guess.

## Approach (B of three)

Slice each boundary day at the reset instant using the sampled WAN counters;
fall back to whole-day only when the sub-day samples are already gone.

| Case | Result |
|---|---|
| Current cycle boundary (start < 30 d old) | exact to the minute, from `interface_metrics` |
| Today, when today is the reset day | exact |
| Previous cycle boundary (often 30–60 d old, pruned) | whole-day fallback; affects only the "last cycle avg" context number |
| Per-user / per-device breakdown on a boundary day | stays day-granular — reset time only matters for the quota total |
| Router reboot on a boundary day | counter resets; the slice detects the backwards jump and does not difference across it |

Rejected: **A** (HH:MM for the countdown only, whole-day traffic) — user asked
for it to be counted correctly where possible. **C** (hourly rollups) — a new
table and collection path, full precision forever, overkill for a reset-time
setting.

## Changes

### Storage
Two new `AppSetting` keys, `billing_cycle_anchor_hour` (0–23, default 0) and
`billing_cycle_anchor_minute` (0–59, default 0). 00:00 reproduces today's
behaviour exactly, so existing installs are unchanged and no data migration is
needed.

### `analytics_engine`
- `get_billing_anchor_day` → add `get_billing_anchor_time(session) -> tuple[int,int]`
  reading the two keys with `(0, 0)` defaults; `set_billing_anchor_time`.
- `get_billing_cycle_dates(anchor_day, ref_date, previous)` →
  `get_billing_cycle_bounds(anchor_day, anchor_hour, anchor_minute, ref_dt, previous) -> tuple[datetime, datetime]`,
  router-local. The reset day belongs to the new cycle from the reset instant
  onward, to the old cycle before it. Month-length clamping (day 31 in a
  30-day month) is unchanged; the time is attached after the day is resolved.
- `resolve_date_range` still returns whole `date`s for `billing_current` /
  `billing_previous`, widened to cover both partial boundary days
  (`bounds[0].date()` … `min(bounds[1].date(), today)`).

### New helper — `services/rollups.py`
```
async def slice_of_day_bytes(
    session, router_id, day: date,
    from_time: time | None, to_time: time | None,
    interfaces: list[str],
) -> Volume | None
```
Per monitored WAN interface, loads every `interface_metrics` row on `day` with
`from_time <= timestamp <= to_time` (a `None` bound = start / end of day),
ordered by timestamp, and sums `max(0, curr.rx_bytes_total - prev.rx_bytes_total)`
across consecutive samples (likewise `tx`). Walking every sample in the window
rather than just differencing the two endpoints means an intermediate reboot
shows up as one negative step that `max(0, …)` drops, instead of corrupting the
whole slice. Returns `None` when no interface has a sample in the window, so the
caller falls back to the whole-day rollup.

Bytes between the window edge and the nearest sample are unattributed — at
~1.5 s spacing that is at most a couple of seconds of traffic per edge, far
below the rounding already in every GB figure.

### `build_quota_status` (endpoints/analytics.py)
- `anchor_hour, anchor_minute = await AnalyticsEngine.get_billing_anchor_time(db)`
- `cycle_start_dt, cycle_end_dt = get_billing_cycle_bounds(...)` in router-local time.
- `used` = whole-range gateway rollup **minus** the pre-reset slice of the
  cycle-start day (`slice_of_day_bytes(..., day=cycle_start_dt.date(),
  from_time=None, to_time=cycle_start_dt.time())`), when that slice is not
  `None`. The post-"now" slice on the current day is already excluded by
  `end_date = min(cycle_end, today)`.
- `days_remaining` stays an **int** on the DTO (ceil of the remaining fraction),
  so `"{days} days left"` still reads cleanly and no existing consumer breaks.
  The precise instant is carried separately as `cycle_end_at` for a
  finer-grained label.
- The projection math uses **fractional** day counts internally
  (`elapsed_days = (now - cycle_start_dt) / 1 day`,
  `total_days = (cycle_end_dt - cycle_start_dt) / 1 day`) so `avg_per_day` and
  the pace blend move smoothly across the reset instead of stepping by a whole
  day. The `cycle_days_total` / `cycle_days_elapsed` DTO fields stay int
  (rounded) — display only.
- Previous-cycle figures: same slice attempt, whole-day fallback when `None`.

### Schemas
- `BillingCycleConfig`: add `anchor_hour: int = Field(0, ge=0, le=23)`,
  `anchor_minute: int = Field(0, ge=0, le=59)`.
- `QuotaStatusDTO`: `days_remaining` stays `int` (ceil); add
  `cycle_end_at: datetime | None` — the exact router-local reset instant, for a
  precise countdown label.
- `TrafficAnalyticsResponse.billing_anchor_day` unchanged; add
  `billing_anchor_time: str = "00:00"` for display.

### Frontend
- `api/client.js`: `saveBillingCycleConfig` sends `anchor_hour`, `anchor_minute`;
  `getBillingCycleConfig` reads them.
- `TrafficAnalytics.jsx` billing-cycle modal: an optional `type="time"` input
  (`HH:MM`) beside the day field; the header summary reads
  `Day 5 · 14:30` when a non-midnight time is set, `Day 5` otherwise.
- `QuotaStrip.jsx`: when `cycle_end_at` is set and the reset is not at midnight,
  the label reads `{d}d {h}h left` computed from `cycle_end_at` in the browser;
  otherwise the existing `{days} days left`. New i18n key `quota_time_left`
  (`"{d}d {h}h left"`) EN + RU.

### Migration 014
No schema change (AppSetting rows). A no-op revision to keep the Alembic head
chain linear and documented; `init_db` needs nothing (AppSetting already
exists). `down_revision = "013_containers_router_traffic"`.

## Tests

- `get_billing_cycle_bounds`: non-midnight anchors; the reset instant falling
  before / after `ref_dt` on the reset day; day 31 at 14:30 in a 30-day month;
  `previous=True` shift; year boundary.
- `slice_of_day_bytes`: seeded `interface_metrics` — a clean partial day, a day
  with a reboot mid-slice (counter resets), a day with no samples → `None`,
  multiple monitored interfaces summed.
- `build_quota_status`: boundary-day `used` exact from samples vs whole-day
  fallback when samples pruned; `cycle_end_at` carries the reset instant;
  fractional internal day counts; **00:00 anchor reproduces the pre-change
  numbers exactly** (regression guard).
- Frontend: the modal round-trips `HH:MM`; QuotaStrip label switches format on a
  non-midnight reset.

## Out of scope

Per-user / per-device boundary-day precision (no sub-day samples for the mangle
counters); reconstructing previous-cycle boundaries older than the 30-day
`interface_metrics` retention; a configurable retention window.
