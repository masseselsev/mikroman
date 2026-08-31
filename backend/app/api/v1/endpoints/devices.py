import logging
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.db.models import AlertLog, Device, DeviceCoexistence, DeviceHistory, User
from backend.app.db.session import get_db
from backend.app.schemas.common import APIResponse
from backend.app.schemas.device import (
    DeviceDTO,
    DeviceHistoryDTO,
    DeviceLinkRequest,
    DeviceMergeRequest,
    DevicePauseUpdate,
    DeviceSpeedLimitUpdate,
    DeviceSplitRequest,
    DeviceSuggestionDTO,
    DeviceUpdate,
)
from backend.app.services.device_linking import (
    LinkSuggestion,
    find_link_suggestions,
    link_device,
    unlink_device,
)
from backend.app.services.device_manager import DeviceManager, detach_device_traffic_from_user
from backend.app.services.mac_rotation import canonical_pair, normalise_hostname
from backend.app.services.router_manager import router_manager
from backend.app.services.traffic_controller import TrafficController, resolve_unassigned_limit
from backend.app.services.vendor_lookup import vendor_service

logger = logging.getLogger("mikroman.devices")

router = APIRouter(prefix="/devices", tags=["Devices"])


async def get_traffic_controller(db: AsyncSession = Depends(get_db)) -> TrafficController:
    return TrafficController(await router_manager.require_client(session=db))


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
    dev_mgr = DeviceManager(await router_manager.require_client(session=db))
    suggestions = await dev_mgr.find_merge_suggestions(db)
    return APIResponse(data=suggestions)


@router.get("/link-suggestions", response_model=APIResponse[List[LinkSuggestion]])
async def get_link_suggestions(db: AsyncSession = Depends(get_db)):
    """Devices that look like separate adapters of the same physical machine."""
    return APIResponse(data=await find_link_suggestions(db))


@router.post("/{device_id}/link", response_model=APIResponse[DeviceDTO])
async def link_device_endpoint(
    device_id: int,
    payload: DeviceLinkRequest,
    db: AsyncSession = Depends(get_db)
):
    """Attach a device to another as an additional network adapter.

    Unlike merging - which exists for MAC rotation and collapses two records
    because only one address is real - both addresses remain valid here and both
    records are kept, simply presented as one machine.
    """
    try:
        device = await link_device(db, device_id=device_id, primary_device_id=payload.primary_device_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return APIResponse(data=DeviceDTO.model_validate(device))


@router.post("/{device_id}/unlink", response_model=APIResponse[DeviceDTO])
async def unlink_device_endpoint(device_id: int, db: AsyncSession = Depends(get_db)):
    """Detach an adapter so it stands as its own device again."""
    try:
        device = await unlink_device(db, device_id=device_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return APIResponse(data=DeviceDTO.model_validate(device))


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
    dev_mgr = DeviceManager(await router_manager.require_client(session=db))
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
    dev_mgr = DeviceManager(await router_manager.require_client(session=db))
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
    if "ip_address" in payload.model_fields_set:
        # Explicit null clears a stale lease; the accounting rule and any queue
        # for the old address are pruned on the next sync tick.
        device.ip_address = payload.ip_address
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

    # Taking a device out of quarantine has to release the quarantine limit with
    # it. Without this the device kept its 5M/5M child queue under its new
    # owner's parent and stayed throttled, whatever the owner's own limit said.
    # Only an untouched quarantine value is cleared: an explicit limit set for
    # this device is the operator's decision and survives the move.
    became_assigned = old_user_id is None and device.user_id is not None
    if became_assigned and payload.speed_limit is None:
        quarantine = await resolve_unassigned_limit(db)
        if device.speed_limit in (quarantine, None):
            device.speed_limit = "default"

    # Leaving a profile: unless told otherwise, take this device's recorded
    # daily volume back out of that profile's totals, so the breakdown stays
    # honest after the move.
    became_unassigned = (
        old_user_id is not None
        and "user_id" in payload.model_fields_set
        and payload.user_id is None
    )
    if became_unassigned and (payload.detach_traffic is None or payload.detach_traffic):
        moved = await detach_device_traffic_from_user(db, device, old_user_id)
        if moved:
            logger.info(
                f"Detached device {device.id} traffic from user {old_user_id} "
                f"across {moved} day(s)"
            )

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


@router.delete("/{device_id}", response_model=APIResponse[bool])
async def delete_device(
    device_id: int,
    db: AsyncSession = Depends(get_db),
    traffic_ctrl: TrafficController = Depends(get_traffic_controller),
):
    """Delete a device record for good.

    The profile's traffic totals are **not** touched: a deleted device's bytes
    stay counted for whoever owned it (the per-user ``TrafficRollup`` is a
    separate table). Only the device row and its own history / daily rollups
    go. Any adapter that pointed at this device as its primary is detached, and
    the router-side accounting rule and managed queue are cleared on the next
    background sync.
    """
    device = await db.get(
        Device, device_id,
        options=[selectinload(Device.history), selectinload(Device.traffic_rollups)],
    )
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    owner_id = device.user_id
    name = device.custom_name or device.hostname or device.mac_address
    days = len(device.traffic_rollups)

    # Adapters linked to this device lose their primary rather than dangling.
    await db.execute(
        update(Device)
        .where(Device.linked_to_device_id == device_id)
        .values(linked_to_device_id=None)
    )

    db.add(AlertLog(
        router_id=device.router_id,
        alert_type="device_deleted",
        message=(
            f"Device '{name}' ({device.mac_address}) deleted"
            + (f"; its {days} day(s) of traffic stay counted for the profile" if owner_id and days else "")
        ),
    ))

    await db.delete(device)  # cascade removes DeviceHistory + DeviceTrafficRollup
    await db.commit()

    if owner_id:
        user = await db.get(User, owner_id)
        if user:
            active_ips = [d.ip_address for d in user.devices if d.is_active and d.ip_address]
            await traffic_ctrl.sync_user_queue(user.id, user.name, active_ips, user.speed_limit)

    return APIResponse(data=True, message=f"Device '{name}' deleted")


@router.post("/{device_id}/split", response_model=APIResponse[DeviceDTO])
async def split_device(
    device_id: int,
    payload: DeviceSplitRequest,
    db: AsyncSession = Depends(get_db),
):
    """Break a wrongly-merged MAC back out into its own device record.

    Use this when discovery folded two genuinely separate devices together (two
    identical phones, say). A new **unassigned** device is created for the given
    address, and the pair is recorded in ``device_coexistence`` so the
    consolidation pass never merges them again.

    What this cannot do: divide the *past*. Once daily rollups were coalesced by
    a merge, the individual device's share is gone. Traffic already recorded
    stays with the original device; only traffic seen on the split-off address
    from now on is tracked separately.
    """
    device = await db.get(Device, device_id, options=[selectinload(Device.history)])
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    wanted = payload.mac_address.strip().upper()
    if not wanted:
        raise HTTPException(status_code=400, detail="mac_address is required")
    if wanted == (device.mac_address or "").upper():
        raise HTTPException(status_code=400, detail="That is the device's current address")

    hist_row = next(
        (h for h in device.history if (h.mac_address or "").upper() == wanted), None
    )
    if hist_row is None:
        raise HTTPException(
            status_code=400,
            detail="That address is not in this device's history",
        )

    clash = (await db.execute(
        select(Device).where(func.upper(Device.mac_address) == wanted)
    )).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(status_code=400, detail="A device with that address already exists")

    original_case = hist_row.mac_address
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    new_device = Device(
        mac_address=original_case,
        router_id=device.router_id,
        ip_address=None,
        hostname=device.hostname,
        custom_name=device.custom_name or device.hostname or original_case,
        vendor=device.vendor,
        user_id=None,
        speed_limit="default",
        is_active=False,
        last_seen=hist_row.created_at or now_utc,
    )
    db.add(new_device)
    await db.flush()

    lo, hi = canonical_pair(original_case, device.mac_address)
    db.add(DeviceCoexistence(
        mac_a=lo, mac_b=hi,
        hostname=normalise_hostname(device.custom_name) or normalise_hostname(device.hostname),
        first_seen_together=now_utc,
        last_seen_together=now_utc,
        observations=1,
    ))

    split_note = (
        f"Split {original_case} out of device #{device.id} ({device.mac_address}). "
        f"Traffic recorded before now stays with #{device.id} - coalesced daily "
        f"totals cannot be divided retroactively."
    )
    db.add(DeviceHistory(
        device_id=new_device.id, mac_address=original_case,
        hostname=new_device.hostname, event_type="split", details=split_note,
    ))
    db.add(DeviceHistory(
        device_id=device.id, mac_address=original_case,
        hostname=device.hostname, event_type="split",
        details=f"Split {original_case} out into a separate device (#{new_device.id}).",
    ))
    db.add(AlertLog(
        router_id=device.router_id,
        alert_type="device_split",
        message=(
            f"Split {original_case} out of '{device.custom_name or device.hostname or device.mac_address}' "
            f"into a new unassigned device. They will not be auto-merged again."
        ),
    ))

    await db.commit()
    await db.refresh(new_device)
    return APIResponse(data=DeviceDTO.model_validate(new_device), message="Device split")
