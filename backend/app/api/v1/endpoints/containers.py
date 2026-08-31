"""RouterOS container management for a selected router.

The container package is optional and absent on a stock install, so the list
endpoint never errors on that account: it returns a ``support`` block describing
the state, and the frontend renders a banner. The action endpoints do return a
clear error when the feature is unavailable, since there is nothing to act on.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.session import get_db
from backend.app.schemas.common import APIResponse
from backend.app.schemas.container import ContainerCreateRequest, ContainerOverviewDTO
from backend.app.services.container_manager import ContainerManager
from backend.app.services.router_manager import router_manager

logger = logging.getLogger("mikroman.containers")

router = APIRouter(prefix="/routers/{router_id}/containers", tags=["Containers"])

_VALID_ACTIONS = {"start", "stop", "remove"}


async def _manager(router_id: int, db: AsyncSession) -> ContainerManager:
    client = await router_manager.get_client(router_id, session=db)
    if client is None:
        raise HTTPException(status_code=404, detail="Router not found or not reachable")
    return ContainerManager(client)


@router.get("", response_model=APIResponse[ContainerOverviewDTO])
async def list_containers(router_id: int, db: AsyncSession = Depends(get_db)):
    """Containers, mounts, envs and global config for this router, plus a
    ``support`` block that says whether the feature is usable at all."""
    mgr = await _manager(router_id, db)
    overview = await mgr.get_overview()
    return APIResponse(data=overview)


@router.post("/{container_id}/{action}", response_model=APIResponse[bool])
async def container_action(
    router_id: int, container_id: str, action: str, db: AsyncSession = Depends(get_db)
):
    """Run ``start`` / ``stop`` / ``remove`` on one container."""
    if action not in _VALID_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown action '{action}'")
    mgr = await _manager(router_id, db)
    support = await mgr._probe_support()
    if support.status != "ready":
        raise HTTPException(status_code=409, detail=support.message or "Containers are not available on this router")
    try:
        ok = await mgr.run_action(action, container_id)
    except Exception as e:
        logger.warning(f"Container {action} failed for {container_id}: {e}")
        raise HTTPException(status_code=502, detail=f"Router rejected the {action}: {e}") from e
    return APIResponse(data=ok, message=f"Container {action} dispatched")


@router.post("", response_model=APIResponse[dict])
async def create_container(
    router_id: int, payload: ContainerCreateRequest, db: AsyncSession = Depends(get_db)
):
    """Create a container from a remote image."""
    mgr = await _manager(router_id, db)
    support = await mgr._probe_support()
    if support.status != "ready":
        raise HTTPException(status_code=409, detail=support.message or "Containers are not available on this router")
    try:
        result = await mgr.create(payload.model_dump())
    except Exception as e:
        logger.warning(f"Container create failed: {e}")
        raise HTTPException(status_code=502, detail=f"Router rejected the create: {e}") from e
    return APIResponse(data=result, message="Container creation dispatched")
