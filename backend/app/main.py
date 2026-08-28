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
                active_routers = await router_manager.get_all_active_routers(session)
                for r in active_routers:
                    try:
                        client = await router_manager.get_client(r.id, session=session)
                        if client:
                            dev_mgr = DeviceManager(client, router_id=r.id)
                            _, new_devices = await dev_mgr.sync_devices_from_router(session)
                            if new_devices and telegram_service:
                                for dev in new_devices:
                                    msg = (
                                        f"🔔 *New Device Discovered on {r.name}!*\n"
                                        f"• Host: `{dev.hostname or 'Unknown'}`\n"
                                        f"• IP: `{dev.ip_address}`\n"
                                        f"• MAC: `{dev.mac_address}`\n"
                                        f"• Vendor: `{dev.vendor or 'Unknown'}`"
                                    )
                                    await telegram_service.send_alert_to_admins(msg)
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
