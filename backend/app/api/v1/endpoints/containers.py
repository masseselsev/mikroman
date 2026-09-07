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
from backend.app.schemas.container import (
    ContainerCreateRequest,
    ContainerMigrateRequest,
    ContainerMigrateResultDTO,
    ContainerOverviewDTO,
    ContainerSetupPlanDTO,
    ContainerSetupRequest,
)
from backend.app.services.container_manager import ContainerManager
from backend.app.services.container_setup import (
    ContainerSetupService,
    DataMigrationError,
    migrate_data,
)
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


@router.post("/setup/plan", response_model=APIResponse[ContainerSetupPlanDTO])
async def setup_plan(
    router_id: int, payload: ContainerSetupRequest, db: AsyncSession = Depends(get_db)
):
    """What preparing this router for a container would change. Writes nothing.

    Declared above ``/{container_id}/{action}`` on purpose: FastAPI matches in
    declaration order, so under the later route ``setup`` would be read as a
    container id and ``plan`` rejected as an unknown action.
    """
    mgr = await _manager(router_id, db)
    support = await mgr._probe_support()
    if support.status != "ready":
        raise HTTPException(status_code=409, detail=support.message or "Containers are not available on this router")
    plan = await ContainerSetupService(mgr.client).plan(payload)
    return APIResponse(data=plan, message="Plan computed; nothing was written")


@router.post("/setup/apply", response_model=APIResponse[ContainerSetupPlanDTO])
async def setup_apply(
    router_id: int, payload: ContainerSetupRequest, db: AsyncSession = Depends(get_db)
):
    """Apply the plan: storage, directories, container network, mount, container.

    Idempotent - every step checks what the router already has and refuses to
    touch anything it did not create. Stops at the first command the router
    refuses and reports which steps did land.
    """
    mgr = await _manager(router_id, db)
    support = await mgr._probe_support()
    if support.status != "ready":
        raise HTTPException(status_code=409, detail=support.message or "Containers are not available on this router")
    plan = await ContainerSetupService(mgr.client).apply(payload)
    if not plan.ok:
        raise HTTPException(
            status_code=502,
            detail="; ".join(plan.blockers) or "the router refused part of the setup",
        )
    return APIResponse(data=plan, message=f"Setup applied ({sum(1 for s in plan.steps if s.applied)} changes written)")


@router.post("/migrate-data", response_model=APIResponse[ContainerMigrateResultDTO])
async def migrate_data_here(
    router_id: int, payload: ContainerMigrateRequest, db: AsyncSession = Depends(get_db)
):
    """Stage this installation's database and secret key for the container's mount.

    Run this while the container is created but not started. The snapshot comes
    from the live database through SQLite's backup API, so polling keeps working
    during the copy. RouterOS has no upload endpoint for a binary this size, so
    the response lists the two files to copy and where they go; afterwards start
    the container and stop this instance.
    """
    mgr = await _manager(router_id, db)
    try:
        result = await migrate_data(
            mgr.client,
            storage_dir=payload.storage_dir,
            data_dir_name=payload.data_dir_name,
            container_name=payload.container_name,
        )
    except DataMigrationError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:
        logger.warning(f"Data migration to router {router_id} failed: {e}")
        raise HTTPException(status_code=502, detail=f"Migration failed: {e}") from e
    return APIResponse(
        data=result,
        message=f"{result['database_bytes']} bytes staged; {len(result['next_steps'])} steps left",
    )


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
