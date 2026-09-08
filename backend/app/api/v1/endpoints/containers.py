"""RouterOS container management for a selected router.

The container package is optional and absent on a stock install, so the list
endpoint never errors on that account: it returns a ``support`` block describing
the state, and the frontend renders a banner. The action endpoints do return a
clear error when the feature is unavailable, since there is nothing to act on.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.session import get_db
from backend.app.schemas.common import APIResponse
from backend.app.schemas.container import (
    ContainerCreateRequest,
    ContainerFormatRequest,
    ContainerFormatResultDTO,
    ContainerOverviewDTO,
    ContainerSetupPlanDTO,
    ContainerSetupRequest,
    ContainerStorageDTO,
)
from backend.app.services.container_manager import ContainerManager
from backend.app.services.container_setup import (
    ContainerSetupService,
    StorageFormatError,
    format_storage,
    read_storage,
)
from backend.app.services.router_manager import router_manager
from backend.app.services.routeros.provisioning import RouterOSCommandError
from backend.app.utils_format import format_bytes_human

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


@router.get("/storage", response_model=APIResponse[ContainerStorageDTO])
async def storage_inventory(
    router_id: int,
    storage_dir: Optional[str] = Query(
        None, description="Also judge this slot; omit to get only what the router has"
    ),
    db: AsyncSession = Depends(get_db),
):
    """The router's disks and partitions, and whether a chosen one can host containers.

    The page reads this before the operator types anything: a picker built from
    ``/disk`` cannot offer a device that is unmounted, read-only or too small,
    whereas a free-text path can - and the pull fails minutes later with a
    message from the registry.
    """
    mgr = await _manager(router_id, db)
    data = await read_storage(mgr.client, storage_dir)
    if data.ready:
        message = f"{data.matched_slot}: {data.fs}, {format_bytes_human(data.free_bytes or 0)} free"
    elif data.problems:
        message = data.problems[0]
    else:
        message = "no storage chosen yet"
    return APIResponse(data=data, message=message)


@router.post("/storage/format", response_model=APIResponse[ContainerFormatResultDTO])
async def storage_format(
    router_id: int, payload: ContainerFormatRequest, db: AsyncSession = Depends(get_db)
):
    """Format a disk or partition for container use. **Destroys everything on it.**

    Guarded in the service, not here: the slot name has to be repeated in
    ``confirm``, the device has to exist in ``/disk``, the filesystem has to be
    one RouterOS formats, and the target must hold no container state - which
    includes the storage this very instance is running from.
    """
    mgr = await _manager(router_id, db)
    support = await mgr._probe_support()
    if support.status != "ready":
        raise HTTPException(status_code=409, detail=support.message or "Containers are not available on this router")
    try:
        result = await format_storage(mgr.client, payload)
    except StorageFormatError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except RouterOSCommandError as e:
        raise HTTPException(status_code=502, detail=f"The router refused the format: {e.detail}") from e
    except Exception as e:
        logger.warning(f"Storage format failed for router {router_id}: {e}")
        raise HTTPException(status_code=502, detail=f"Format failed: {e}") from e
    return APIResponse(data=result, message=result["detail"])


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
