import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import Router, RouterBackup
from backend.app.db.session import AsyncSessionLocal
from backend.app.services.backup_normalizer import compute_fingerprint, normalize_rsc
from backend.app.services.routeros.backup import generate_backup_password
from backend.app.services.routeros.client import RouterOSClient

logger = logging.getLogger("mikroman.backups")

BACKUP_STORAGE_DIR = os.getenv("BACKUP_STORAGE_DIR", "data/backups")
_router_locks: dict[int, asyncio.Lock] = {}


def get_router_lock(router_id: int) -> asyncio.Lock:
    if router_id not in _router_locks:
        _router_locks[router_id] = asyncio.Lock()
    return _router_locks[router_id]


def get_routeros_client(router: Router) -> RouterOSClient:
    return RouterOSClient(
        host=router.host,
        port=router.port,
        use_ssl=router.use_ssl,
        ssl_verify=router.ssl_verify,
        username=router.username,
        password=router.password,
        timeout=35.0,
    )


async def run_router_backup(
    router_id: int, source: str = "manual", db_session: Optional[AsyncSession] = None
) -> RouterBackup:
    """Execute a complete backup run for a router.

    1. Checks concurrency lock.
    2. Sweeps prior temp files.
    3. Runs /export and checks normalized SHA-256 fingerprint.
    4. If unchanged from latest successful backup: records 'unchanged' log without duplicate files.
    5. If changed or initial: runs /system/backup/save with AES password, stores binary on disk and .rsc in DB.
    6. Always sweeps temporary files from router in finally block.
    """
    lock = get_router_lock(router_id)
    if lock.locked():
        raise RuntimeError(f"A backup run for router {router_id} is already in flight")

    async with lock:
        should_close = False
        session = db_session
        if session is None:
            session = AsyncSessionLocal()
            should_close = True

        try:
            res = await session.execute(select(Router).filter(Router.id == router_id))
            router = res.scalars().first()
            if not router:
                raise ValueError(f"Router {router_id} not found")

            client = get_routeros_client(router)
            started_at = time.monotonic()
            stem = str(int(datetime.now(timezone.utc).timestamp() * 1000))

            # Pre-flight sweep
            await client.sweep_temporary_files()

            try:
                # Read identity metadata
                model = None
                serial = None
                os_version = None
                try:
                    res_sys = await client.get_system_resource()
                    if isinstance(res_sys, dict):
                        model = res_sys.get("board-name")
                        os_version = res_sys.get("version")
                    rb = await client.get_system_routerboard()
                    if isinstance(rb, dict):
                        serial = rb.get("serial-number")
                except Exception as id_err:
                    logger.debug(f"Could not read full identity for router {router_id}: {id_err}")

                # Export configuration script
                raw_rsc = await client.export_config(stem=stem)
                normalized_rsc = normalize_rsc(raw_rsc)
                fingerprint = compute_fingerprint(normalized_rsc)

                # Query latest successful backup
                q = await session.execute(
                    select(RouterBackup)
                    .filter(
                        RouterBackup.router_id == router_id,
                        RouterBackup.outcome.in_(["changed", "unchanged"]),
                    )
                    .order_by(RouterBackup.created_at.desc())
                )
                latest_backup = q.scalars().first()

                duration_ms = int((time.monotonic() - started_at) * 1000)

                # Deduplication check: configuration unchanged
                if latest_backup and latest_backup.fingerprint == fingerprint:
                    backup_rec = RouterBackup(
                        router_id=router_id,
                        created_at=datetime.now(timezone.utc),
                        outcome="unchanged",
                        source=source,
                        fingerprint=fingerprint,
                        rsc_content=None,
                        rsc_bytes=len(normalized_rsc.encode("utf-8")),
                        backup_file_path=None,
                        backup_bytes=0,
                        is_pinned=False,
                        model=model,
                        serial=serial,
                        os_version=os_version,
                        duration_ms=duration_ms,
                    )
                    session.add(backup_rec)
                    await session.commit()
                    await session.refresh(backup_rec)
                    logger.info(f"Router {router_id} backup: unchanged (fingerprint {fingerprint[:8]})")
                    return backup_rec

                # Config changed or initial: take binary backup
                backup_password = generate_backup_password(24)
                binary_bytes = await client.create_system_backup(stem=stem, password=backup_password)

                # Save binary to disk
                dir_path = Path(BACKUP_STORAGE_DIR) / str(router_id)
                dir_path.mkdir(parents=True, exist_ok=True)
                file_rel_path = f"{router_id}/{stem}_{fingerprint[:8]}.backup"
                abs_file_path = Path(BACKUP_STORAGE_DIR) / file_rel_path
                with open(abs_file_path, "wb") as f:
                    f.write(binary_bytes)

                duration_ms = int((time.monotonic() - started_at) * 1000)

                backup_rec = RouterBackup(
                    router_id=router_id,
                    created_at=datetime.now(timezone.utc),
                    outcome="changed",
                    source=source,
                    fingerprint=fingerprint,
                    rsc_content=normalized_rsc,
                    rsc_bytes=len(normalized_rsc.encode("utf-8")),
                    backup_file_path=file_rel_path,
                    backup_bytes=len(binary_bytes),
                    backup_password=backup_password,
                    is_pinned=False,
                    model=model,
                    serial=serial,
                    os_version=os_version,
                    duration_ms=duration_ms,
                )
                session.add(backup_rec)
                await session.commit()
                await session.refresh(backup_rec)
                logger.info(
                    f"Router {router_id} backup: changed ({backup_rec.rsc_bytes} bytes rsc, {backup_rec.backup_bytes} bytes binary)"
                )

                # Trigger auto-pruning
                await prune_router_backups(router_id, db_session=session)

                return backup_rec

            except Exception as e:
                duration_ms = int((time.monotonic() - started_at) * 1000)
                logger.warning(f"Router {router_id} backup failed: {e}")
                fail_rec = RouterBackup(
                    router_id=router_id,
                    created_at=datetime.now(timezone.utc),
                    outcome="failed",
                    source=source,
                    error_message=str(e),
                    duration_ms=duration_ms,
                )
                session.add(fail_rec)
                await session.commit()
                await session.refresh(fail_rec)
                return fail_rec

            finally:
                # Guaranteed cleanup of router storage
                await client.sweep_temporary_files()
                await client.aclose()

        finally:
            if should_close:
                await session.close()


async def prune_router_backups(
    router_id: int, max_count: int = 30, max_days: int = 90, db_session: Optional[AsyncSession] = None
) -> int:
    """Prune unpinned router backups that exceed max_count or max_days.

    Pinned backups (is_pinned=True) are strictly protected and never pruned.
    """
    should_close = False
    session = db_session
    if session is None:
        session = AsyncSessionLocal()
        should_close = True

    try:
        q = await session.execute(
            select(RouterBackup)
            .filter(
                RouterBackup.router_id == router_id,
                RouterBackup.is_pinned.is_(False),
                RouterBackup.outcome != "failed",
            )
            .order_by(RouterBackup.created_at.desc())
        )
        unpinned = list(q.scalars().all())

        cutoff_date = datetime.now(timezone.utc) - timedelta(days=max_days)
        to_delete = []

        for idx, backup in enumerate(unpinned):
            created = backup.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)

            if idx >= max_count or created < cutoff_date:
                to_delete.append(backup)

        for b in to_delete:
            if b.backup_file_path:
                file_path = Path(BACKUP_STORAGE_DIR) / b.backup_file_path
                try:
                    file_path.unlink(missing_ok=True)
                except Exception as err:
                    logger.warning(f"Could not remove backup file {file_path}: {err}")
            await session.delete(b)

        if to_delete:
            await session.commit()
            logger.info(f"Pruned {len(to_delete)} old backups for router {router_id}")

        return len(to_delete)

    finally:
        if should_close:
            await session.close()
