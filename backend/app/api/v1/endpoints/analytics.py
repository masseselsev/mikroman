from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.session import get_db
from backend.app.schemas.analytics import (
    BillingCycleConfig,
    QuotaConfigDTO,
    QuotaStatusDTO,
    TrafficAnalyticsResponse,
)
from backend.app.schemas.common import APIResponse
from backend.app.services.analytics_engine import (
    AnalyticsEngine,
    get_billing_cycle_bounds,
    inclusive_end_date,
    resolve_date_range,
)
from backend.app.services.interface_rollups import recompute_recent
from backend.app.services.quota import (
    QuotaConfig,
    get_quota_config,
    save_quota_config,
    unfired_for_cycle,
)
from backend.app.services.rollups import resolve_monitored_interfaces, slice_of_day_bytes
from backend.app.services.router_manager import router_manager
from backend.app.services.router_time import get_router_offset, router_local_now

router = APIRouter(prefix="/analytics", tags=["Traffic Analytics"])


@router.get("/traffic", response_model=APIResponse[TrafficAnalyticsResponse])
async def get_traffic_analytics(
    preset: str = Query("7d", description="Range preset: today, yesterday, 7d, 30d, billing_current, billing_previous, custom"),
    start_date: Optional[date] = Query(None, description="Custom start date (YYYY-MM-DD)"),
    end_date: Optional[date] = Query(None, description="Custom end date (YYYY-MM-DD)"),
    router_id: Optional[int] = Query(None, description="Filter by router ID"),
    db: AsyncSession = Depends(get_db)
):
    """Retrieve historical traffic metrics across Gateway, Users, Devices, and Timeline."""
    # Fold the newest samples into the recent rollups so an open dashboard sees
    # near-live figures without waiting for the next background tick.
    try:
        await recompute_recent(db, router_id or 1)
    except Exception:
        pass

    anchor_day = await AnalyticsEngine.get_billing_anchor_day(db)
    anchor_hour, anchor_minute = await AnalyticsEngine.get_billing_anchor_time(db)
    # Presets follow the router's calendar, not the container's UTC one; one
    # clock read gives both the date and the instant the presets need.
    now = await router_local_now(db)
    today = now.date()
    resolved_start, resolved_end, range_label = resolve_date_range(
        preset=preset,
        start_date=start_date,
        end_date=end_date,
        anchor_day=anchor_day,
        anchor_hour=anchor_hour,
        anchor_minute=anchor_minute,
        today=today,
        now_dt=now,
    )

    data = await AnalyticsEngine.get_historical_traffic(
        session=db,
        start_date=resolved_start,
        end_date=resolved_end,
        router_id=router_id,
        range_preset=range_label,
        anchor_day=anchor_day
    )
    return APIResponse(data=data)


@router.get("/billing-cycle", response_model=APIResponse[BillingCycleConfig])
async def get_billing_cycle_config(
    db: AsyncSession = Depends(get_db)
):
    """The ISP billing cycle anchor: day of month, and time of day."""
    anchor_day = await AnalyticsEngine.get_billing_anchor_day(db)
    anchor_hour, anchor_minute = await AnalyticsEngine.get_billing_anchor_time(db)
    return APIResponse(data=BillingCycleConfig(
        anchor_day=anchor_day, anchor_hour=anchor_hour, anchor_minute=anchor_minute,
    ))


@router.get("/debug-state")
async def get_debug_state(db: AsyncSession = Depends(get_db)):
    """Inspect raw queue counters, address-lists, firewall rules, and rollups."""
    from sqlalchemy import select

    from backend.app.db.models import AppSetting, Device, DeviceTrafficRollup, RouterTrafficRollup, TrafficRollup, User

    r_client = await router_manager.get_client(session=db)
    router_queues = []
    router_filters = []
    router_addr = []
    if r_client:
        try:
            raw_q = await r_client.get_simple_queues()
            router_queues = [q.model_dump() for q in raw_q]
            raw_f = await r_client.get_firewall_filter_rules()
            router_filters = [f for f in raw_f if f.get("chain") == "forward"]
            router_addr = await r_client.get_address_list("mikroman_queued")
        except Exception as e:
            router_queues = [{"error": str(e)}]

    user_rollups = (await db.execute(select(TrafficRollup))).scalars().all()
    dev_rollups = (await db.execute(select(DeviceTrafficRollup))).scalars().all()
    r_rollups = (await db.execute(select(RouterTrafficRollup))).scalars().all()
    settings = (await db.execute(select(AppSetting))).scalars().all()
    devices = (await db.execute(select(Device))).scalars().all()
    users = (await db.execute(select(User))).scalars().all()

    return {
        "router_queues": router_queues,
        "router_filters": router_filters,
        "router_addr": router_addr,
        "user_rollups": [{"user_id": r.user_id, "date": str(r.record_date), "in": r.bytes_in, "out": r.bytes_out} for r in user_rollups],
        "dev_rollups": [{"device_id": r.device_id, "date": str(r.record_date), "in": r.bytes_in, "out": r.bytes_out} for r in dev_rollups],
        "router_rollups": [{"router_id": r.router_id, "date": str(r.record_date), "in": r.bytes_in, "out": r.bytes_out} for r in r_rollups],
        "users": [{"id": u.id, "name": u.name, "speed_limit": u.speed_limit} for u in users],
        "devices": [{"id": d.id, "user_id": d.user_id, "ip": d.ip_address, "mac": d.mac_address, "limit": d.speed_limit} for d in devices],
        "settings": {s.key: s.value for s in settings}
    }


@router.post("/billing-cycle", response_model=APIResponse[BillingCycleConfig])
async def set_billing_cycle_config(
    payload: BillingCycleConfig,
    db: AsyncSession = Depends(get_db)
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
    non_midnight = anchor_hour != 0 or anchor_minute != 0

    start_dt, end_dt = get_billing_cycle_bounds(
        anchor_day, anchor_hour, anchor_minute, now, previous=False,
    )
    cycle_start = start_dt.date()
    cycle_end = inclusive_end_date(end_dt)

    # Both boundary slices below fire on exactly the same condition (a
    # non-midnight reset) and need the same two lookups. Resolve them once:
    # the offset is threaded into slice_of_day_bytes so it is not re-read per
    # call, and prev_end_dt is this cycle's start instant so one interface list
    # serves both.
    slice_interfaces: list = []
    slice_offset = 0
    if non_midnight:
        slice_interfaces = await resolve_monitored_interfaces(db, router_id)
        slice_offset = await get_router_offset(db) or 0

    data = await AnalyticsEngine.get_historical_traffic(
        session=db,
        start_date=cycle_start,
        end_date=min(cycle_end, today),
        router_id=router_id,
        range_preset="billing_current",
        anchor_day=anchor_day,
        include_breakdown_extras=False,
    )
    used = data.gateway.total_bytes

    # Traffic on the cycle-start date that happened *before* the reset instant
    # belongs to the previous cycle. Only the start day needs adjusting: we are
    # always mid-cycle here, so "everything up to now" on any later day is
    # already inside this cycle. A midnight anchor never enters this branch, so
    # its "used" figure stays byte-for-byte the pre-change one.
    if non_midnight:
        pre = await slice_of_day_bytes(
            db, router_id, cycle_start, None, start_dt.time(), slice_interfaces,
            offset_minutes=slice_offset,
        )
        if pre is not None:
            used = max(0, used - (pre[0] + pre[1]))

    limit = config.limit_bytes

    # Day counts are fractional so the projection eases across the reset instant
    # rather than stepping a whole day. At a midnight anchor they collapse back
    # to the original inclusive calendar-day counts: for anchor days 2-28 every
    # derived figure is identical to the pre-change output. Anchor day 1 and a
    # clamped short-month anchor (e.g. 31 in February) now differ on purpose -
    # those are the two acknowledged latent-bug fixes the datetime bounds bring.
    DAY = 86400.0
    total_days = max(1e-9, (end_dt - start_dt).total_seconds() / DAY)
    if anchor_hour == 0 and anchor_minute == 0:
        elapsed_days = float(min(total_days, max(1, (today - cycle_start).days + 1)))
    else:
        # Mirror the legacy "the current day counts as one whole elapsed day"
        # behaviour: floor at a full day (but never above total_days for a
        # hypothetical sub-day cycle) so avg_per_day stays finite right after a
        # reset instead of exploding on the first few minutes of traffic.
        elapsed_days = min(
            total_days,
            max(min(1.0, total_days), (now - start_dt).total_seconds() / DAY),
        )
    days_left_after_today = max(0.0, total_days - elapsed_days)

    remaining_seconds = max(0.0, (end_dt - now).total_seconds())
    days_remaining = int(remaining_seconds // DAY) + (1 if remaining_seconds % DAY else 0)

    # --- end-of-cycle forecast ---------------------------------------------
    cycle_days_total = max(1, round(total_days))
    cycle_days_elapsed = min(cycle_days_total, max(1, round(elapsed_days)))

    # Conservative: the average day so far, projected across the whole cycle.
    avg_per_day = used / elapsed_days
    projected_bytes_linear = int(avg_per_day * total_days)

    # Previous full billing cycle's daily average - the anchor that keeps the
    # "at current pace" figure from swinging wildly in the first days of a cycle,
    # when it would otherwise rest on one or two samples.
    prev_start_dt, prev_end_dt = get_billing_cycle_bounds(
        anchor_day, anchor_hour, anchor_minute, now, previous=True,
    )
    prev_start = prev_start_dt.date()
    prev_end = inclusive_end_date(prev_end_dt)
    prev_data = await AnalyticsEngine.get_historical_traffic(
        session=db,
        start_date=prev_start,
        end_date=prev_end,
        router_id=router_id,
        range_preset="billing_previous",
        anchor_day=anchor_day,
        include_breakdown_extras=False,
    )
    prev_cycle_bytes = prev_data.gateway.total_bytes

    # The previous cycle ends at this cycle's start instant. When that is not at
    # midnight, prev_end (its inclusive last date) is the very same calendar day
    # as cycle_start, and that day's whole-day rollup is already counted in this
    # cycle's "used". Subtract the post-reset slice of that day so it is not
    # counted in both. If the samples are pruned (None) the whole day stands -
    # the same documented fallback as the current-cycle start-day slice.
    if non_midnight:
        s = await slice_of_day_bytes(
            db, router_id, prev_end_dt.date(),
            from_time=prev_end_dt.time(), to_time=None, interfaces=slice_interfaces,
            offset_minutes=slice_offset,
        )
        if s is not None:
            prev_cycle_bytes = max(0, prev_cycle_bytes - (s[0] + s[1]))

    prev_cycle_days = max(1, (prev_end - prev_start).days + 1)
    prev_per_day = (prev_cycle_bytes / prev_cycle_days) if prev_cycle_bytes > 0 else None

    # "At current pace": the mean of up to the last 7 recorded days in this
    # cycle. Early on that mean is noisy, so it is blended with last cycle's
    # daily average on a weight that ramps from 0 to 1 over the first 7 days -
    # day 1 leans entirely on last cycle, day 7+ entirely on the recent mean.
    recent = [p.total_bytes for p in sorted(data.timeline, key=lambda p: p.record_date)][-7:]
    recent_mean = (sum(recent) / len(recent)) if recent else avg_per_day

    if prev_per_day is not None:
        pace_blend_weight = min(1.0, cycle_days_elapsed / 7.0)
        pace_per_day = pace_blend_weight * recent_mean + (1.0 - pace_blend_weight) * prev_per_day
        pace_basis = "blended"
    elif len(recent) >= 3:
        pace_blend_weight = 1.0
        pace_per_day = recent_mean
        pace_basis = "recent"
    else:
        # No previous cycle and barely any data this one: fall back to the same
        # flat average the conservative projection uses, and say so.
        pace_blend_weight = 1.0
        pace_per_day = avg_per_day
        pace_basis = "sparse"

    projected_bytes_at_pace = int(used + pace_per_day * days_left_after_today)

    return QuotaStatusDTO(
        limit_bytes=limit,
        used_bytes=used,
        remaining_bytes=max(0, limit - used) if limit else 0,
        used_pct=round((used / limit) * 100, 2) if limit else 0.0,
        cycle_start=cycle_start,
        cycle_end=cycle_end,
        # Only carried for a non-midnight reset; a midnight anchor keeps the
        # plain "N days left" label the UI has always shown for it.
        cycle_end_at=end_dt if (anchor_hour or anchor_minute) else None,
        days_remaining=days_remaining,
        projected_daily_budget=(max(0, limit - used) // days_remaining) if (limit and days_remaining) else 0,
        cycle_days_total=cycle_days_total,
        cycle_days_elapsed=cycle_days_elapsed,
        projected_bytes_linear=projected_bytes_linear,
        projected_pct_linear=round(projected_bytes_linear / limit * 100, 1) if limit else 0.0,
        pace_bytes_per_day=int(pace_per_day),
        projected_bytes_at_pace=projected_bytes_at_pace,
        projected_pct_at_pace=round(projected_bytes_at_pace / limit * 100, 1) if limit else 0.0,
        prev_cycle_bytes=prev_cycle_bytes,
        prev_cycle_bytes_per_day=int(prev_per_day) if prev_per_day is not None else 0,
        pace_blend_weight=round(pace_blend_weight, 2),
        pace_basis=pace_basis,
        on_track=bool(limit) and projected_bytes_linear <= limit,
        thresholds=config.thresholds,
        thresholds_reached=await unfired_for_cycle(db, cycle_start),
        enabled=limit > 0,
        notify_telegram=config.notify_telegram,
        portal_url=config.portal_url,
        portal_label=config.portal_label,
    )


@router.get("/quota", response_model=APIResponse[QuotaStatusDTO])
async def get_quota_status(
    router_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """Quota consumption for the current billing cycle."""
    return APIResponse(data=await build_quota_status(db, router_id))


@router.post("/quota", response_model=APIResponse[QuotaStatusDTO])
async def set_quota_config(
    payload: QuotaConfigDTO,
    router_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """Set the ISP allowance, the warning percentages, and the portal link."""
    try:
        await save_quota_config(db, QuotaConfig(
            limit_bytes=payload.limit_bytes,
            thresholds=payload.thresholds,
            notify_telegram=payload.notify_telegram,
            portal_url=payload.portal_url,
            portal_label=payload.portal_label,
        ))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return APIResponse(data=await build_quota_status(db, router_id))
