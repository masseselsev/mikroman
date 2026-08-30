from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.session import get_db
from backend.app.schemas.common import APIResponse
from backend.app.schemas.traffic import PauseStateUpdate, SpeedLimitUpdate
from backend.app.services.router_manager import router_manager
from backend.app.services.routeros import RouterOSClient
from backend.app.services.traffic_controller import TrafficController

router = APIRouter(prefix="/traffic", tags=["Traffic Control"])


async def get_traffic_controller(db: AsyncSession = Depends(get_db)) -> TrafficController:
    client = await router_manager.get_client(session=db)
    return TrafficController(client or RouterOSClient())


@router.post("/users/{user_id}/limit", response_model=APIResponse[bool])
async def set_user_limit(
    user_id: int,
    payload: SpeedLimitUpdate,
    db: AsyncSession = Depends(get_db),
    traffic_ctrl: TrafficController = Depends(get_traffic_controller)
):
    """Update speed limit for user (e.g. '10M/50M' or 'unlimited')."""
    success = await traffic_ctrl.set_user_speed_limit(user_id, payload.speed_limit, db)
    if not success:
        raise HTTPException(status_code=404, detail="User not found")
    return APIResponse(data=True, message=f"Speed limit set to {payload.speed_limit}")


@router.post("/users/{user_id}/pause", response_model=APIResponse[bool])
async def toggle_pause_user(
    user_id: int,
    payload: PauseStateUpdate,
    db: AsyncSession = Depends(get_db),
    traffic_ctrl: TrafficController = Depends(get_traffic_controller)
):
    """Pause or resume internet access for a user."""
    if payload.is_paused:
        success = await traffic_ctrl.pause_user_internet(user_id, db)
        msg = "Internet paused for user"
    else:
        success = await traffic_ctrl.resume_user_internet(user_id, db)
        msg = "Internet resumed for user"

    if not success:
        raise HTTPException(status_code=404, detail="User not found")
    return APIResponse(data=True, message=msg)
