import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.v1.endpoints.telegram import set_telegram_service
from backend.app.api.v1.endpoints.ws import router as ws_router
from backend.app.api.v1.router import api_v1_router
from backend.app.core.config import settings
from backend.app.db.session import AsyncSessionLocal, init_db
from backend.app.services.device_manager import DeviceManager
from backend.app.services.router_manager import router_manager
from backend.app.services.telegram_bot import TelegramBotService

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("mikroman.main")

telegram_service: TelegramBotService = None
bg_sync_task: asyncio.Task = None


async def background_sync_worker():
    """Periodic background discovery and health monitor for all configured active routers."""
    while True:
        try:
            async with AsyncSessionLocal() as session:
                from backend.app.db.models import AppSetting
                auto_scan_sett = await session.get(AppSetting, "auto_scan_enabled")
                is_auto_scan_enabled = (auto_scan_sett.value.lower() != "false") if auto_scan_sett else True

                active_routers = await router_manager.get_all_active_routers(session)
                for r in active_routers:
                    try:
                        client = await router_manager.get_client(r.id, session=session)
                        if client:
                            new_devices = []
                            if is_auto_scan_enabled:
                                dev_mgr = DeviceManager(client, router_id=r.id)
                                _, new_devices = await dev_mgr.sync_devices_from_router(session)

                            # Maintain RouterOS Simple Queues and FastTrack exemptions for all active users & unassigned devices
                            try:
                                from backend.app.db.models import Device, User
                                from backend.app.services.traffic_controller import TrafficController
                                tc = TrafficController(client)
                                from sqlalchemy import select
                                users_res = await session.execute(select(User))
                                for u in users_res.scalars().all():
                                    active_ips = [d.ip_address for d in u.devices if d.is_active and d.ip_address]
                                    await tc.sync_user_queue(u.id, u.name, active_ips, u.speed_limit)

                                # Sync unassigned quarantine devices and custom device queues
                                devs_res = await session.execute(
                                    select(Device).where(
                                        Device.is_active,
                                        Device.user_id.is_(None) | (Device.speed_limit != "default")
                                    )
                                )
                                for dev in devs_res.scalars().all():
                                    await tc.sync_device_queue(dev.id, session)
                            except Exception as qe:
                                logger.debug(f"Queue sync tick error for router {r.id}: {qe}")

                            # Collect hardware and interface time-series metrics
                            try:
                                from backend.app.services.metrics_collector import metrics_collector
                                await metrics_collector.collect_and_store(session, r.id, client)
                            except Exception as me:
                                logger.debug(f"Metrics collection tick error for router {r.id}: {me}")

                            # Collect and record cumulative traffic rollups for analytics
                            try:
                                from backend.app.services.analytics_engine import AnalyticsEngine
                                await AnalyticsEngine.record_traffic_snapshot(session, r.id, client)
                            except Exception as te:
                                logger.debug(f"Traffic rollups tick error for router {r.id}: {te}")

                            if new_devices:
                                try:
                                    from backend.app.api.v1.endpoints.ws import manager
                                    await manager.broadcast({
                                        "type": "devices_updated",
                                        "router_id": r.id,
                                        "new_count": len(new_devices)
                                    })
                                except Exception:
                                    pass

                                if telegram_service:
                                    for dev in new_devices:
                                        msg = (
                                            f"🔔 <b>New Device Discovered on {r.name}!</b>\n"
                                            f"• Host: <code>{dev.hostname or 'Unknown'}</code>\n"
                                            f"• IP: <code>{dev.ip_address}</code>\n"
                                            f"• MAC: <code>{dev.mac_address}</code>\n"
                                            f"• Vendor: <code>{dev.vendor or 'Unknown'}</code>"
                                        )
                                        await telegram_service.send_alert_to_admins(msg, parse_mode="HTML")
                    except Exception as e:
                        logger.debug(f"Sync error for router {r.name} ({r.id}): {e}")
        except Exception as e:
            logger.debug(f"Background sync tick error: {e}")

        await asyncio.sleep(settings.POLL_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global telegram_service, bg_sync_task
    logger.info("Initializing MikroMan Database...")
    await init_db()

    telegram_service = TelegramBotService(
        router_manager=router_manager,
        session_factory=AsyncSessionLocal
    )
    set_telegram_service(telegram_service)
    await telegram_service.start()

    bg_sync_task = asyncio.create_task(background_sync_worker())
    logger.info("MikroMan Engine initialized successfully.")

    yield

    if bg_sync_task:
        bg_sync_task.cancel()
    if telegram_service:
        await telegram_service.stop()
    await router_manager.aclose()
    logger.info("MikroMan Engine shut down cleanly.")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_v1_router)
app.include_router(ws_router)

# Mount frontend build if directory exists
dist_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "frontend", "dist")
if os.path.exists(dist_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(dist_dir, "assets")), name="assets")

    @app.get("/")
    async def serve_root():
        index_file = os.path.join(dist_dir, "index.html")
        if os.path.exists(index_file):
            return FileResponse(index_file)
        return {"message": "Frontend build not found. Running in API-only mode."}

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("ws/"):
            return None
        index_file = os.path.join(dist_dir, "index.html")
        if os.path.exists(index_file):
            return FileResponse(index_file)
        return {"message": "Frontend build not found. Running in API-only mode."}
