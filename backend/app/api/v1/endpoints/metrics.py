import json
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import AppSetting
from backend.app.db.session import get_db
from backend.app.schemas.common import APIResponse
from backend.app.schemas.metrics import (
    InterfaceHistoryResponse,
    MonitoredInterfacesConfig,
    SystemMetricsResponse,
)
from backend.app.schemas.routeros import InterfaceDTO
from backend.app.services.metrics_collector import metrics_collector
from backend.app.services.router_manager import router_manager

router = APIRouter(prefix="/metrics", tags=["Metrics & Graphing"])
logger = logging.getLogger("mikroman.metrics")


@router.get("/system", response_model=APIResponse[SystemMetricsResponse])
async def get_system_metrics(
    range: str = Query("1h", pattern="^(1h|6h|24h|7d|30d)$"),
    router_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    """Fetch time-series metrics for CPU load, RAM usage %, and Temperature/Voltage."""
    if not router_id:
        active_r = await router_manager.get_default_or_first_router(db)
        if active_r:
            router_id = active_r.id

    data = await metrics_collector.get_system_history(db, router_id=router_id, range_key=range)
    return APIResponse(data=data)


@router.get("/interfaces", response_model=APIResponse[InterfaceHistoryResponse])
async def get_interface_metrics(
    range: str = Query("1h", pattern="^(1h|6h|24h|7d|30d)$"),
    interfaces: Optional[str] = Query(None, description="Comma-separated interface names e.g. ether1,sfp1"),
    router_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    """Fetch time-series bandwidth history summed across selected interfaces."""
    if not router_id:
        active_r = await router_manager.get_default_or_first_router(db)
        if active_r:
            router_id = active_r.id

    # Check if interfaces parameter was explicitly provided
    if interfaces is not None:
        trimmed = interfaces.strip()
        if not trimmed:
            # User explicitly deselected all interfaces -> return empty response
            return APIResponse(data=InterfaceHistoryResponse(
                range=range,
                interfaces=[],
                is_summed=True,
                points=[],
                current_rx_bps=0.0,
                current_tx_bps=0.0
            ))
        selected_list = [i.strip() for i in trimmed.split(",") if i.strip()]
    else:
        # Parameter omitted entirely -> fallback to saved default
        selected_list = None
        if router_id:
            setting_key = f"monitored_interfaces_{router_id}"
            setting = await db.get(AppSetting, setting_key)
            if setting and setting.value:
                try:
                    saved = json.loads(setting.value)
                    if isinstance(saved, list) and len(saved) > 0:
                        selected_list = saved
                except Exception:
                    pass

    data = await metrics_collector.get_interface_history(
        db,
        router_id=router_id,
        range_key=range,
        selected_interfaces=selected_list
    )
    return APIResponse(data=data)


@router.get("/interfaces/list", response_model=APIResponse[List[InterfaceDTO]])
async def list_available_interfaces(
    router_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    """Fetch live list of all physical and virtual interfaces available on the active router."""
    client = await router_manager.get_client(router_id, session=db)
    if not client:
        return APIResponse(data=[], message="Router not connected")
    ifaces = await client.get_interfaces()
    # Flag the interfaces that carry a default route so the "WAN only" preset
    # in the UI selects the real uplink(s) instead of matching on the name.
    try:
        wan = set(await client.get_wan_interfaces())
        for iface in ifaces:
            if iface.name in wan:
                iface.is_wan = True
    except Exception:
        logger.debug("Could not resolve WAN interfaces from /ip/route", exc_info=True)
    return APIResponse(data=ifaces)


@router.get("/config", response_model=APIResponse[MonitoredInterfacesConfig])
async def get_monitored_interfaces_config(
    router_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    """Get the saved default monitored interfaces for a router."""
    if not router_id:
        active_r = await router_manager.get_default_or_first_router(db)
        if active_r:
            router_id = active_r.id

    setting_key = f"monitored_interfaces_{router_id}" if router_id else "monitored_interfaces_default"
    setting = await db.get(AppSetting, setting_key)
    selected = []
    if setting and setting.value:
        try:
            selected = json.loads(setting.value)
        except Exception:
            selected = []

    return APIResponse(data=MonitoredInterfacesConfig(router_id=router_id, selected_interfaces=selected))


@router.post("/config", response_model=APIResponse[MonitoredInterfacesConfig])
async def save_monitored_interfaces_config(
    payload: MonitoredInterfacesConfig,
    db: AsyncSession = Depends(get_db)
):
    """Save the default monitored interfaces for a router."""
    router_id = payload.router_id
    if not router_id:
        active_r = await router_manager.get_default_or_first_router(db)
        if active_r:
            router_id = active_r.id

    setting_key = f"monitored_interfaces_{router_id}" if router_id else "monitored_interfaces_default"
    val_json = json.dumps(payload.selected_interfaces)

    setting = await db.get(AppSetting, setting_key)
    if setting:
        setting.value = val_json
    else:
        setting = AppSetting(key=setting_key, value=val_json, description="Monitored interfaces for traffic graph")
        db.add(setting)

    await db.commit()
    return APIResponse(data=payload, message="Monitored interfaces saved")
