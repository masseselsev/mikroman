import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.db.models import AlertLog, AppSetting
from backend.app.db.session import get_db
from backend.app.schemas.common import AlertLogDTO, APIResponse
from backend.app.schemas.routeros import InterfaceDTO, RouterSystemHealth, RouterSystemResource
from backend.app.services.router_manager import router_manager
from backend.app.services.routeros import RouterOSClient

logger = logging.getLogger("mikroman.system")

router = APIRouter(prefix="/system", tags=["System & Settings"])


async def get_router_client(db: AsyncSession = Depends(get_db)) -> RouterOSClient:
    client = await router_manager.get_client(session=db)
    return client or RouterOSClient()


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
    if "unassigned_device_speed_limit" not in data:
        data["unassigned_device_speed_limit"] = "5M/5M"
    if "temp_warning_threshold" not in data:
        data["temp_warning_threshold"] = "80"
    if "auto_scan_enabled" not in data:
        data["auto_scan_enabled"] = "true"

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

    # If unassigned_device_speed_limit changed, update all unassigned devices
    if "unassigned_device_speed_limit" in payload:
        new_unassigned_limit = payload["unassigned_device_speed_limit"]
        from backend.app.db.models import Device
        from backend.app.services.traffic_controller import TrafficController
        unassigned_res = await db.execute(select(Device).where(Device.user_id == None))  # noqa: E711
        unassigned_devs = unassigned_res.scalars().all()
        for d in unassigned_devs:
            d.speed_limit = new_unassigned_limit
        await db.commit()

        # Resync unassigned device queues on RouterOS
        try:
            client = await router_manager.get_client(session=db)
            if client:
                tc = TrafficController(client)
                for d in unassigned_devs:
                    await tc.sync_device_queue(d.id, db)
        except Exception as e:
            logger.debug(f"Failed to sync unassigned device queues: {e}")

    # Dynamically reconfigure live Telegram bot service if updated
    try:
        from backend.app.api.v1.endpoints.telegram import telegram_bot_service
        if telegram_bot_service:
            token = payload.get("telegram_bot_token")
            admin_ids_str = payload.get("telegram_admin_ids")
            mode = payload.get("telegram_mode")
            webhook_url = payload.get("telegram_webhook_url")

            admin_ids = []
            if admin_ids_str:
                for p in admin_ids_str.split(","):
                    p = p.strip()
                    if p.isdigit() or (p.startswith("-") and p[1:].isdigit()):
                        admin_ids.append(int(p))

            await telegram_bot_service.reconfigure(
                token=token,
                admin_ids=admin_ids if admin_ids_str is not None else None,
                mode=mode,
                webhook_url=webhook_url
            )
    except Exception as e:
        logger.debug(f"Failed to dynamically reconfigure Telegram bot: {e}")

    return APIResponse(data=True, message="Settings saved successfully")
