from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.db.models import AlertLog, AppSetting
from backend.app.db.session import get_db
from backend.app.schemas.common import AlertLogDTO, APIResponse
from backend.app.schemas.routeros import InterfaceDTO, RouterSystemHealth, RouterSystemResource
from backend.app.services.routeros import RouterOSClient

router = APIRouter(prefix="/system", tags=["System & Settings"])


def get_router_client() -> RouterOSClient:
    return RouterOSClient()


@router.get("/status", response_model=APIResponse[Dict[str, Any]])
async def get_system_status(router_client: RouterOSClient = Depends(get_router_client)):
    """Fetch live RouterOS system status, CPU, memory, health, and test connection."""
    test_res = await router_client.test_connection()
    if not test_res.get("connected"):
        return APIResponse(
            success=False,
            message="Cannot reach RouterOS REST API",
            data={"connected": False, "error": test_res.get("error")}
        )

    resource: RouterSystemResource = await router_client.get_system_resource()
    health: RouterSystemHealth = await router_client.get_system_health()

    return APIResponse(
        data={
            "connected": True,
            "resource": resource.model_dump(),
            "health": health.model_dump(),
            "app_version": settings.APP_VERSION,
        }
    )


@router.get("/interfaces", response_model=APIResponse[List[InterfaceDTO]])
async def get_interfaces(router_client: RouterOSClient = Depends(get_router_client)):
    """Fetch list of network interfaces and counters."""
    interfaces = await router_client.get_interfaces()
    return APIResponse(data=interfaces)


@router.post("/reboot", response_model=APIResponse[bool])
async def reboot_router(router_client: RouterOSClient = Depends(get_router_client)):
    """Reboot MikroTik router."""
    success = await router_client.reboot_system()
    return APIResponse(data=success, message="Reboot signal dispatched to router")


@router.get("/alerts", response_model=APIResponse[List[AlertLogDTO]])
async def get_alerts(limit: int = 50, db: AsyncSession = Depends(get_db)):
    """Fetch recent alert events."""
    result = await db.execute(select(AlertLog).order_by(AlertLog.created_at.desc()).limit(limit))
    alerts = result.scalars().all()
    return APIResponse(data=[AlertLogDTO.model_validate(a) for a in alerts])


@router.get("/settings", response_model=APIResponse[Dict[str, str]])
async def get_settings(db: AsyncSession = Depends(get_db)):
    """Fetch stored application settings."""
    result = await db.execute(select(AppSetting))
    settings_list = result.scalars().all()
    data = {s.key: s.value for s in settings_list}

    # Defaults
    if "theme" not in data:
        data["theme"] = "dark"
    if "lang" not in data:
        data["lang"] = "en"
    if "router_host" not in data:
        data["router_host"] = settings.ROUTEROS_HOST
    if "router_port" not in data:
        data["router_port"] = str(settings.ROUTEROS_PORT)
    if "router_user" not in data:
        data["router_user"] = settings.ROUTEROS_USER

    return APIResponse(data=data)


@router.post("/settings", response_model=APIResponse[bool])
async def save_settings(payload: Dict[str, str], db: AsyncSession = Depends(get_db)):
    """Save application settings."""
    for k, v in payload.items():
        setting = await db.get(AppSetting, k)
        if setting:
            setting.value = v
        else:
            setting = AppSetting(key=k, value=v)
            db.add(setting)
    await db.commit()
    return APIResponse(data=True, message="Settings saved successfully")
