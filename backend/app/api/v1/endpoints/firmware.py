import asyncio
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


async def _wait_for_router_online(router_id: int, timeout_seconds: int = 300, poll_interval: float = 5.0) -> bool:
    """Wait for router to come back online after reboot.

    Polls /system/resource until successful or timeout. Returns True if router
    responded, False if timeout exceeded.
    """
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    while asyncio.get_event_loop().time() < deadline:
        try:
            client = await router_manager.get_client(router_id)
            await client.get_system_resource()
            return True
        except Exception:
            await asyncio.sleep(poll_interval)
    return False
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

    # Gate 4: Dispatch package install (triggers reboot)
    # Package update must come first - bootloader firmware is bundled with it
    await client.install_package_update()

    # Gate 5: Wait for router to come back online after package update reboot
    bootloader_upgraded = False
    if payload.stage_bootloader:
        logger.info(f"Waiting for router {router_id} to come back online after package update...")
        online = await _wait_for_router_online(router_id, timeout_seconds=300, poll_interval=5.0)

        if online:
            # Refresh client after reboot
            client = await router_manager.get_client(router_id)

            # Check if bootloader firmware is now available
            rb = await client.get_routerboard_status()
            if rb.get("firmware_available"):
                logger.info(f"Upgrading RouterBOOT firmware on router {router_id}")
                await client.upgrade_routerboard_firmware()
                bootloader_upgraded = True
            else:
                logger.info(f"No bootloader firmware available on router {router_id}")
        else:
            logger.error(f"Router {router_id} did not come back online within timeout after package update")

    status_msg = "Upgrade initiated. Router is rebooting."
    if bootloader_upgraded:
        status_msg = "Package and bootloader upgrade complete. Router is rebooting."
    elif payload.stage_bootloader and not bootloader_upgraded:
        status_msg = "Package update complete. Bootloader firmware not available."

    return {
        "status": "rebooting",
        "backup_id": backup.id,
        "target_version": latest,
        "bootloader_upgraded": bootloader_upgraded,
        "message": status_msg,
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

