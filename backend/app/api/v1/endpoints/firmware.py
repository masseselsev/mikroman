import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import Router
from backend.app.db.session import get_db
from backend.app.schemas.firmware import (
    BootloaderUpgradePayload,
    ChangelogOut,
    FirmwareChannelUpdatePayload,
    FirmwareUpgradePayload,
    PackageUpdateInfo,
    RouterBoardInfo,
    RouterFirmwareStatusOut,
)
from backend.app.services.backup_service import run_router_backup
from backend.app.services.changelog import changelog_service
from backend.app.services.router_manager import router_manager

logger = logging.getLogger("mikroman.api.firmware")
router = APIRouter()


async def _get_router(router_id: int, db: AsyncSession) -> Router:
    res = await db.execute(select(Router).filter(Router.id == router_id))
    r = res.scalars().first()
    if not r:
        raise HTTPException(status_code=404, detail=f"Router with ID {router_id} not found")
    return r


@router.get("", response_model=RouterFirmwareStatusOut)
async def get_firmware_status(router_id: int, db: AsyncSession = Depends(get_db)):
    r = await _get_router(router_id, db)
    client = await router_manager.get_client(router_id)
    pkg = await client.get_package_update_status()
    rb = await client.get_routerboard_status()

    return RouterFirmwareStatusOut(
        router_id=r.id,
        router_name=r.name,
        packages=PackageUpdateInfo(**pkg),
        routerboard=RouterBoardInfo(**rb),
        checked_at=datetime.now(timezone.utc),
    )


@router.post("/check", response_model=RouterFirmwareStatusOut)
async def check_firmware_updates(router_id: int, db: AsyncSession = Depends(get_db)):
    r = await _get_router(router_id, db)
    client = await router_manager.get_client(router_id)
    pkg = await client.check_for_package_updates()
    rb = await client.get_routerboard_status()

    return RouterFirmwareStatusOut(
        router_id=r.id,
        router_name=r.name,
        packages=PackageUpdateInfo(**pkg),
        routerboard=RouterBoardInfo(**rb),
        checked_at=datetime.now(timezone.utc),
    )


@router.put("/channel", response_model=RouterFirmwareStatusOut)
async def set_update_channel(
    router_id: int, payload: FirmwareChannelUpdatePayload, db: AsyncSession = Depends(get_db)
):
    r = await _get_router(router_id, db)
    client = await router_manager.get_client(router_id)
    pkg = await client.set_package_update_channel(payload.channel)
    rb = await client.get_routerboard_status()

    return RouterFirmwareStatusOut(
        router_id=r.id,
        router_name=r.name,
        packages=PackageUpdateInfo(**pkg),
        routerboard=RouterBoardInfo(**rb),
        checked_at=datetime.now(timezone.utc),
    )


@router.get("/changelog", response_model=ChangelogOut)
async def get_changelog(version: str = Query(..., description="Target RouterOS version")):
    try:
        notes = await changelog_service.get_notes(version)
        return ChangelogOut(version=version, notes=notes)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/upgrade")
async def execute_router_upgrade(
    router_id: int, payload: FirmwareUpgradePayload, db: AsyncSession = Depends(get_db)
):
    r = await _get_router(router_id, db)

    # Gate 1: Strict name match
    if payload.confirm_name.strip() != r.name.strip():
        raise HTTPException(
            status_code=400,
            detail=f"Confirmation mismatch: expected '{r.name}', got '{payload.confirm_name}'",
        )

    client = await router_manager.get_client(router_id)
    pkg = await client.get_package_update_status()

    # Gate 2: Version sanity check
    if not pkg.get("update_available"):
        raise HTTPException(status_code=400, detail="Router is already on the newest version")

    # Gate 3: Automated pinned disaster-recovery backup
    installed = pkg.get("installed_version", "unknown")
    latest = pkg.get("latest_version") or "latest"
    channel = pkg.get("channel", "stable")
    note = f"Pre-upgrade backup v{installed} -> v{latest} ({channel})"

    try:
        backup = await run_router_backup(
            router_id=router_id, source="manual", db_session=db
        )
        backup.is_pinned = True
        backup.note = note
        await db.commit()
    except Exception as e:
        logger.error(f"Pre-upgrade backup failed for router {router_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Pre-upgrade disaster recovery backup failed ({e}). Upgrade aborted for safety.",
        )

    # Gate 4: RouterBOOT staging
    if payload.stage_bootloader:
        rb = await client.get_routerboard_status()
        if rb.get("firmware_available"):
            await client.upgrade_routerboard_firmware()

    # Gate 5: Dispatch package install
    await client.install_package_update()
    return {
        "status": "rebooting",
        "backup_id": backup.id,
        "target_version": latest,
        "message": f"Upgrade initiated. Router {r.name} is rebooting into v{latest}.",
    }


@router.post("/bootloader")
async def upgrade_bootloader_only(
    router_id: int, payload: BootloaderUpgradePayload, db: AsyncSession = Depends(get_db)
):
    r = await _get_router(router_id, db)
    if payload.confirm_name.strip() != r.name.strip():
        raise HTTPException(
            status_code=400,
            detail=f"Confirmation mismatch: expected '{r.name}', got '{payload.confirm_name}'",
        )

    client = await router_manager.get_client(router_id)
    rb = await client.get_routerboard_status()
    if not rb.get("firmware_available"):
        raise HTTPException(status_code=400, detail="Bootloader is already on the upgrade firmware")

    await client.upgrade_routerboard_firmware()
    if payload.reboot:
        await client.reboot_system()
        return {"status": "rebooting", "message": "Bootloader staged and router is rebooting."}

    return {"status": "staged", "message": "Bootloader upgrade staged. It will apply on next reboot."}

