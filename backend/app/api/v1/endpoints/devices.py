from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import Device, User
from backend.app.db.session import get_db
from backend.app.schemas.common import APIResponse
from backend.app.schemas.device import DeviceDTO, DeviceUpdate
from backend.app.services.device_manager import DeviceManager
from backend.app.services.routeros import RouterOSClient
from backend.app.services.traffic_controller import TrafficController

router = APIRouter(prefix="/devices", tags=["Devices"])


def get_device_manager() -> DeviceManager:
    return DeviceManager(RouterOSClient())


def get_traffic_controller() -> TrafficController:
    return TrafficController(RouterOSClient())


@router.get("", response_model=APIResponse[List[DeviceDTO]])
async def list_devices(
    unassigned_only: bool = False,
    active_only: bool = False,
    db: AsyncSession = Depends(get_db)
):
    """List network devices with optional unassigned/active filtering."""
    query = select(Device)
    if unassigned_only:
        query = query.where(Device.user_id == None)  # noqa: E711
    if active_only:
        query = query.where(Device.is_active == True)  # noqa: E712

    result = await db.execute(query)
    devices = result.scalars().all()
    return APIResponse(data=[DeviceDTO.model_validate(d) for d in devices])


@router.post("/scan", response_model=APIResponse[List[DeviceDTO]])
async def scan_network(
    db: AsyncSession = Depends(get_db),
    dev_mgr: DeviceManager = Depends(get_device_manager)
):
    """Trigger immediate network discovery scan from RouterOS."""
    all_devs, newly_discovered = await dev_mgr.sync_devices_from_router(db)
    return APIResponse(
        data=[DeviceDTO.model_validate(d) for d in all_devs],
        message=f"Scan complete. {len(newly_discovered)} new devices discovered."
    )


@router.patch("/{device_id}", response_model=APIResponse[DeviceDTO])
async def update_device(
    device_id: int,
    payload: DeviceUpdate,
    db: AsyncSession = Depends(get_db),
    traffic_ctrl: TrafficController = Depends(get_traffic_controller)
):
    """Assign/unassign device to user or update custom name."""
    device = await db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    old_user_id = device.user_id

    if payload.custom_name is not None:
        device.custom_name = payload.custom_name
    if payload.is_active is not None:
        device.is_active = payload.is_active
    if payload.user_id is not None or "user_id" in payload.model_fields_set:
        device.user_id = payload.user_id

    await db.commit()
    await db.refresh(device)

    # Resync affected user queues
    affected_user_ids = {u for u in [old_user_id, device.user_id] if u is not None}
    for uid in affected_user_ids:
        user = await db.get(User, uid)
        if user:
            active_ips = [d.ip_address for d in user.devices if d.is_active and d.ip_address]
            await traffic_ctrl.sync_user_queue(user.id, user.name, active_ips, user.speed_limit)

    return APIResponse(data=DeviceDTO.model_validate(device))
