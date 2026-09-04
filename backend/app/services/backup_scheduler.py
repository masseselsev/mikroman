import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from backend.app.db.models import AppSetting, Router, RouterBackup
from backend.app.db.session import AsyncSessionLocal
from backend.app.services.backup_service import run_router_backup

logger = logging.getLogger("mikroman.backup_scheduler")


class BackupScheduler:
    """Background service that periodically evaluates and executes scheduled backups for all active routers."""

    def __init__(self, check_interval_seconds: float = 3600.0):
        self.check_interval_seconds = check_interval_seconds
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("BackupScheduler started")

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("BackupScheduler stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self.run_due_backups()
            except Exception as e:
                logger.error(f"Unexpected error in BackupScheduler run: {e}", exc_info=True)

            try:
                await asyncio.sleep(self.check_interval_seconds)
            except asyncio.CancelledError:
                break

    async def run_due_backups(self) -> int:
        """Inspect all active routers and trigger backups for any that are due."""
        due_count = 0
        async with AsyncSessionLocal() as session:
            # Check if backups are enabled globally
            q_enabled = await session.execute(
                select(AppSetting).filter(AppSetting.key == "backup_enabled")
            )
            enabled_setting = q_enabled.scalars().first()
            if enabled_setting and enabled_setting.value.lower() in ("false", "0", "no"):
                return 0

            q_interval = await session.execute(
                select(AppSetting).filter(AppSetting.key == "backup_interval_hours")
            )
            interval_setting = q_interval.scalars().first()
            interval_hours = 24
            if interval_setting:
                try:
                    interval_hours = int(interval_setting.value)
                except ValueError:
                    interval_hours = 24

            q_routers = await session.execute(
                select(Router).filter(Router.is_active.is_(True), Router.archived_at.is_(None))
            )
            routers = list(q_routers.scalars().all())

            now = datetime.now(timezone.utc)
            threshold = now - timedelta(hours=interval_hours)

            for router in routers:
                q_latest = await session.execute(
                    select(RouterBackup)
                    .filter(
                        RouterBackup.router_id == router.id,
                        RouterBackup.outcome.in_(["changed", "unchanged"]),
                    )
                    .order_by(RouterBackup.created_at.desc())
                )
                latest = q_latest.scalars().first()

                is_due = False
                if not latest:
                    is_due = True
                else:
                    created = latest.created_at
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    if created <= threshold:
                        is_due = True

                if is_due:
                    logger.info(f"Router {router.id} ({router.name}) backup is due. Triggering...")
                    try:
                        await run_router_backup(router.id, source="scheduled", db_session=session)
                        due_count += 1
                    except Exception as err:
                        logger.warning(f"Scheduled backup failed for router {router.id}: {err}")

        return due_count


backup_scheduler = BackupScheduler()
