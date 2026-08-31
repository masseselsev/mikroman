from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import Device, DeviceTrafficRollup, User
from backend.app.db.session import get_db
from backend.app.schemas.common import APIResponse
from backend.app.schemas.user import UserCreate, UserDTO, UserReorderRequest, UserUpdate
from backend.app.services.device_manager import detach_device_traffic_from_user
from backend.app.services.router_manager import router_manager
from backend.app.services.traffic_controller import TrafficController

router = APIRouter(prefix="/users", tags=["Users"])


async def get_traffic_controller(db: AsyncSession = Depends(get_db)) -> TrafficController:
    return TrafficController(await router_manager.require_client(session=db))


@router.get("", response_model=APIResponse[List[UserDTO]])
async def list_users(
    db: AsyncSession = Depends(get_db),
    traffic_ctrl: TrafficController = Depends(get_traffic_controller)
):
    """List all users with live metrics and device assignments."""
    # Manual order first, then id, so the sequence is stable for equal values.
    result = await db.execute(select(User).order_by(User.sort_order, User.id))
    users = result.scalars().all()

    # Enrich with live queue metrics
    metrics = await traffic_ctrl.get_realtime_traffic_stats(db)
    metrics_map = {m["user_id"]: m for m in metrics}

    # All-time bytes per device, from the daily rollups. Today's running total is
    # already one of those rows, so summing every date needs no separate add-on.
    totals_rows = (await db.execute(
        select(
            DeviceTrafficRollup.device_id,
            func.sum(DeviceTrafficRollup.bytes_in),
            func.sum(DeviceTrafficRollup.bytes_out),
        ).group_by(DeviceTrafficRollup.device_id)
    )).all()
    totals_map = {r[0]: (int(r[1] or 0), int(r[2] or 0)) for r in totals_rows}

    user_dtos = []
    for u in users:
        dto = UserDTO.model_validate(u)
        m = metrics_map.get(u.id)
        if m:
            dto.current_rate_in = m["current_rate_in"]
            dto.current_rate_out = m["current_rate_out"]
            dto.bytes_today_in = m["bytes_in"]
            dto.bytes_today_out = m["bytes_out"]

        # Per-device live rate and daily volume, so the dashboard can point at
        # the specific device consuming bandwidth rather than its owner. The
        # all-time total is attached even when the user has no live metrics.
        per_device = (m or {}).get("devices", {})
        for device_dto in dto.devices:
            dm = per_device.get(device_dto.id)
            if dm:
                device_dto.current_rate_in = dm["current_rate_in"]
                device_dto.current_rate_out = dm["current_rate_out"]
                device_dto.bytes_today_in = dm["bytes_today_in"]
                device_dto.bytes_today_out = dm["bytes_today_out"]
            t_in, t_out = totals_map.get(device_dto.id, (0, 0))
            device_dto.bytes_total_in = t_in
            device_dto.bytes_total_out = t_out
        user_dtos.append(dto)

    return APIResponse(data=user_dtos)


@router.post("", response_model=APIResponse[UserDTO], status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
    traffic_ctrl: TrafficController = Depends(get_traffic_controller)
):
    """Create a new user profile and optionally assign devices by MAC."""
    existing = await db.execute(select(User).where(User.name == payload.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="User with this name already exists")

    user = User(
        name=payload.name,
        avatar_icon=payload.avatar_icon,
        speed_limit=payload.speed_limit,
        is_paused=payload.is_paused,
        priority=payload.priority
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    if payload.device_macs:
        for mac in payload.device_macs:
            dev_res = await db.execute(select(Device).where(Device.mac_address == mac.upper()))
            dev = dev_res.scalar_one_or_none()
            if dev:
                dev.user_id = user.id
        await db.commit()
        await db.refresh(user)

    active_ips = [d.ip_address for d in user.devices if d.is_active and d.ip_address]
    await traffic_ctrl.sync_user_queue(user.id, user.name, active_ips, user.speed_limit)

    return APIResponse(data=UserDTO.model_validate(user))


@router.get("/{user_id}", response_model=APIResponse[UserDTO])
async def get_user(user_id: int, db: AsyncSession = Depends(get_db)):
    """Get single user profile by ID."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return APIResponse(data=UserDTO.model_validate(user))


@router.patch("/{user_id}", response_model=APIResponse[UserDTO])
async def update_user(
    user_id: int,
    payload: UserUpdate,
    db: AsyncSession = Depends(get_db),
    traffic_ctrl: TrafficController = Depends(get_traffic_controller)
):
    """Update user profile (name, avatar, limit, pause state)."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.name is not None and payload.name != user.name:
        existing = await db.execute(select(User).where(User.name == payload.name, User.id != user_id))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="User with this name already exists")
        user.name = payload.name
    if payload.avatar_icon is not None:
        user.avatar_icon = payload.avatar_icon
    if payload.priority is not None:
        user.priority = payload.priority
    if payload.speed_limit is not None:
        user.speed_limit = payload.speed_limit
    if payload.is_paused is not None:
        user.is_paused = payload.is_paused

    # Handle device assignments / unassignments by MAC address
    if payload.device_macs is not None:
        target_macs = set(m.upper().strip() for m in payload.device_macs if m and m.strip())
        # Unassign devices that were deselected, taking their recorded daily
        # volume back out of this profile's totals as they go.
        for dev in list(user.devices):
            if dev.mac_address.upper() not in target_macs:
                await detach_device_traffic_from_user(db, dev, user.id)
                dev.user_id = None

        # Assign newly selected devices
        for mac in target_macs:
            dev_res = await db.execute(select(Device).where(Device.mac_address == mac))
            dev = dev_res.scalar_one_or_none()
            if dev:
                dev.user_id = user.id

    await db.commit()
    await db.refresh(user)

    active_ips = [d.ip_address for d in user.devices if d.is_active and d.ip_address]
    await traffic_ctrl.sync_user_queue(user.id, user.name, active_ips, user.speed_limit)

    return APIResponse(data=UserDTO.model_validate(user))


@router.delete("/{user_id}", response_model=APIResponse[bool])
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    traffic_ctrl: TrafficController = Depends(get_traffic_controller)
):
    """Delete a user profile and unassign all associated devices."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Clean up queues
    await traffic_ctrl.sync_user_queue(user.id, user.name, [], "unlimited")

    await db.delete(user)
    await db.commit()
    return APIResponse(data=True, message="User deleted successfully")


@router.post("/reorder", response_model=APIResponse[bool])
async def reorder_users(payload: UserReorderRequest, db: AsyncSession = Depends(get_db)):
    """Set the dashboard order of the user cards.

    Positions are assigned from the given sequence; ids that are not listed keep
    their existing order after the listed ones, so a partial list cannot drop a
    profile off the dashboard.
    """
    result = await db.execute(select(User))
    users = {u.id: u for u in result.scalars().all()}

    position = 0
    for user_id in payload.user_ids:
        user = users.pop(user_id, None)
        if user is not None:
            user.sort_order = position
            position += 1

    for leftover in sorted(users.values(), key=lambda u: (u.sort_order, u.id)):
        leftover.sort_order = position
        position += 1

    await db.commit()
    return APIResponse(data=True)
