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
from backend.app.services.analytics_engine import AnalyticsEngine, get_billing_cycle_dates, resolve_date_range
from backend.app.services.quota import (
    QuotaConfig,
    get_quota_config,
    save_quota_config,
    unfired_for_cycle,
)
from backend.app.services.router_manager import router_manager
from backend.app.services.router_time import router_local_date

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
    # Opportunistically capture live traffic snapshot from active router
    try:
        r_client = await router_manager.get_client(router_id=router_id, session=db)
        if r_client:
            await AnalyticsEngine.record_traffic_snapshot(db, router_id=router_id or 1, client=r_client)
    except Exception:
        pass

    anchor_day = await AnalyticsEngine.get_billing_anchor_day(db)
    resolved_start, resolved_end, range_label = resolve_date_range(
        preset=preset,
        start_date=start_date,
        end_date=end_date,
        anchor_day=anchor_day,
        # Presets follow the router's calendar, not the container's UTC one.
        today=await router_local_date(db)
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
    """Fetch the configured ISP billing cycle renewal day (1-31)."""
    anchor_day = await AnalyticsEngine.get_billing_anchor_day(db)
    return APIResponse(data=BillingCycleConfig(anchor_day=anchor_day))


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
    """Save the ISP billing cycle renewal day (1-31)."""
    saved_day = await AnalyticsEngine.set_billing_anchor_day(db, payload.anchor_day)
    return APIResponse(data=BillingCycleConfig(anchor_day=saved_day), message=f"Billing cycle anchor set to day {saved_day}")


async def build_quota_status(db: AsyncSession, router_id: Optional[int] = None) -> QuotaStatusDTO:
    """Consumption against the ISP allowance for the current billing cycle.

    Usage is taken from the gateway figure for the cycle window, which is the
    number an ISP actually bills on, rather than the per-device sum.
    """
    config = await get_quota_config(db)
    anchor_day = await AnalyticsEngine.get_billing_anchor_day(db)
    today = await router_local_date(db)
    cycle_start, cycle_end = get_billing_cycle_dates(anchor_day, today, previous=False)

    data = await AnalyticsEngine.get_historical_traffic(
        session=db,
        start_date=cycle_start,
        end_date=min(cycle_end, today),
        router_id=router_id,
        range_preset="billing_current",
        anchor_day=anchor_day,
    )
    used = data.gateway.total_bytes
    limit = config.limit_bytes
    # Inclusive of today: consuming the remainder over the rest of the cycle
    # includes what is left of the current day.
    days_remaining = max(0, (cycle_end - today).days + 1)

    # --- end-of-cycle forecast ---------------------------------------------
    cycle_days_total = max(1, (cycle_end - cycle_start).days + 1)
    cycle_days_elapsed = min(cycle_days_total, max(1, (today - cycle_start).days + 1))
    days_left_after_today = max(0, cycle_days_total - cycle_days_elapsed)

    # Conservative: the average day so far, projected across the whole cycle.
    avg_per_day = used / cycle_days_elapsed
    projected_bytes_linear = int(avg_per_day * cycle_days_total)

    # "At current pace": mean of up to the last 7 daily totals actually on
    # record for this cycle, added to what is already used.
    recent = [p.total_bytes for p in sorted(data.timeline, key=lambda p: p.record_date)][-7:]
    pace_per_day = (sum(recent) / len(recent)) if recent else avg_per_day
    projected_bytes_at_pace = int(used + pace_per_day * days_left_after_today)

    return QuotaStatusDTO(
        limit_bytes=limit,
        used_bytes=used,
        remaining_bytes=max(0, limit - used) if limit else 0,
        used_pct=round((used / limit) * 100, 2) if limit else 0.0,
        cycle_start=cycle_start,
        cycle_end=cycle_end,
        days_remaining=days_remaining,
        projected_daily_budget=(max(0, limit - used) // days_remaining) if (limit and days_remaining) else 0,
        cycle_days_total=cycle_days_total,
        cycle_days_elapsed=cycle_days_elapsed,
        projected_bytes_linear=projected_bytes_linear,
        projected_pct_linear=round(projected_bytes_linear / limit * 100, 1) if limit else 0.0,
        pace_bytes_per_day=int(pace_per_day),
        projected_bytes_at_pace=projected_bytes_at_pace,
        projected_pct_at_pace=round(projected_bytes_at_pace / limit * 100, 1) if limit else 0.0,
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
