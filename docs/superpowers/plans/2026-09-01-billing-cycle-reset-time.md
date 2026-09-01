# Billing-cycle reset time (hours + minutes) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the ISP billing-cycle anchor carry an optional time of day (HH:MM), and count the boundary day's traffic correctly by slicing the sampled WAN counters at the reset minute.

**Architecture:** A new `get_billing_cycle_bounds()` returns router-local `datetime` boundaries; `get_billing_cycle_dates()` becomes a thin date-granular shim over it (and, as a side effect, fixes a pre-existing two-month-window bug for `anchor_day == 1`). `build_quota_status` subtracts the pre-reset slice of the cycle-start day from the gateway total, reading that slice from `interface_metrics` (cumulative counters sampled ~every 1.5 s, retained 30 days); when the samples are gone it falls back to the whole day. The reset instant rides on the DTO as `cycle_end_at` for a precise browser-side countdown.

**Tech Stack:** FastAPI, async SQLAlchemy 2.0, Alembic, Pydantic v2 (backend); React 18 + Vite 6, vanilla CSS, lucide-react (frontend); pytest + pytest-asyncio, Vitest + Testing Library.

## Global Constraints

- Python files under ~500–800 lines; split routers/services/components when they grow.
- All DB schema changes go through an Alembic migration **and** a matching ad-hoc `ALTER`/read path, because the production DB is not Alembic-managed (`Base.metadata.create_all` + `init_db`). This feature adds only `AppSetting` rows, so no `ALTER` is needed — but the migration head chain must stay linear.
- Pydantic models for every request/response body.
- UI text is authored in English first; every new EN i18n key gets a RU key of comparable visual length in `frontend/src/i18n/translations.js` (no duplicate keys — last wins silently).
- Never mention AI/Claude in code, comments, or commit messages. Keep commit messages plain, not AI-styled.
- `AppSetting` keys used: `billing_cycle_anchor_day` (existing), `billing_cycle_anchor_hour` (new), `billing_cycle_anchor_minute` (new).
- Router-local time comes from `backend/app/services/router_time.py`: `router_local_now(session) -> datetime`, `router_local_date(session) -> date`. Never use `datetime.now()` for cycle math.
- `interface_metrics` retention is 30 days (`metrics_collector.py:104`). Previous-cycle boundary slices will often have no samples — that path MUST degrade to whole-day, not error.
- Verify after every task: `python -m pytest tests -q` and `python -m ruff check backend tests` from repo root (`.venv/bin/python` if no active venv); frontend tasks additionally `cd frontend && npx vitest run && npx vite build` and `node frontend/scripts/check-identifiers.cjs`, plus the i18n check `node <scratchpad>/i18ncheck.cjs` if present.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `backend/app/services/analytics_engine.py` | `get_billing_cycle_bounds()` (new, datetime), `get_billing_cycle_dates()` (shim), `get_billing_anchor_time()` / `set_billing_anchor_time()`, `resolve_date_range()` gains anchor-time params | 1, 3 |
| `backend/app/services/rollups.py` | `resolve_monitored_interfaces()`, `slice_of_day_bytes()` | 2 |
| `backend/app/schemas/analytics.py` | `BillingCycleConfig.anchor_hour/anchor_minute`, `QuotaStatusDTO.cycle_end_at`, `TrafficAnalyticsResponse.billing_anchor_time` | 4 |
| `backend/app/api/v1/endpoints/analytics.py` | `build_quota_status` precise boundary + fractional day math; `get/set_billing_cycle_config` carry the time; `get_traffic_analytics` passes anchor time to `resolve_date_range` | 5 |
| `backend/migrations/versions/014_billing_anchor_time.py` | no-op revision keeping the Alembic head chain linear | 1 |
| `frontend/src/api/client.js` | `saveBillingCycleConfig` / `getBillingCycleConfig` carry `anchor_hour`, `anchor_minute` | 6 |
| `frontend/src/components/TrafficAnalytics.jsx` | optional `HH:MM` input in the billing-cycle modal; header summary line | 6 |
| `frontend/src/components/QuotaStrip.jsx` | `{d}d {h}h left` label from `cycle_end_at` when the reset is not at midnight | 7 |
| `frontend/src/i18n/translations.js` | `billing_anchor_time`, `billing_anchor_time_hint`, `quota_time_left` (EN + RU) | 6, 7 |
| `tests/test_billing_quota.py` | unit tests for bounds, anchor-time settings, `slice_of_day_bytes` | 1, 2 |
| `tests/test_analytics_and_device_limits.py` | `resolve_date_range` billing presets; `build_quota_status` boundary exactness + fallback + 00:00 regression | 3, 5 |
| `frontend/src/components/QuotaBillingTime.test.jsx` | modal round-trips HH:MM; QuotaStrip label switches format | 6, 7 |

---

## Task 1: `get_billing_cycle_bounds`, anchor-time settings, migration 014

**Files:**
- Modify: `backend/app/services/analytics_engine.py` (imports at `:1-4`; add functions after `get_billing_cycle_dates` which ends at `:80`; add methods to `AnalyticsEngine` after `set_billing_anchor_day` which ends at `:147`)
- Create: `backend/migrations/versions/014_billing_anchor_time.py`
- Test: `tests/test_billing_quota.py` (add at end; new imports)

**Interfaces:**
- Produces:
  - `get_billing_cycle_bounds(anchor_day: int, anchor_hour: int, anchor_minute: int, ref_dt: datetime, previous: bool = False) -> Tuple[datetime, datetime]` — `(start_dt, end_dt)`, router-local naive datetimes, `start_dt` inclusive, `end_dt` exclusive (the next cycle's reset instant). For `previous=True`, the cycle immediately before the one containing `ref_dt`.
  - `AnalyticsEngine.get_billing_anchor_time(session) -> Tuple[int, int]` — `(hour, minute)`, each clamped, defaulting to `(0, 0)`.
  - `AnalyticsEngine.set_billing_anchor_time(session, hour: int, minute: int) -> Tuple[int, int]` — clamps to `0..23` / `0..59`, persists two `AppSetting` rows, returns the stored pair.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_billing_quota.py`. Extend the top imports:

```python
from datetime import date, datetime

from backend.app.services.analytics_engine import (
    AnalyticsEngine,
    get_billing_cycle_bounds,
)
```

Append:

```python
class TestBillingCycleBounds:
    def test_midnight_anchor_matches_the_old_inclusive_dates(self):
        # anchor 15 at 00:00, ref mid-cycle -> Aug 15 00:00 .. Sep 15 00:00
        start, end = get_billing_cycle_bounds(15, 0, 0, datetime(2026, 8, 29, 12, 0))
        assert start == datetime(2026, 8, 15, 0, 0)
        assert end == datetime(2026, 9, 15, 0, 0)

    def test_before_the_reset_time_on_the_anchor_day_is_still_the_old_cycle(self):
        # reset is day 5 at 14:30; it is 10:00 on the 5th -> cycle started Aug 5
        start, end = get_billing_cycle_bounds(5, 14, 30, datetime(2026, 9, 5, 10, 0))
        assert start == datetime(2026, 8, 5, 14, 30)
        assert end == datetime(2026, 9, 5, 14, 30)

    def test_after_the_reset_time_on_the_anchor_day_is_the_new_cycle(self):
        start, end = get_billing_cycle_bounds(5, 14, 30, datetime(2026, 9, 5, 16, 0))
        assert start == datetime(2026, 9, 5, 14, 30)
        assert end == datetime(2026, 10, 5, 14, 30)

    def test_exactly_at_the_reset_instant_counts_as_the_new_cycle(self):
        start, _ = get_billing_cycle_bounds(5, 14, 30, datetime(2026, 9, 5, 14, 30))
        assert start == datetime(2026, 9, 5, 14, 30)

    def test_day_31_anchor_clamps_to_the_last_day_of_a_short_month(self):
        start, end = get_billing_cycle_bounds(31, 9, 0, datetime(2026, 2, 15, 12, 0))
        assert start == datetime(2026, 1, 31, 9, 0)
        assert end == datetime(2026, 2, 28, 9, 0)  # 2026 is not a leap year

    def test_previous_cycle_is_the_one_before(self):
        start, end = get_billing_cycle_bounds(5, 14, 30, datetime(2026, 9, 20, 8, 0), previous=True)
        assert start == datetime(2026, 8, 5, 14, 30)
        assert end == datetime(2026, 9, 5, 14, 30)

    def test_year_boundary(self):
        start, end = get_billing_cycle_bounds(20, 6, 0, datetime(2026, 1, 5, 3, 0))
        assert start == datetime(2025, 12, 20, 6, 0)
        assert end == datetime(2026, 1, 20, 6, 0)

    def test_anchor_day_one_is_a_single_calendar_month(self):
        # The old get_billing_cycle_dates had a bug here (two-month window).
        start, end = get_billing_cycle_bounds(1, 0, 0, datetime(2026, 9, 15, 0, 0))
        assert start == datetime(2026, 9, 1, 0, 0)
        assert end == datetime(2026, 10, 1, 0, 0)


@pytest.mark.asyncio
async def test_billing_anchor_time_round_trips_with_clamping_and_defaults(session):
    # default before anything is stored
    assert await AnalyticsEngine.get_billing_anchor_time(session) == (0, 0)

    stored = await AnalyticsEngine.set_billing_anchor_time(session, 14, 30)
    assert stored == (14, 30)
    assert await AnalyticsEngine.get_billing_anchor_time(session) == (14, 30)

    # out-of-range values are clamped, not rejected
    assert await AnalyticsEngine.set_billing_anchor_time(session, 99, -5) == (23, 0)
    assert await AnalyticsEngine.get_billing_anchor_time(session) == (23, 0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_billing_quota.py -q -k "Bounds or anchor_time"`
Expected: FAIL — `ImportError: cannot import name 'get_billing_cycle_bounds'` / `AttributeError: ... 'get_billing_anchor_time'`.

- [ ] **Step 3: Implement `get_billing_cycle_bounds`**

In `backend/app/services/analytics_engine.py`, change the import line `:3`:

```python
from datetime import date, datetime, timedelta
```

Immediately **after** the existing `get_billing_cycle_dates` function (ends at `:80` with `return (start_date, end_date)`), add:

```python
def get_billing_cycle_bounds(
    anchor_day: int,
    anchor_hour: int,
    anchor_minute: int,
    ref_dt: datetime,
    previous: bool = False,
) -> Tuple[datetime, datetime]:
    """Router-local start (inclusive) and end (exclusive) of an ISP billing cycle.

    ``end_dt`` is the next cycle's reset instant, so the current cycle is the
    half-open interval ``[start_dt, end_dt)``. Unlike the date-only
    :func:`get_billing_cycle_dates`, this is time-aware: on the anchor day
    itself the cycle you are in depends on whether ``ref_dt`` has passed the
    reset time yet.
    """
    day = max(1, min(anchor_day, 31))
    hh = max(0, min(anchor_hour, 23))
    mm = max(0, min(anchor_minute, 59))

    def reset_on(year: int, month: int) -> datetime:
        last = calendar.monthrange(year, month)[1]
        return datetime(year, month, min(day, last), hh, mm)

    this_month = reset_on(ref_dt.year, ref_dt.month)
    if ref_dt >= this_month:
        start = this_month
    elif ref_dt.month == 1:
        start = reset_on(ref_dt.year - 1, 12)
    else:
        start = reset_on(ref_dt.year, ref_dt.month - 1)

    if start.month == 12:
        end = reset_on(start.year + 1, 1)
    else:
        end = reset_on(start.year, start.month + 1)

    if previous:
        prev_end = start
        if start.month == 1:
            prev_start = reset_on(start.year - 1, 12)
        else:
            prev_start = reset_on(start.year, start.month - 1)
        return (prev_start, prev_end)

    return (start, end)
```

- [ ] **Step 4: Implement the anchor-time settings**

In `class AnalyticsEngine`, right after `set_billing_anchor_day` (ends at `:147`):

```python
    @staticmethod
    async def get_billing_anchor_time(session: AsyncSession) -> Tuple[int, int]:
        """The configured reset time of day as ``(hour, minute)``.

        Defaults to midnight, which reproduces the pre-existing date-only
        behaviour exactly, so an install that never set a time is unaffected.
        A stored value that is not a valid integer falls back to 0 rather than
        raising, matching how ``get_billing_anchor_day`` handles corruption.
        """
        hour = 0
        minute = 0
        h_setting = await session.get(AppSetting, "billing_cycle_anchor_hour")
        m_setting = await session.get(AppSetting, "billing_cycle_anchor_minute")
        if h_setting and h_setting.value:
            try:
                hour = max(0, min(int(h_setting.value), 23))
            except ValueError:
                hour = 0
        if m_setting and m_setting.value:
            try:
                minute = max(0, min(int(m_setting.value), 59))
            except ValueError:
                minute = 0
        return (hour, minute)

    @staticmethod
    async def set_billing_anchor_time(session: AsyncSession, hour: int, minute: int) -> Tuple[int, int]:
        """Persist the reset time of day, clamped to a valid wall-clock time."""
        hh = max(0, min(hour, 23))
        mm = max(0, min(minute, 59))
        for key, value, desc in (
            ("billing_cycle_anchor_hour", hh, "ISP billing cycle reset hour (0-23), router-local"),
            ("billing_cycle_anchor_minute", mm, "ISP billing cycle reset minute (0-59)"),
        ):
            setting = await session.get(AppSetting, key)
            if setting:
                setting.value = str(value)
            else:
                session.add(AppSetting(key=key, value=str(value), description=desc))
        await session.commit()
        return (hh, mm)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_billing_quota.py -q -k "Bounds or anchor_time"`
Expected: PASS (9 cases).

- [ ] **Step 6: Create migration 014**

`backend/migrations/versions/014_billing_anchor_time.py`:

```python
"""billing cycle reset time (hours + minutes)

The reset time of day is stored as two ``app_settings`` rows
(``billing_cycle_anchor_hour`` / ``billing_cycle_anchor_minute``), read with a
midnight default. There is no schema change; this revision only keeps the
Alembic head chain linear and documents when the keys were introduced.

Revision ID: 014_billing_anchor_time
Revises: 013_containers_router_traffic
Create Date: 2026-09-01 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op  # noqa: F401  (kept for symmetry with the other revisions)

revision: str = "014_billing_anchor_time"
down_revision: Union[str, None] = "013_containers_router_traffic"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
```

- [ ] **Step 7: Run the migration test**

Run: `python -m pytest tests/test_alembic_migrations.py -q`
Expected: PASS.
Also run: `python -m alembic -c backend/alembic.ini heads` → exactly one line, `014_billing_anchor_time (head)`.

- [ ] **Step 8: Full check + commit**

Run: `python -m pytest tests -q && python -m ruff check backend tests`
Expected: all pass, ruff clean.

```bash
git add backend/app/services/analytics_engine.py backend/migrations/versions/014_billing_anchor_time.py tests/test_billing_quota.py
git commit -m "add time-of-day to the billing cycle anchor: bounds helper + settings"
```

---

## Task 2: `slice_of_day_bytes` and `resolve_monitored_interfaces`

**Files:**
- Modify: `backend/app/services/rollups.py` (imports at `:14-25`; add functions at end of file, after `sum_window` which ends at `:131`)
- Test: `tests/test_billing_quota.py` (add a class; new imports)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `resolve_monitored_interfaces(session, router_id: Optional[int]) -> list[str]` — the WAN interface names for a router from `AppSetting` `monitored_interfaces_{id}` / `monitored_interfaces_default`, JSON list, default `["ether1"]`.
  - `slice_of_day_bytes(session, router_id: Optional[int], day: date, from_time: Optional[time], to_time: Optional[time], interfaces: list[str]) -> Optional[Volume]` — combined `(bytes_in, bytes_out)` transferred on `day` between the two clock times, summed across `interfaces`, reconstructed from `interface_metrics` cumulative counters. `None` bound = start / end of day. Returns `None` when no interface has a sample in the window.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_billing_quota.py`. Extend imports:

```python
from datetime import time as dtime

from backend.app.db.models import InterfaceMetric
from backend.app.services.rollups import resolve_monitored_interfaces, slice_of_day_bytes
```

Append:

```python
class TestSliceOfDayBytes:
    async def _seed(self, session, samples, interface="ether1", router_id=1):
        """samples: list of (datetime, rx_total, tx_total)."""
        for ts, rx, tx in samples:
            session.add(InterfaceMetric(
                router_id=router_id, interface_name=interface,
                rx_rate_bps=0.0, tx_rate_bps=0.0,
                rx_bytes_total=rx, tx_bytes_total=tx, timestamp=ts,
            ))
        await session.commit()

    @pytest.mark.asyncio
    async def test_clean_partial_day_is_a_forward_counter_delta(self, session):
        day = date(2026, 9, 5)
        await self._seed(session, [
            (datetime(2026, 9, 5, 0, 0), 1_000, 100),
            (datetime(2026, 9, 5, 12, 0), 5_000, 300),
            (datetime(2026, 9, 5, 14, 30), 9_000, 800),
            (datetime(2026, 9, 5, 23, 0), 20_000, 2_000),
        ])
        # 00:00 -> 14:30 : (9_000 - 1_000, 800 - 100)
        got = await slice_of_day_bytes(session, 1, day, None, dtime(14, 30), ["ether1"])
        assert got == (8_000, 700)

    @pytest.mark.asyncio
    async def test_a_reboot_mid_slice_drops_only_the_backwards_step(self, session):
        day = date(2026, 9, 5)
        await self._seed(session, [
            (datetime(2026, 9, 5, 0, 0), 10_000, 1_000),
            (datetime(2026, 9, 5, 4, 0), 30_000, 3_000),   # +20_000 / +2_000
            (datetime(2026, 9, 5, 5, 0), 200, 50),          # reboot: counter reset
            (datetime(2026, 9, 5, 10, 0), 7_000, 900),      # +6_800 / +850
        ])
        got = await slice_of_day_bytes(session, 1, day, None, None, ["ether1"])
        assert got == (20_000 + 6_800, 2_000 + 850)

    @pytest.mark.asyncio
    async def test_multiple_interfaces_are_summed(self, session):
        day = date(2026, 9, 5)
        await self._seed(session, [
            (datetime(2026, 9, 5, 0, 0), 0, 0),
            (datetime(2026, 9, 5, 6, 0), 1_000, 100),
        ], interface="ether1")
        await self._seed(session, [
            (datetime(2026, 9, 5, 0, 0), 0, 0),
            (datetime(2026, 9, 5, 6, 0), 4_000, 400),
        ], interface="pppoe-out1")
        got = await slice_of_day_bytes(session, 1, day, None, None, ["ether1", "pppoe-out1"])
        assert got == (5_000, 500)

    @pytest.mark.asyncio
    async def test_a_day_with_no_samples_returns_none(self, session):
        got = await slice_of_day_bytes(session, 1, date(2026, 1, 1), None, None, ["ether1"])
        assert got is None

    @pytest.mark.asyncio
    async def test_from_time_bound_excludes_earlier_samples(self, session):
        day = date(2026, 9, 5)
        await self._seed(session, [
            (datetime(2026, 9, 5, 8, 0), 1_000, 100),
            (datetime(2026, 9, 5, 14, 30), 9_000, 800),
            (datetime(2026, 9, 5, 20, 0), 12_000, 1_100),
        ])
        # 14:30 -> end : (12_000 - 9_000, 1_100 - 800)
        got = await slice_of_day_bytes(session, 1, day, dtime(14, 30), None, ["ether1"])
        assert got == (3_000, 300)


@pytest.mark.asyncio
async def test_resolve_monitored_interfaces_reads_the_setting_or_defaults(session):
    assert await resolve_monitored_interfaces(session, 1) == ["ether1"]
    session.add(AppSetting(key="monitored_interfaces_1", value='["ether1", "pppoe-out1"]'))
    await session.commit()
    assert await resolve_monitored_interfaces(session, 1) == ["ether1", "pppoe-out1"]
    # router_id None uses the _default key
    session.add(AppSetting(key="monitored_interfaces_default", value='["wan"]'))
    await session.commit()
    assert await resolve_monitored_interfaces(session, None) == ["wan"]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_billing_quota.py -q -k "SliceOfDay or monitored_interfaces"`
Expected: FAIL — `ImportError: cannot import name 'slice_of_day_bytes'`.

- [ ] **Step 3: Implement in `rollups.py`**

Change the imports block (`:14-25`) to:

```python
import json
from datetime import date, datetime, time
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    AppSetting,
    DeviceTrafficRollup,
    InterfaceMetric,
    RouterSelfTrafficRollup,
    RouterTrafficRollup,
    TrafficRollup,
)
```

At the end of the file (after `sum_window`):

```python
async def resolve_monitored_interfaces(
    session: AsyncSession, router_id: Optional[int]
) -> List[str]:
    """WAN interface names for a router, from the same setting the gateway
    rollups are measured on, so a slice and the daily total describe the same
    link. Defaults to ``["ether1"]`` when nothing is configured."""
    key = f"monitored_interfaces_{router_id}" if router_id else "monitored_interfaces_default"
    setting = await session.get(AppSetting, key)
    if setting and setting.value:
        try:
            names = json.loads(setting.value)
            if isinstance(names, list) and names:
                return [str(n) for n in names]
        except (json.JSONDecodeError, TypeError):
            pass
    return ["ether1"]


async def slice_of_day_bytes(
    session: AsyncSession,
    router_id: Optional[int],
    day: date,
    from_time: Optional[time],
    to_time: Optional[time],
    interfaces: List[str],
) -> Optional[Tuple[int, int]]:
    """Bytes transferred on ``day`` between two clock times, from the sampled
    WAN interface counters.

    ``interface_metrics`` records each interface's *cumulative* rx/tx byte
    counter about every 1.5 s. Walking every sample in the window and summing
    ``max(0, curr - prev)`` per interface means an intermediate router reboot
    shows up as one negative step that is dropped, rather than corrupting the
    whole slice. Bytes between a window edge and the nearest sample are
    unattributed - at ~1.5 s spacing that is a couple of seconds per edge, far
    below the rounding in every GB figure.

    Returns ``None`` when no interface has a sample in the window (the samples
    have been pruned - retention is 30 days), so the caller can fall back to the
    whole-day rollup.
    """
    if not interfaces:
        return None
    lo = datetime.combine(day, from_time or time(0, 0, 0))
    hi = datetime.combine(day, to_time or time(23, 59, 59, 999999))

    stmt = (
        select(InterfaceMetric)
        .where(InterfaceMetric.interface_name.in_(interfaces))
        .where(InterfaceMetric.timestamp >= lo)
        .where(InterfaceMetric.timestamp <= hi)
        .order_by(InterfaceMetric.interface_name, InterfaceMetric.timestamp)
    )
    if router_id is not None:
        stmt = stmt.where(
            (InterfaceMetric.router_id == router_id) | (InterfaceMetric.router_id.is_(None))
        )

    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        return None

    total_in = total_out = 0
    prev: Dict[str, Tuple[int, int]] = {}
    for row in rows:
        last = prev.get(row.interface_name)
        if last is not None:
            total_in += max(0, row.rx_bytes_total - last[0])
            total_out += max(0, row.tx_bytes_total - last[1])
        prev[row.interface_name] = (row.rx_bytes_total, row.tx_bytes_total)
    return (total_in, total_out)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_billing_quota.py -q -k "SliceOfDay or monitored_interfaces"`
Expected: PASS (6 cases).

- [ ] **Step 5: Full check + commit**

Run: `python -m pytest tests -q && python -m ruff check backend tests`

```bash
git add backend/app/services/rollups.py tests/test_billing_quota.py
git commit -m "add slice_of_day_bytes: reconstruct a partial day from sampled WAN counters"
```

---

## Task 3: `get_billing_cycle_dates` shim + `resolve_date_range` anchor time

**Files:**
- Modify: `backend/app/services/analytics_engine.py` — replace the body of `get_billing_cycle_dates` (`:32-80`); add params to `resolve_date_range` (`:83-118`)
- Test: `tests/test_analytics_and_device_limits.py` — extend `test_billing_cycle_date_calculation`, add a `resolve_date_range` billing case

**Interfaces:**
- Consumes: `get_billing_cycle_bounds` (Task 1).
- Produces:
  - `get_billing_cycle_dates(anchor_day, reference_date=None, previous=False) -> Tuple[date, date]` — **unchanged signature**, now derived from `get_billing_cycle_bounds` at midnight: `start = bounds_start.date()`, `end = (bounds_end - 1µs).date()` (the inclusive last calendar day the cycle touches). Fixes the `anchor_day == 1` two-month-window bug.
  - `resolve_date_range(preset, start_date=None, end_date=None, anchor_day=1, anchor_hour=0, anchor_minute=0, today=None, now_dt=None) -> Tuple[date, date, str]` — new keyword params `anchor_hour`, `anchor_minute`, `now_dt`; billing presets widen to `bounds_start.date() .. min((bounds_end - 1µs).date(), today)`.

- [ ] **Step 1: Write / adjust the failing tests**

In `tests/test_analytics_and_device_limits.py`, **append** to `test_billing_cycle_date_calculation` (after the existing new-year assertions):

```python
    # Regression: anchor_day 1 is a single calendar month, not two.
    # (The pre-shim implementation returned end_date in the *next* month.)
    m_start, m_end = get_billing_cycle_dates(anchor_day=1, reference_date=date(2026, 9, 15), previous=False)
    assert m_start == date(2026, 9, 1)
    assert m_end == date(2026, 9, 30)
```

Add a new test:

```python
def test_resolve_date_range_billing_current_widens_to_the_reset_day():
    from backend.app.services.analytics_engine import resolve_date_range

    # anchor day 5 at 14:30, "today" is the 20th -> cycle Sep 5 .. Oct 5,
    # widened so both partial boundary days are covered; capped at today.
    s, e, lbl = resolve_date_range(
        "billing_current", anchor_day=5, anchor_hour=14, anchor_minute=30,
        today=date(2026, 9, 20),
    )
    assert lbl == "billing_current"
    assert s == date(2026, 9, 5)
    assert e == date(2026, 9, 20)  # min(Oct 5, today)

    # Midnight anchor keeps the old inclusive-last-full-day end.
    s2, e2, _ = resolve_date_range(
        "billing_previous", anchor_day=15, today=date(2026, 8, 29),
    )
    assert s2 == date(2026, 7, 15)
    assert e2 == date(2026, 8, 14)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_analytics_and_device_limits.py -q -k "billing_cycle_date or billing_current_widens"`
Expected: FAIL — the `anchor_day=1` assertion fails (`end == date(2026, 10, 31)`), and `resolve_date_range` rejects the `anchor_hour` kwarg.

- [ ] **Step 3: Replace `get_billing_cycle_dates` body**

Replace the whole function (`:32-80`) with:

```python
def get_billing_cycle_dates(
    anchor_day: int, reference_date: Optional[date] = None, previous: bool = False
) -> Tuple[date, date]:
    """Inclusive first and last *calendar dates* an ISP billing cycle touches.

    A thin date-granular view of :func:`get_billing_cycle_bounds` at midnight.
    Kept for callers that only think in whole days (range presets, alert-state
    keying). For anything that needs the reset time - the quota's "used" figure
    and its countdown - use ``get_billing_cycle_bounds`` with the real anchor
    time and ``router_local_now``.
    """
    ref = reference_date or date.today()
    start_dt, end_dt = get_billing_cycle_bounds(
        anchor_day, 0, 0, datetime.combine(ref, datetime.min.time()), previous
    )
    return (start_dt.date(), (end_dt - timedelta(microseconds=1)).date())
```

Note this must be defined **after** `get_billing_cycle_bounds`. If `get_billing_cycle_bounds` currently sits after `get_billing_cycle_dates` (from Task 1), move `get_billing_cycle_bounds` above `get_billing_cycle_dates` so the reference resolves at call time regardless — both are module-level `def`s, so ordering only matters for readability, but keep `bounds` first.

- [ ] **Step 4: Add anchor-time params to `resolve_date_range`**

Change the signature (`:83-88`) and the two billing branches. New signature:

```python
def resolve_date_range(
    preset: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    anchor_day: int = 1,
    anchor_hour: int = 0,
    anchor_minute: int = 0,
    today: Optional[date] = None,
    now_dt: Optional[datetime] = None,
) -> Tuple[date, date, str]:
```

Replace the `billing_current` / `billing_previous` branches (currently `:107-113`):

```python
    elif preset == "billing_current":
        ref = now_dt or datetime.combine(today, datetime.min.time())
        s_dt, e_dt = get_billing_cycle_bounds(anchor_day, anchor_hour, anchor_minute, ref, previous=False)
        e_date = (e_dt - timedelta(microseconds=1)).date()
        return (s_dt.date(), min(e_date, today), "billing_current")
    elif preset == "billing_previous":
        ref = now_dt or datetime.combine(today, datetime.min.time())
        s_dt, e_dt = get_billing_cycle_bounds(anchor_day, anchor_hour, anchor_minute, ref, previous=True)
        return (s_dt.date(), (e_dt - timedelta(microseconds=1)).date(), "billing_previous")
```

(`today` is already guaranteed non-None by the `today = today or date.today()` line at the top of the function.)

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_analytics_and_device_limits.py -q -k "billing_cycle_date or billing_current_widens or resolve_date_range"`
Expected: PASS.

- [ ] **Step 6: Full check + commit**

Run: `python -m pytest tests -q && python -m ruff check backend tests`
Expected: all pass. (Watch for any other test asserting the old `anchor_day=1` two-month window — none exists in the repo at plan time, but if one appears, it is asserting the bug; update it to the one-month result and note it in the commit body.)

```bash
git add backend/app/services/analytics_engine.py tests/test_analytics_and_device_limits.py
git commit -m "derive billing-cycle dates from the datetime bounds; fixes the day-1 two-month window"
```

---

## Task 4: Schema fields

**Files:**
- Modify: `backend/app/schemas/analytics.py` — `BillingCycleConfig` (`:65-67`), `QuotaStatusDTO` (add near `cycle_end` at `:22-23`), `TrafficAnalyticsResponse` (`:186-190`)
- Test: `tests/test_billing_quota.py` (a small schema test)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `BillingCycleConfig` gains `anchor_hour: int = Field(0, ge=0, le=23)` and `anchor_minute: int = Field(0, ge=0, le=59)`.
  - `QuotaStatusDTO` gains `cycle_end_at: Optional[datetime] = None` — the exact router-local reset instant.
  - `TrafficAnalyticsResponse` gains `billing_anchor_time: str = "00:00"` — `"HH:MM"`, for display next to `billing_anchor_day`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_billing_quota.py`:

```python
def test_billing_cycle_config_accepts_a_time_and_rejects_out_of_range():
    from pydantic import ValidationError
    from backend.app.schemas.analytics import BillingCycleConfig

    # default is midnight
    assert BillingCycleConfig(anchor_day=5).anchor_hour == 0
    assert BillingCycleConfig(anchor_day=5).anchor_minute == 0

    cfg = BillingCycleConfig(anchor_day=5, anchor_hour=14, anchor_minute=30)
    assert (cfg.anchor_hour, cfg.anchor_minute) == (14, 30)

    with pytest.raises(ValidationError):
        BillingCycleConfig(anchor_day=5, anchor_hour=24)
    with pytest.raises(ValidationError):
        BillingCycleConfig(anchor_day=5, anchor_minute=60)


def test_quota_status_dto_has_a_precise_reset_instant_field():
    from backend.app.schemas.analytics import QuotaStatusDTO

    assert QuotaStatusDTO().cycle_end_at is None
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_billing_quota.py -q -k "config_accepts_a_time or precise_reset_instant"`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'anchor_hour'` / `AttributeError: cycle_end_at`.

- [ ] **Step 3: Edit the schemas**

`backend/app/schemas/analytics.py`. Ensure `datetime` is imported (line 1 is `from datetime import date`) → change to:

```python
from datetime import date, datetime
```

`BillingCycleConfig` (`:65-67`) becomes:

```python
class BillingCycleConfig(BaseModel):
    """Configuration for the ISP monthly billing cycle: anchor day and, optionally, time of day."""
    anchor_day: int = Field(default=1, ge=1, le=31, description="Day of the month when ISP traffic counters reset (1-31)")
    anchor_hour: int = Field(default=0, ge=0, le=23, description="Hour of the reset, router-local (0-23)")
    anchor_minute: int = Field(default=0, ge=0, le=59, description="Minute of the reset (0-59)")
```

`QuotaStatusDTO` — after the `cycle_end: Optional[date] = None` line (`:23`), add:

```python
    # The exact router-local instant the current cycle resets. Lets the UI show
    # a countdown finer than whole days when the reset is not at midnight.
    cycle_end_at: Optional[datetime] = None
```

`TrafficAnalyticsResponse` — after `billing_anchor_day: int` (`:188`), add:

```python
    billing_anchor_time: str = "00:00"  # "HH:MM", router-local; "00:00" = day-only
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_billing_quota.py -q -k "config_accepts_a_time or precise_reset_instant"`
Expected: PASS.

- [ ] **Step 5: Full check + commit**

Run: `python -m pytest tests -q && python -m ruff check backend tests`

```bash
git add backend/app/schemas/analytics.py tests/test_billing_quota.py
git commit -m "schema: billing anchor time, and the precise cycle-end instant on the quota DTO"
```

---

## Task 5: `build_quota_status` precise boundary + endpoint config

**Files:**
- Modify: `backend/app/api/v1/endpoints/analytics.py` — imports (`:14-22`), `get_traffic_analytics` (`:45-53`), `get_billing_cycle_config` (`:67-72`), `set_billing_cycle_config` (`:117-123`), `build_quota_status` (`:126-227`)
- Test: `tests/test_analytics_and_device_limits.py` (new class)

**Interfaces:**
- Consumes: `get_billing_cycle_bounds`, `AnalyticsEngine.get_billing_anchor_time` / `set_billing_anchor_time` (Task 1); `resolve_monitored_interfaces`, `slice_of_day_bytes` (Task 2); `BillingCycleConfig.anchor_hour/anchor_minute`, `QuotaStatusDTO.cycle_end_at` (Task 4); `TrafficAnalyticsResponse.billing_anchor_time` (Task 4).
- Produces: `GET/POST /api/v1/analytics/billing-cycle` round-trip `anchor_hour` + `anchor_minute`; `GET /api/v1/analytics/quota` returns `cycle_end_at` and a boundary-exact `used_bytes`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_analytics_and_device_limits.py`. Extend the imports:

```python
from datetime import date, datetime, time, timedelta

from backend.app.db.models import AppSetting, InterfaceMetric, RouterTrafficRollup
from backend.app.services.router_time import ROUTER_OFFSET_SETTING_KEY
```

New class (uses the existing `api_client` fixture, which exposes `session_factory`):

```python
GB = 1024 ** 3


class TestQuotaBoundaryPrecision:
    async def _configure(self, session, *, limit_gb, anchor_day, hour=0, minute=0):
        await session.execute(
            __import__("sqlalchemy").text("DELETE FROM app_settings")
        )
        # router clock == container clock, so router_local_now is predictable
        session.add(AppSetting(key=ROUTER_OFFSET_SETTING_KEY, value="0"))
        session.add(AppSetting(key="billing_cycle_anchor_day", value=str(anchor_day)))
        session.add(AppSetting(key="billing_cycle_anchor_hour", value=str(hour)))
        session.add(AppSetting(key="billing_cycle_anchor_minute", value=str(minute)))
        session.add(AppSetting(key="isp_quota_limit_bytes", value=str(limit_gb * GB)))
        await session.commit()

    async def _daily_gateway(self, session, day, total_bytes, router_id=1):
        session.add(RouterTrafficRollup(
            router_id=router_id, record_date=day,
            bytes_in=total_bytes, bytes_out=0,
        ))
        await session.commit()

    async def _samples(self, session, day, points, interface="ether1", router_id=1):
        for hh, mm, rx in points:
            session.add(InterfaceMetric(
                router_id=router_id, interface_name=interface,
                rx_rate_bps=0.0, tx_rate_bps=0.0,
                rx_bytes_total=rx, tx_bytes_total=0,
                timestamp=datetime.combine(day, time(hh, mm)),
            ))
        await session.commit()

    @pytest.mark.asyncio
    async def test_pre_reset_traffic_on_the_start_day_is_subtracted_from_used(self, api_client, monkeypatch):
        from backend.app.api.v1.endpoints import analytics as analytics_ep

        # Freeze "now" to the 10th of the month at noon.
        frozen = datetime(2026, 9, 10, 12, 0)
        monkeypatch.setattr(analytics_ep, "router_local_now", _fake_now(frozen))
        monkeypatch.setattr(analytics_ep, "router_local_date", _fake_date(frozen.date()))

        async with api_client.session_factory() as s:
            await self._configure(s, limit_gb=100, anchor_day=5, hour=14, minute=30)
            # cycle start day (Sep 5): whole-day rollup 10 GB, of which the WAN
            # counter shows 6 GB moved before 14:30.
            await self._daily_gateway(s, date(2026, 9, 5), 10 * GB)
            await self._daily_gateway(s, date(2026, 9, 6), 20 * GB)
            await self._daily_gateway(s, date(2026, 9, 10), 5 * GB)
            await self._samples(s, date(2026, 9, 5), [
                (0, 0, 0), (14, 30, 6 * GB), (23, 0, 10 * GB),
            ])

        resp = await api_client.get("/api/v1/analytics/quota")
        q = resp.json()["data"]
        # 10 + 20 + 5 = 35 GB whole days, minus the 6 GB pre-reset slice = 29 GB
        assert abs(q["used_bytes"] - 29 * GB) < 1024 * 1024
        assert q["cycle_end_at"].startswith("2026-10-05T14:30")

    @pytest.mark.asyncio
    async def test_falls_back_to_the_whole_day_when_samples_are_pruned(self, api_client, monkeypatch):
        from backend.app.api.v1.endpoints import analytics as analytics_ep
        frozen = datetime(2026, 9, 10, 12, 0)
        monkeypatch.setattr(analytics_ep, "router_local_now", _fake_now(frozen))
        monkeypatch.setattr(analytics_ep, "router_local_date", _fake_date(frozen.date()))

        async with api_client.session_factory() as s:
            await self._configure(s, limit_gb=100, anchor_day=5, hour=14, minute=30)
            await self._daily_gateway(s, date(2026, 9, 5), 10 * GB)
            await self._daily_gateway(s, date(2026, 9, 10), 5 * GB)
            # no interface_metrics rows for Sep 5 -> slice returns None

        resp = await api_client.get("/api/v1/analytics/quota")
        q = resp.json()["data"]
        assert abs(q["used_bytes"] - 15 * GB) < 1024 * 1024  # whole start day kept

    @pytest.mark.asyncio
    async def test_midnight_anchor_reproduces_the_pre_change_number(self, api_client, monkeypatch):
        from backend.app.api.v1.endpoints import analytics as analytics_ep
        frozen = datetime(2026, 9, 10, 12, 0)
        monkeypatch.setattr(analytics_ep, "router_local_now", _fake_now(frozen))
        monkeypatch.setattr(analytics_ep, "router_local_date", _fake_date(frozen.date()))

        async with api_client.session_factory() as s:
            await self._configure(s, limit_gb=100, anchor_day=5, hour=0, minute=0)
            await self._daily_gateway(s, date(2026, 9, 5), 10 * GB)
            await self._daily_gateway(s, date(2026, 9, 10), 5 * GB)
            # samples exist but must be ignored at a 00:00 anchor
            await self._samples(s, date(2026, 9, 5), [(0, 0, 0), (23, 0, 10 * GB)])

        resp = await api_client.get("/api/v1/analytics/quota")
        q = resp.json()["data"]
        assert abs(q["used_bytes"] - 15 * GB) < 1024 * 1024
        assert q["cycle_end_at"].startswith("2026-10-05T00:00")

    @pytest.mark.asyncio
    async def test_billing_cycle_config_endpoint_round_trips_the_time(self, api_client):
        resp = await api_client.post(
            "/api/v1/analytics/billing-cycle",
            json={"anchor_day": 5, "anchor_hour": 14, "anchor_minute": 30},
        )
        assert resp.status_code == 200
        d = resp.json()["data"]
        assert (d["anchor_day"], d["anchor_hour"], d["anchor_minute"]) == (5, 14, 30)

        got = (await api_client.get("/api/v1/analytics/billing-cycle")).json()["data"]
        assert (got["anchor_hour"], got["anchor_minute"]) == (14, 30)
```

Helpers at module level in the test file (near the top, after imports):

```python
def _fake_now(dt):
    async def _inner(_session):
        return dt
    return _inner


def _fake_date(d):
    async def _inner(_session, now_utc=None):
        return d
    return _inner
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_analytics_and_device_limits.py -q -k "QuotaBoundaryPrecision"`
Expected: FAIL — `cycle_end_at` missing / `used_bytes` off by the pre-reset slice / config endpoint ignores the time fields.

- [ ] **Step 3: Update the imports and the two config endpoints**

`backend/app/api/v1/endpoints/analytics.py`. Imports block:

```python
from datetime import date, datetime, time, timedelta

from backend.app.services.analytics_engine import (
    AnalyticsEngine,
    get_billing_cycle_bounds,
    get_billing_cycle_dates,
    resolve_date_range,
)
from backend.app.services.rollups import resolve_monitored_interfaces, slice_of_day_bytes
from backend.app.services.router_time import router_local_date, router_local_now
```

(Keep the other existing imports.) `math` is **not** needed — use `int(x) + (1 if x > int(x) else 0)` for a ceil, or `-(-a // b)` on ints; the plan below uses `int()` on a positive float plus a remainder check.

`get_billing_cycle_config` (`:67-72`):

```python
@router.get("/billing-cycle", response_model=APIResponse[BillingCycleConfig])
async def get_billing_cycle_config(db: AsyncSession = Depends(get_db)):
    """The ISP billing cycle anchor: day of month, and time of day."""
    anchor_day = await AnalyticsEngine.get_billing_anchor_day(db)
    anchor_hour, anchor_minute = await AnalyticsEngine.get_billing_anchor_time(db)
    return APIResponse(data=BillingCycleConfig(
        anchor_day=anchor_day, anchor_hour=anchor_hour, anchor_minute=anchor_minute,
    ))
```

`set_billing_cycle_config` (`:117-123`):

```python
@router.post("/billing-cycle", response_model=APIResponse[BillingCycleConfig])
async def set_billing_cycle_config(
    payload: BillingCycleConfig,
    db: AsyncSession = Depends(get_db),
):
    """Save the ISP billing cycle renewal day and time of day."""
    saved_day = await AnalyticsEngine.set_billing_anchor_day(db, payload.anchor_day)
    saved_hour, saved_minute = await AnalyticsEngine.set_billing_anchor_time(
        db, payload.anchor_hour, payload.anchor_minute,
    )
    return APIResponse(
        data=BillingCycleConfig(
            anchor_day=saved_day, anchor_hour=saved_hour, anchor_minute=saved_minute,
        ),
        message=f"Billing cycle anchor set to day {saved_day} at {saved_hour:02d}:{saved_minute:02d}",
    )
```

- [ ] **Step 4: Thread anchor time through `get_traffic_analytics`**

In `get_traffic_analytics` (`:45-53`), after `anchor_day = await AnalyticsEngine.get_billing_anchor_day(db)`:

```python
    anchor_hour, anchor_minute = await AnalyticsEngine.get_billing_anchor_time(db)
    resolved_start, resolved_end, range_label = resolve_date_range(
        preset=preset,
        start_date=start_date,
        end_date=end_date,
        anchor_day=anchor_day,
        anchor_hour=anchor_hour,
        anchor_minute=anchor_minute,
        today=await router_local_date(db),
        now_dt=await router_local_now(db),
    )
```

- [ ] **Step 5: Rework `build_quota_status`**

Replace the body from the top of the function down to (but not including) the `# --- "at current pace" ---` comment. Concretely, replace `:126` … the `days_left_after_today = ...` line with:

```python
async def build_quota_status(db: AsyncSession, router_id: Optional[int] = None) -> QuotaStatusDTO:
    """Consumption against the ISP allowance for the current billing cycle.

    Usage is the gateway figure - the number an ISP bills on - for the cycle
    window. When the anchor carries a time of day, the cycle-start date's
    pre-reset slice is subtracted using the sampled WAN counters; if those
    samples have been pruned, the whole start day is kept (documented fallback).
    """
    config = await get_quota_config(db)
    anchor_day = await AnalyticsEngine.get_billing_anchor_day(db)
    anchor_hour, anchor_minute = await AnalyticsEngine.get_billing_anchor_time(db)
    now = await router_local_now(db)
    today = now.date()

    start_dt, end_dt = get_billing_cycle_bounds(anchor_day, anchor_hour, anchor_minute, now, previous=False)
    cycle_start = start_dt.date()
    cycle_end = (end_dt - timedelta(microseconds=1)).date()

    data = await AnalyticsEngine.get_historical_traffic(
        session=db,
        start_date=cycle_start,
        end_date=min(cycle_end, today),
        router_id=router_id,
        range_preset="billing_current",
        anchor_day=anchor_day,
    )
    used = data.gateway.total_bytes

    # Traffic on the cycle-start date that happened *before* the reset instant
    # belongs to the previous cycle. Only the start day needs adjusting: we are
    # always mid-cycle here, so "everything up to now" on any later day is
    # already inside this cycle.
    if start_dt.time() != time(0, 0):
        interfaces = await resolve_monitored_interfaces(db, router_id)
        pre = await slice_of_day_bytes(
            db, router_id, cycle_start, None, start_dt.time(), interfaces,
        )
        if pre is not None:
            used = max(0, used - (pre[0] + pre[1]))

    limit = config.limit_bytes

    # Fractional day counts keep the projection smooth across the reset instead
    # of stepping a whole day.
    DAY = 86400.0
    total_days = max(1e-9, (end_dt - start_dt).total_seconds() / DAY)
    elapsed_days = min(total_days, max(1e-9, (now - start_dt).total_seconds() / DAY))
    days_left_after_today = max(0.0, total_days - elapsed_days)

    remaining_seconds = max(0.0, (end_dt - now).total_seconds())
    days_remaining = int(remaining_seconds // DAY) + (1 if remaining_seconds % DAY else 0)

    cycle_days_total = max(1, round(total_days))
    cycle_days_elapsed = min(cycle_days_total, max(1, round(elapsed_days)))

    avg_per_day = used / elapsed_days
    projected_bytes_linear = int(avg_per_day * total_days)
```

Then, further down, in the previous-cycle block, replace the two lines that compute `prev_start, prev_end` and the `get_historical_traffic` call's date args. Currently:

```python
    prev_start, prev_end = get_billing_cycle_dates(anchor_day, today, previous=True)
```

becomes:

```python
    prev_start_dt, prev_end_dt = get_billing_cycle_bounds(
        anchor_day, anchor_hour, anchor_minute, now, previous=True,
    )
    prev_start = prev_start_dt.date()
    prev_end = (prev_end_dt - timedelta(microseconds=1)).date()
```

Leave the `prev_data = await AnalyticsEngine.get_historical_traffic(... start_date=prev_start, end_date=prev_end ...)` call as-is. Previous-cycle boundary slicing is **out of scope** (samples are almost always pruned by then) — the whole-day figure is fine for the "last cycle avg" context number.

Finally, in the `return QuotaStatusDTO(...)` block, add `cycle_end_at=end_dt` and keep `days_remaining=days_remaining` (now the fractional-aware int). The `projected_daily_budget` line already divides by `days_remaining` — guard it: it currently reads `(max(0, limit - used) // days_remaining) if (limit and days_remaining) else 0`, which still works.

Also change the `projected_bytes_at_pace` line lower down — it uses `days_left_after_today`, which is now a float; `int(used + pace_per_day * days_left_after_today)` still works.

- [ ] **Step 6: Run to verify pass**

Run: `python -m pytest tests/test_analytics_and_device_limits.py -q -k "QuotaBoundaryPrecision"`
Expected: PASS (4 cases).

- [ ] **Step 7: Full check + commit**

Run: `python -m pytest tests -q && python -m ruff check backend tests`
Expected: all pass. If `test_analytics_api_endpoints` (which posts `{"anchor_day": 15}` with no time) fails on the response shape, add `anchor_hour`/`anchor_minute` assertions defaulting to 0 — the DTO now always returns them.

```bash
git add backend/app/api/v1/endpoints/analytics.py tests/test_analytics_and_device_limits.py
git commit -m "quota: slice the cycle-start day at the reset time; carry the reset instant"
```

---

## Task 6: Frontend — billing-cycle modal + API client

**Files:**
- Modify: `frontend/src/api/client.js` (`:130-134`)
- Modify: `frontend/src/components/TrafficAnalytics.jsx` — state (`:61`), load (`:66-74`), save (`:110-125`), header summary (`:205`), modal body (`:475-487`)
- Modify: `frontend/src/i18n/translations.js` — new keys in the `en` block and the `ru` block
- Create: `frontend/src/components/QuotaBillingTime.test.jsx`

**Interfaces:**
- Consumes: `GET/POST /api/v1/analytics/billing-cycle` now carry `anchor_hour`, `anchor_minute` (Task 5).
- Produces: `api.saveBillingCycleConfig(anchorDay, anchorHour, anchorMinute)`; the modal round-trips an `HH:MM` value.

- [ ] **Step 1: Write the failing test**

`frontend/src/components/QuotaBillingTime.test.jsx`:

```jsx
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, renderWithProviders, screen, waitFor } from '../test/render';
import { TrafficAnalytics } from './TrafficAnalytics';
import { api } from '../api/client';

vi.mock('../api/client', () => ({
  api: {
    getBillingCycleConfig: vi.fn(),
    saveBillingCycleConfig: vi.fn().mockResolvedValue({ data: {} }),
    getTrafficAnalytics: vi.fn().mockResolvedValue({ data: null }),
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
  api.getBillingCycleConfig.mockResolvedValue({
    data: { anchor_day: 5, anchor_hour: 14, anchor_minute: 30 },
  });
  api.getTrafficAnalytics.mockResolvedValue({ data: null });
  api.saveBillingCycleConfig.mockResolvedValue({ data: {} });
});

describe('billing-cycle reset time', () => {
  it('loads the stored HH:MM into the modal and saves an edited value', async () => {
    renderWithProviders(<TrafficAnalytics activeRouter={{ id: 1 }} />);

    // open the billing-cycle modal (the summary line is a button)
    const opener = await screen.findByText(/Day 5/i);
    fireEvent.click(opener);

    const timeInput = await screen.findByLabelText(/reset time/i);
    expect(timeInput.value).toBe('14:30');

    fireEvent.change(timeInput, { target: { value: '09:15' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() =>
      expect(api.saveBillingCycleConfig).toHaveBeenCalledWith(5, 9, 15)
    );
  });

  it('shows Day-only in the summary when the reset is at midnight', async () => {
    api.getBillingCycleConfig.mockResolvedValue({
      data: { anchor_day: 5, anchor_hour: 0, anchor_minute: 0 },
    });
    renderWithProviders(<TrafficAnalytics activeRouter={{ id: 1 }} />);
    expect(await screen.findByText(/Day 5/i)).toBeInTheDocument();
    expect(screen.queryByText(/Day 5 ·/)).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/components/QuotaBillingTime.test.jsx`
Expected: FAIL — no `reset time` label; `saveBillingCycleConfig` called with one arg.

- [ ] **Step 3: API client**

`frontend/src/api/client.js` (`:130-134`):

```javascript
  getBillingCycleConfig: () => request('/analytics/billing-cycle'),
  saveBillingCycleConfig: (anchorDay, anchorHour = 0, anchorMinute = 0) => request('/analytics/billing-cycle', {
    method: 'POST',
    body: JSON.stringify({
      anchor_day: Number(anchorDay),
      anchor_hour: Number(anchorHour),
      anchor_minute: Number(anchorMinute),
    }),
  }),
```

- [ ] **Step 4: `TrafficAnalytics.jsx` state + load + save**

State (`:61`, next to `const [anchorDay, setAnchorDay] = useState(1);`):

```javascript
  const [anchorHour, setAnchorHour] = useState(0);
  const [anchorMinute, setAnchorMinute] = useState(0);
```

Load (`:66-74`) — inside the `.then`:

```javascript
      .then(res => {
        if (res?.data?.anchor_day) {
          setAnchorDay(res.data.anchor_day);
          setAnchorHour(res.data.anchor_hour ?? 0);
          setAnchorMinute(res.data.anchor_minute ?? 0);
        }
      })
```

Save (`handleSaveBillingCycle`, `:110-125`) — change the call:

```javascript
      await api.saveBillingCycleConfig(anchorDay, anchorHour, anchorMinute);
```

- [ ] **Step 5: `TrafficAnalytics.jsx` header summary + modal input**

Header summary (`:205`), replace:

```jsx
            {t('billing_cycle')}: <strong style={{ color: 'var(--text-primary)' }}>Day {anchorDay}</strong>
```

with:

```jsx
            {t('billing_cycle')}: <strong style={{ color: 'var(--text-primary)' }}>
              {t('billing_summary_day', { day: anchorDay })}
              {(anchorHour !== 0 || anchorMinute !== 0)
                ? ` · ${String(anchorHour).padStart(2, '0')}:${String(anchorMinute).padStart(2, '0')}`
                : ''}
            </strong>
```

Modal body — after the existing `anchor_day` `form-group` (`:475-487`), add:

```jsx
              <div className="form-group">
                <label className="form-label" htmlFor="billing-reset-time">{t('billing_anchor_time')}</label>
                <input
                  id="billing-reset-time"
                  type="time"
                  aria-label={t('billing_anchor_time')}
                  className="form-input font-mono"
                  value={`${String(anchorHour).padStart(2, '0')}:${String(anchorMinute).padStart(2, '0')}`}
                  onChange={e => {
                    const [h, m] = e.target.value.split(':').map(Number);
                    setAnchorHour(Number.isFinite(h) ? Math.min(23, Math.max(0, h)) : 0);
                    setAnchorMinute(Number.isFinite(m) ? Math.min(59, Math.max(0, m)) : 0);
                  }}
                  style={{ height: 38, fontSize: 'var(--fs-lg)' }}
                />
                <p style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', marginTop: 4 }}>
                  {t('billing_anchor_time_hint')}
                </p>
              </div>
```

- [ ] **Step 6: i18n keys**

`frontend/src/i18n/translations.js` — in the `en` block, near `billing_anchor_day`:

```javascript
    billing_summary_day: "Day {day}",
    billing_anchor_time: "Reset time (router-local)",
    billing_anchor_time_hint: "When the ISP resets the counter. Leave at 00:00 for a plain day-only cycle. On the reset day the traffic split is taken from the sampled WAN counters.",
    quota_time_left: "{d}d {h}h left",
```

In the `ru` block, near the RU `billing_anchor_day`:

```javascript
    billing_summary_day: "День {day}",
    billing_anchor_time: "Время сброса (по роутеру)",
    billing_anchor_time_hint: "Когда оператор обнуляет счётчик. Оставьте 00:00 для обычного посуточного цикла. В день сброса трафик делится по сэмплам счётчиков WAN.",
    quota_time_left: "{d}д {h}ч осталось",
```

- [ ] **Step 7: Run to verify pass**

Run: `cd frontend && npx vitest run src/components/QuotaBillingTime.test.jsx`
Expected: PASS (2 cases).

- [ ] **Step 8: Full frontend check + commit**

Run: `cd frontend && npx vitest run && npx vite build && node scripts/check-identifiers.cjs`
Then from repo root, if the i18n checker exists in scratchpad: `node <scratchpad>/i18ncheck.cjs` — expect EN/RU parity, no dups, all `t()` keys resolve.

```bash
git add frontend/src/api/client.js frontend/src/components/TrafficAnalytics.jsx frontend/src/i18n/translations.js frontend/src/components/QuotaBillingTime.test.jsx
git commit -m "ui: optional HH:MM on the billing-cycle anchor"
```

---

## Task 7: Frontend — QuotaStrip precise countdown

**Files:**
- Modify: `frontend/src/components/QuotaStrip.jsx` (`:93-97`)
- Test: `frontend/src/components/QuotaBillingTime.test.jsx` (add a case)

**Interfaces:**
- Consumes: `QuotaStatusDTO.cycle_end_at` (ISO string or null) from `GET /api/v1/analytics/quota` (Task 5); `quota_time_left` i18n key (Task 6).
- Produces: nothing downstream.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/components/QuotaBillingTime.test.jsx`:

```jsx
import { QuotaStrip } from './QuotaStrip';

describe('QuotaStrip countdown', () => {
  const base = {
    enabled: true, used_bytes: 50 * 1024 ** 3, limit_bytes: 100 * 1024 ** 3,
    used_pct: 50, remaining_bytes: 50 * 1024 ** 3, projected_daily_budget: 1024 ** 3,
    on_track: true, projected_pct_linear: 70, pace_basis: 'recent',
    projected_pct_at_pace: 72, days_remaining: 3,
    cycle_start: '2026-09-05', cycle_end: '2026-10-04',
  };

  it('shows whole days when the reset is at midnight (no cycle_end_at)', () => {
    vi.spyOn(api, 'getQuota').mockResolvedValue({ data: { ...base, cycle_end_at: null } });
    renderWithProviders(<QuotaStrip activeRouterId={1} onOpenSettings={() => {}} />);
    return screen.findByText(/3 days left/i);
  });

  it('shows Nd Nh when cycle_end_at is a non-midnight instant', async () => {
    const soon = new Date(Date.now() + (2 * 24 + 14) * 3600 * 1000).toISOString();
    vi.spyOn(api, 'getQuota').mockResolvedValue({ data: { ...base, cycle_end_at: soon } });
    renderWithProviders(<QuotaStrip activeRouterId={1} onOpenSettings={() => {}} />);
    expect(await screen.findByText(/2d 1[34]h left/)).toBeInTheDocument();
  });
});
```

Add `getQuota` to the `api` mock at the top of the file:

```jsx
    getQuota: vi.fn().mockResolvedValue({ data: null }),
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/components/QuotaBillingTime.test.jsx`
Expected: FAIL — the "2d 14h left" text is not rendered.

- [ ] **Step 3: Implement the label switch**

`frontend/src/components/QuotaStrip.jsx`, replace the `quota_days_left` line (`:95`):

```jsx
        {(() => {
          // A non-midnight reset carries a precise instant; show days + hours.
          if (q.cycle_end_at) {
            const ms = new Date(q.cycle_end_at).getTime() - Date.now();
            if (ms > 0) {
              const totalHours = Math.floor(ms / 3_600_000);
              const d = Math.floor(totalHours / 24);
              const h = totalHours % 24;
              if (h !== 0 || d === 0) {
                return t('quota_time_left', { d, h });
              }
            }
          }
          return t('quota_days_left', { days: q.days_remaining });
        })()}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npx vitest run src/components/QuotaBillingTime.test.jsx`
Expected: PASS.

- [ ] **Step 5: Full frontend check + commit**

Run: `cd frontend && npx vitest run && npx vite build && node scripts/check-identifiers.cjs`

```bash
git add frontend/src/components/QuotaStrip.jsx frontend/src/components/QuotaBillingTime.test.jsx
git commit -m "ui: quota strip shows days + hours when the reset is not at midnight"
```

---

## Task 8: README + LESSONS

**Files:**
- Modify: `README.md` — the billing-cycle bullet under "📊 Historical Traffic Accounting & ISP Billing Cycles"
- Modify: `docs/LESSONS.md` — one dated entry

**Interfaces:** none.

- [ ] **Step 1: README**

Find the billing-cycle feature bullet (search `billing cycle anchor` / `anchor day`). Add a sentence:

> The anchor also takes an **optional time of day** (router-local). At a non-midnight reset the cycle-start day's traffic is split at the reset minute using the WAN interface's sampled cumulative counters (`interface_metrics`, ~1.5 s spacing, 30-day retention); when those samples are already pruned it keeps the whole day. Setting a time also fixes a pre-existing bug where `anchor_day = 1` (the default) produced a **two-month** current-cycle window instead of one calendar month.

- [ ] **Step 2: LESSONS**

Append to `docs/LESSONS.md`:

```markdown
**[2026-09-01] Problem:** The ISP billing-cycle anchor was a day of the month
only; some ISPs reset at a specific time. **Solution:** every traffic figure
MikroMan stores is a daily total, so a mid-day reset splits the boundary day
between two cycles and the rollups cannot express that. But `interface_metrics`
samples the WAN interface's *cumulative* byte counter about every 1.5 s
(30-day retention), and the quota is billed on the gateway total - so the
current cycle's start day (always < 30 days old) can be sliced at the reset
minute from those samples: walk every sample in `[00:00, reset]`, sum
`max(0, curr - prev)` per interface (which drops a reboot's backwards step),
subtract from `used`. Previous-cycle boundaries are usually pruned, so that path
degrades to whole-day. Reworking the cycle math onto datetime bounds also
surfaced a latent bug: `get_billing_cycle_dates` returned an end date in the
*next* month for `anchor_day == 1`, giving the default config a two-month
window. Lesson: "we only store daily totals" is not the end of the question -
check whether a finer-grained series exists for the one figure that needs it.
```

- [ ] **Step 3: Commit**

```bash
git add README.md docs/LESSONS.md
git commit -m "docs: billing-cycle reset time"
```

---

## Self-Review

**1. Spec coverage**

| Spec item | Task |
|---|---|
| Two `AppSetting` keys, defaults `(0,0)`, 00:00 == today's behaviour | 1 (settings), 4 (schema validators), 5 (regression test) |
| `get_billing_cycle_bounds` returns router-local datetimes, half-open | 1 |
| `get_billing_cycle_dates` becomes a shim; day-1 bug fixed | 3 |
| `resolve_date_range` widens billing presets to `.date()` bounds | 3 |
| `slice_of_day_bytes` — walk samples, `max(0, Δ)`, `None` when empty | 2 |
| `resolve_monitored_interfaces` | 2 |
| `build_quota_status` subtracts the pre-reset start-day slice; whole-day fallback | 5 |
| fractional internal day counts; `days_remaining` stays int (ceil) | 5 |
| `cycle_end_at` on the DTO | 4 (field), 5 (populated) |
| `BillingCycleConfig` hour/minute; `billing_anchor_time` on the analytics response | 4 |
| endpoint config round-trips the time | 5 |
| migration 014, head chain linear | 1 |
| frontend modal `HH:MM` input + header summary + client | 6 |
| QuotaStrip `{d}d {h}h left` | 7 |
| i18n `quota_time_left` EN+RU (+ the two `billing_anchor_time*` keys) | 6 |
| tests: bounds edges, slice incl. reboot + pruned, quota exactness + fallback + 00:00 regression, fractional countdown, frontend round-trip | 1,2,3,5,6,7 |
| out of scope: per-user/device boundary precision, previous-cycle slicing, configurable retention | not implemented — matches spec |

**2. Placeholder scan**

None. Every code step has real code, every test step has real assertions, no "TBD" / "add error handling" / "similar to Task N".

**3. Type consistency**

- `get_billing_cycle_bounds(anchor_day, anchor_hour, anchor_minute, ref_dt, previous=False) -> (datetime, datetime)` — same call shape in Tasks 1, 3, 5. ✓
- `slice_of_day_bytes(session, router_id, day, from_time, to_time, interfaces) -> Optional[Tuple[int,int]]` — Task 2 defines, Task 5 calls with `(db, router_id, cycle_start, None, start_dt.time(), interfaces)`. ✓
- `resolve_monitored_interfaces(session, router_id) -> list[str]` — Task 2 defines, Task 5 calls `resolve_monitored_interfaces(db, router_id)`. ✓
- `AnalyticsEngine.get_billing_anchor_time(session) -> (int, int)` — Tasks 1, 5. ✓
- `BillingCycleConfig` field names `anchor_hour` / `anchor_minute` — Tasks 4, 5, 6 (`res.data.anchor_hour`). ✓
- `QuotaStatusDTO.cycle_end_at` — Task 4 (field), 5 (`cycle_end_at=end_dt`), 7 (`q.cycle_end_at`). ✓
- `api.saveBillingCycleConfig(anchorDay, anchorHour, anchorMinute)` — Task 6 client + caller + test all three-arg. ✓
- i18n key `quota_time_left` with params `{d}`, `{h}` — Task 6 defines, Task 7 calls `t('quota_time_left', { d, h })`. ✓
- `billing_summary_day` with `{day}` — Task 6 defines and uses. ✓

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-09-01-billing-cycle-reset-time.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
