import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import Router, RouterBackup
from backend.app.db.session import get_db
from backend.app.schemas.backup import (
    RouterBackupListResponse,
    RouterBackupResponse,
    RouterBackupUpdate,
)
from backend.app.services.backup_normalizer import normalize_rsc
from backend.app.services.backup_service import (
    BACKUP_STORAGE_DIR,
    get_routeros_client,
    run_router_backup,
)
from backend.app.services.diff_engine import DiffEngine, DiffResult

logger = logging.getLogger("mikroman.api.backups")

router = APIRouter(prefix="/routers/{router_id}/backups", tags=["backups"])


async def _get_rsc_content_for_backup(session: AsyncSession, backup: RouterBackup) -> str:
    """Retrieve the .rsc content for a backup, resolving deduplicated pointers if needed."""
    if backup.rsc_content:
        return backup.rsc_content
    if backup.fingerprint:
        # Find the latest predecessor with the same fingerprint that stored the content
        q = await session.execute(
            select(RouterBackup)
            .filter(
                RouterBackup.router_id == backup.router_id,
                RouterBackup.fingerprint == backup.fingerprint,
                RouterBackup.rsc_content.is_not(None),
            )
            .order_by(RouterBackup.created_at.desc())
        )
        parent = q.scalars().first()
        if parent and parent.rsc_content:
            return parent.rsc_content
    return ""


@router.get("", response_model=RouterBackupListResponse)
async def list_backups(
    router_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    outcome: Optional[str] = Query(None),
    pinned_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """List historical backup runs for a specific router with filtering and pagination."""
    query = select(RouterBackup).filter(RouterBackup.router_id == router_id)
    if outcome:
        query = query.filter(RouterBackup.outcome == outcome)
    if pinned_only:
        query = query.filter(RouterBackup.is_pinned.is_(True))

    count_query = select(func.count()).select_from(query.subquery())
    total_res = await db.execute(count_query)
    total = total_res.scalar() or 0

    offset = (page - 1) * page_size
    query = query.order_by(RouterBackup.created_at.desc()).offset(offset).limit(page_size)
    items_res = await db.execute(query)
    items = list(items_res.scalars().all())

    return RouterBackupListResponse(
        items=[RouterBackupResponse.model_validate(b) for b in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/run", response_model=RouterBackupResponse)
async def trigger_backup(
    router_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Trigger an on-demand manual backup run for the specified router."""
    router_res = await db.execute(select(Router).filter(Router.id == router_id))
    r = router_res.scalars().first()
    if not r:
        raise HTTPException(status_code=404, detail="Router not found")

    try:
        backup = await run_router_backup(router_id, source="manual", db_session=db)
        return RouterBackupResponse.model_validate(backup)
    except Exception as e:
        logger.error(f"Manual backup failed for router {router_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Backup failed: {e}")


@router.get("/diff", response_model=DiffResult)
async def get_backup_diff(
    router_id: int,
    base_id: int = Query(..., description="ID of baseline backup"),
    target_id: str = Query(..., description="ID of target backup or 'live'"),
    db: AsyncSession = Depends(get_db),
):
    """Compute visual unified diff between two backup revisions, or between a backup and the live router state."""
    base_res = await db.execute(
        select(RouterBackup).filter(
            RouterBackup.id == base_id, RouterBackup.router_id == router_id
        )
    )
    base_backup = base_res.scalars().first()
    if not base_backup:
        raise HTTPException(status_code=404, detail=f"Baseline backup {base_id} not found")

    base_text = await _get_rsc_content_for_backup(db, base_backup)

    if target_id.lower() == "live":
        router_res = await db.execute(select(Router).filter(Router.id == router_id))
        r = router_res.scalars().first()
        if not r:
            raise HTTPException(status_code=404, detail="Router not found")

        client = get_routeros_client(r)
        try:
            raw_rsc = await client.export_config(stem="live_diff")
            target_text = normalize_rsc(raw_rsc)
        finally:
            await client.sweep_temporary_files()
            await client.aclose()

        return DiffEngine.diff_texts(
            base_text=base_text,
            target_text=target_text,
            fromfile=f"backup_{base_backup.id}.rsc",
            tofile="live_router.rsc",
            base_id=base_backup.id,
            target_id=None,
            is_target_live=True,
        )

    try:
        target_id_num = int(target_id)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="target_id must be a numeric backup ID or 'live'"
        )

    target_res = await db.execute(
        select(RouterBackup).filter(
            RouterBackup.id == target_id_num, RouterBackup.router_id == router_id
        )
    )
    target_backup = target_res.scalars().first()
    if not target_backup:
        raise HTTPException(
            status_code=404, detail=f"Target backup {target_id_num} not found"
        )

    target_text = await _get_rsc_content_for_backup(db, target_backup)

    return DiffEngine.diff_texts(
        base_text=base_text,
        target_text=target_text,
        fromfile=f"backup_{base_backup.id}.rsc",
        tofile=f"backup_{target_backup.id}.rsc",
        base_id=base_backup.id,
        target_id=target_backup.id,
        is_target_live=False,
    )


@router.get("/{backup_id}", response_model=RouterBackupResponse)
async def get_backup_detail(
    router_id: int,
    backup_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Retrieve details and metadata for a single backup run."""
    res = await db.execute(
        select(RouterBackup).filter(
            RouterBackup.id == backup_id, RouterBackup.router_id == router_id
        )
    )
    backup = res.scalars().first()
    if not backup:
        raise HTTPException(status_code=404, detail="Backup not found")
    return RouterBackupResponse.model_validate(backup)


@router.get("/{backup_id}/download/rsc")
async def download_rsc(
    router_id: int,
    backup_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Download the plaintext .rsc script export file."""
    res = await db.execute(
        select(RouterBackup).filter(
            RouterBackup.id == backup_id, RouterBackup.router_id == router_id
        )
    )
    backup = res.scalars().first()
    if not backup:
        raise HTTPException(status_code=404, detail="Backup not found")

    rsc_text = await _get_rsc_content_for_backup(db, backup)
    if not rsc_text:
        raise HTTPException(status_code=404, detail="No configuration script available for this backup")

    filename = f"router_{router_id}_backup_{backup.id}.rsc"
    return Response(
        content=rsc_text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{backup_id}/download/backup")
async def download_binary_backup(
    router_id: int,
    backup_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Download the encrypted binary .backup file and expose the decryption password in headers."""
    res = await db.execute(
        select(RouterBackup).filter(
            RouterBackup.id == backup_id, RouterBackup.router_id == router_id
        )
    )
    backup = res.scalars().first()
    if not backup or not backup.backup_file_path:
        raise HTTPException(status_code=404, detail="No binary backup available for this run")

    file_path = Path(BACKUP_STORAGE_DIR) / backup.backup_file_path
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Binary backup file missing from storage disk")

    filename = f"router_{router_id}_backup_{backup.id}.backup"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    if backup.backup_password:
        headers["X-Backup-Password"] = backup.backup_password

    return FileResponse(
        path=str(file_path),
        media_type="application/octet-stream",
        filename=filename,
        headers=headers,
    )


@router.patch("/{backup_id}", response_model=RouterBackupResponse)
async def update_backup(
    router_id: int,
    backup_id: int,
    payload: RouterBackupUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update milestone pinning status or custom user note for a backup."""
    res = await db.execute(
        select(RouterBackup).filter(
            RouterBackup.id == backup_id, RouterBackup.router_id == router_id
        )
    )
    backup = res.scalars().first()
    if not backup:
        raise HTTPException(status_code=404, detail="Backup not found")

    if payload.is_pinned is not None:
        backup.is_pinned = payload.is_pinned
    if payload.note is not None:
        backup.note = payload.note

    await db.commit()
    await db.refresh(backup)
    return RouterBackupResponse.model_validate(backup)


@router.delete("/{backup_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_backup(
    router_id: int,
    backup_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a backup record and unlink any associated binary .backup file from server disk."""
    res = await db.execute(
        select(RouterBackup).filter(
            RouterBackup.id == backup_id, RouterBackup.router_id == router_id
        )
    )
    backup = res.scalars().first()
    if not backup:
        raise HTTPException(status_code=404, detail="Backup not found")

    if backup.backup_file_path:
        file_path = Path(BACKUP_STORAGE_DIR) / backup.backup_file_path
        try:
            file_path.unlink(missing_ok=True)
        except Exception as err:
            logger.warning(f"Error removing backup file {file_path}: {err}")

    await db.delete(backup)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
