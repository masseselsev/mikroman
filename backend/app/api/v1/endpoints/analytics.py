from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.session import get_db
from backend.app.schemas.analytics import BillingCycleConfig, TrafficAnalyticsResponse
from backend.app.schemas.common import APIResponse
from backend.app.services.analytics_engine import AnalyticsEngine, resolve_date_range
from backend.app.services.router_manager import router_manager

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
        anchor_day=anchor_day
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
