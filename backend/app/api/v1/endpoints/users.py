from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import Device, User
from backend.app.db.session import get_db
from backend.app.schemas.common import APIResponse
from backend.app.schemas.user import UserCreate, UserDTO, UserUpdate
from backend.app.services.routeros import RouterOSClient
from backend.app.services.traffic_controller import TrafficController

router = APIRouter(prefix="/users", tags=["Users"])


def get_traffic_controller() -> TrafficController:
    return TrafficController(RouterOSClient())


@router.get("", response_model=APIResponse[List[UserDTO]])
async def list_users(
    db: AsyncSession = Depends(get_db),
    traffic_ctrl: TrafficController = Depends(get_traffic_controller)
):
    """List all users with live metrics and device assignments."""
    result = await db.execute(select(User))
    users = result.scalars().all()

    # Enrich with live queue metrics
    metrics = await traffic_ctrl.get_realtime_traffic_stats(db)
    metrics_map = {m["user_id"]: m for m in metrics}

    user_dtos = []
    for u in users:
        dto = UserDTO.model_validate(u)
        m = metrics_map.get(u.id)
        if m:
            dto.current_rate_in = m["current_rate_in"]
            dto.current_rate_out = m["current_rate_out"]
            dto.bytes_today_in = m["bytes_in"]
            dto.bytes_today_out = m["bytes_out"]
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

    if payload.name is not None:
        user.name = payload.name
    if payload.avatar_icon is not None:
        user.avatar_icon = payload.avatar_icon
    if payload.priority is not None:
        user.priority = payload.priority
    if payload.speed_limit is not None:
        user.speed_limit = payload.speed_limit
    if payload.is_paused is not None:
        user.is_paused = payload.is_paused

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
