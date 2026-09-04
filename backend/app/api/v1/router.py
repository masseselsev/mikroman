from fastapi import APIRouter

from backend.app.api.v1.endpoints.analytics import router as analytics_router
from backend.app.api.v1.endpoints.backups import router as backups_router
from backend.app.api.v1.endpoints.connections import router as connections_router
from backend.app.api.v1.endpoints.containers import router as containers_router
from backend.app.api.v1.endpoints.devices import router as devices_router
from backend.app.api.v1.endpoints.firmware import router as firmware_router
from backend.app.api.v1.endpoints.logs import router as logs_router
from backend.app.api.v1.endpoints.metrics import router as metrics_router
from backend.app.api.v1.endpoints.routers import router as routers_router
from backend.app.api.v1.endpoints.speedtest import router as speedtest_router
from backend.app.api.v1.endpoints.system import router as system_router
from backend.app.api.v1.endpoints.telegram import router as telegram_router
from backend.app.api.v1.endpoints.traffic import router as traffic_router
from backend.app.api.v1.endpoints.users import router as users_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(routers_router)
api_v1_router.include_router(firmware_router, prefix="/routers/{router_id}/firmware", tags=["Firmware & Upgrades"])
api_v1_router.include_router(backups_router)
api_v1_router.include_router(logs_router)
api_v1_router.include_router(users_router)
api_v1_router.include_router(devices_router)
api_v1_router.include_router(traffic_router)
api_v1_router.include_router(system_router)
api_v1_router.include_router(telegram_router)
api_v1_router.include_router(metrics_router)
api_v1_router.include_router(analytics_router)
api_v1_router.include_router(containers_router)
api_v1_router.include_router(speedtest_router)
api_v1_router.include_router(connections_router)
