from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.db.models import Device, User
from backend.app.db.session import get_db
from backend.app.schemas.common import APIResponse
from backend.app.schemas.device import (
    DeviceDTO,
    DeviceHistoryDTO,
    DeviceMergeRequest,
    DevicePauseUpdate,
    DeviceSpeedLimitUpdate,
    DeviceSuggestionDTO,
    DeviceUpdate,
)
from backend.app.services.device_manager import DeviceManager
from backend.app.services.router_manager import router_manager
from backend.app.services.routeros import RouterOSClient
from backend.app.services.traffic_controller import TrafficController
from backend.app.services.vendor_lookup import vendor_service

router = APIRouter(prefix="/devices", tags=["Devices"])


async def get_traffic_controller(db: AsyncSession = Depends(get_db)) -> TrafficController:
    client = await router_manager.get_client(session=db)
    return TrafficController(client or RouterOSClient())


@router.get("", response_model=APIResponse[List[DeviceDTO]])
async def list_devices(
    unassigned_only: bool = False,
    active_only: bool = False,
    show_hidden: bool = False,
    db: AsyncSession = Depends(get_db)
):
    """List network devices with optional unassigned/active/hidden filtering."""
    query = select(Device).options(selectinload(Device.history))
    if unassigned_only:
        query = query.where(Device.user_id.is_(None))
    if active_only:
        query = query.where(Device.is_active.is_(True))
    if not show_hidden:
        query = query.where(Device.is_hidden.is_(False))

    result = await db.execute(query)
    devices = list(result.scalars().all())

    # Background auto-heal unknown vendors
    has_updates = False
    for d in devices:
        if not d.vendor or d.vendor == "Unknown Vendor":
            resolved = await vendor_service.lookup_async(d.mac_address, hostname=d.hostname)
            if resolved and resolved != "Unknown Vendor":
                d.vendor = resolved
                has_updates = True
    if has_updates:
        await db.commit()

    # Attach today's accounted volume. For an unassigned device this is the
    # signal that matters most: an unknown client that moved gigabytes today is
    # very different from one that has moved nothing.
    volume = await TrafficController._todays_device_volume(db)

    dtos = []
    for d in devices:
        dto = DeviceDTO.model_validate(d)
        d_in, d_out = volume.get(d.id, (0, 0))
        dto.bytes_today_in = d_in
        dto.bytes_today_out = d_out
        dtos.append(dto)

    return APIResponse(data=dtos)


@router.get("/suggestions", response_model=APIResponse[List[DeviceSuggestionDTO]])
async def get_merge_suggestions(
    db: AsyncSession = Depends(get_db)
):
    """Get smart suggestions for unassigned rotated MAC devices matching existing user devices."""
    client = await router_manager.get_client(session=db)
    dev_mgr = DeviceManager(client or RouterOSClient())
    suggestions = await dev_mgr.find_merge_suggestions(db)
    return APIResponse(data=suggestions)


@router.get("/{device_id}/history", response_model=APIResponse[List[DeviceHistoryDTO]])
async def get_device_history(
    device_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get MAC/hostname audit change history for a device."""
    device = await db.get(Device, device_id, options=[selectinload(Device.history)])
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return APIResponse(data=[DeviceHistoryDTO.model_validate(h) for h in device.history])


@router.post("/scan", response_model=APIResponse[List[DeviceDTO]])
async def scan_network(
    db: AsyncSession = Depends(get_db)
):
    """Trigger immediate network discovery scan from RouterOS."""
    client = await router_manager.get_client(session=db)
    if not client:
        client = RouterOSClient()
    dev_mgr = DeviceManager(client)
    all_devs, newly_discovered = await dev_mgr.sync_devices_from_router(db)
    return APIResponse(
        data=[DeviceDTO.model_validate(d) for d in all_devs],
        message=f"Scan complete. {len(newly_discovered)} new devices discovered."
    )


@router.post("/{device_id}/merge", response_model=APIResponse[DeviceDTO])
async def merge_device(
    device_id: int,
    payload: DeviceMergeRequest,
    db: AsyncSession = Depends(get_db),
    traffic_ctrl: TrafficController = Depends(get_traffic_controller)
):
    """Merge an unassigned rotated MAC device into an existing user device."""
    client = await router_manager.get_client(session=db)
    dev_mgr = DeviceManager(client or RouterOSClient())
    try:
        merged_device = await dev_mgr.merge_devices(
            session=db,
            source_device_id=device_id,
            target_device_id=payload.target_device_id,
            note=payload.note
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Resync user queue if target device is assigned to a user
    if merged_device.user_id:
        user = await db.get(User, merged_device.user_id)
        if user:
            active_ips = [d.ip_address for d in user.devices if d.is_active and d.ip_address]
            await traffic_ctrl.sync_user_queue(user.id, user.name, active_ips, user.speed_limit)

    return APIResponse(data=DeviceDTO.model_validate(merged_device), message="Devices successfully merged")


@router.patch("/{device_id}", response_model=APIResponse[DeviceDTO])
async def update_device(
    device_id: int,
    payload: DeviceUpdate,
    db: AsyncSession = Depends(get_db),
    traffic_ctrl: TrafficController = Depends(get_traffic_controller)
):
    """Assign/unassign device to user or update custom name / hidden status."""
    device = await db.get(Device, device_id, options=[selectinload(Device.history)])
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    old_user_id = device.user_id

    if payload.custom_name is not None:
        device.custom_name = payload.custom_name
    if payload.is_active is not None:
        device.is_active = payload.is_active
    if payload.is_hidden is not None:
        device.is_hidden = payload.is_hidden
    if payload.speed_limit is not None:
        device.speed_limit = payload.speed_limit
    if payload.is_paused is not None:
        device.is_paused = payload.is_paused
    if payload.priority is not None:
        device.priority = payload.priority
    if payload.user_id is not None or "user_id" in payload.model_fields_set:
        device.user_id = payload.user_id

    await db.commit()
    await db.refresh(device)

    # Sync device queue and parent user queues
    await traffic_ctrl.sync_device_queue(device.id, db)
    affected_user_ids = {u for u in [old_user_id, device.user_id] if u is not None}
    for uid in affected_user_ids:
        user = await db.get(User, uid)
        if user:
            active_ips = [d.ip_address for d in user.devices if d.is_active and d.ip_address]
            await traffic_ctrl.sync_user_queue(user.id, user.name, active_ips, user.speed_limit)

    return APIResponse(data=DeviceDTO.model_validate(device))


@router.post("/{device_id}/limit", response_model=APIResponse[bool])
async def set_device_limit(
    device_id: int,
    payload: DeviceSpeedLimitUpdate,
    db: AsyncSession = Depends(get_db),
    traffic_ctrl: TrafficController = Depends(get_traffic_controller)
):
    """Set an individual speed limit for a device."""
    success = await traffic_ctrl.set_device_speed_limit(device_id, payload.speed_limit, db)
    if not success:
        raise HTTPException(status_code=404, detail="Device not found")
    return APIResponse(data=True, message=f"Device speed limit set to {payload.speed_limit}")


@router.post("/{device_id}/pause", response_model=APIResponse[bool])
async def toggle_device_pause(
    device_id: int,
    payload: DevicePauseUpdate,
    db: AsyncSession = Depends(get_db),
    traffic_ctrl: TrafficController = Depends(get_traffic_controller)
):
    """Pause or resume internet access for an individual device."""
    if payload.is_paused:
        success = await traffic_ctrl.pause_device_internet(device_id, db)
        msg = "Internet paused for device"
    else:
        success = await traffic_ctrl.resume_device_internet(device_id, db)
        msg = "Internet resumed for device"

    if not success:
        raise HTTPException(status_code=404, detail="Device not found")
    return APIResponse(data=True, message=msg)
