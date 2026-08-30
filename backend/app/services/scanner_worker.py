import asyncio
import logging
from typing import Optional

from backend.app.api.v1.endpoints.ws import manager
from backend.app.db.session import AsyncSessionLocal
from backend.app.services.device_manager import DeviceManager
from backend.app.services.router_manager import router_manager

logger = logging.getLogger("mikroman.scanner_worker")


class NetworkScannerWorker:
    """Background task that periodically syncs RouterOS discovery tables and broadcasts updates."""

    def __init__(self, interval_seconds: int = 20):
        self.interval_seconds = interval_seconds
        self._task: Optional[asyncio.Task] = None
        self._running: bool = False

    def start(self):
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._run_loop())
            logger.info(f"Background Network Scanner started (Interval: {self.interval_seconds}s)")

    def stop(self):
        if self._running:
            self._running = False
            if self._task:
                self._task.cancel()
                self._task = None
            logger.info("Background Network Scanner stopped")

    async def _run_loop(self):
        # Initial small delay to let app boot
        await asyncio.sleep(5)
        while self._running:
            try:
                async with AsyncSessionLocal() as session:
                    client = await router_manager.get_client(session=session)
                    if client:
                        dev_mgr = DeviceManager(client)
                        all_devs, newly_discovered = await dev_mgr.sync_devices_from_router(session)
                        if newly_discovered:
                            logger.info(f"Auto-scan: Discovered {len(newly_discovered)} new device(s)")
                            await manager.broadcast({
                                "type": "devices_updated",
                                "new_count": len(newly_discovered),
                                "total_active": len([d for d in all_devs if d.is_active])
                            })
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Auto-scan background iteration skipped/failed: {e}")

            try:
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break


scanner_worker = NetworkScannerWorker(interval_seconds=20)
