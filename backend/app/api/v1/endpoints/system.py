import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.db.models import AlertLog, AppSetting
from backend.app.db.session import get_db
from backend.app.schemas.common import AlertLogDTO, APIResponse
from backend.app.schemas.routeros import InterfaceDTO, RouterSystemHealth, RouterSystemResource
from backend.app.services.hardware import resolve_cpu_identity
from backend.app.services.ip_lookup import (
    BUILTIN_SERVICES,
    IpLookupConfig,
    TemplateError,
    all_services,
)
from backend.app.services.ip_lookup import get_config as get_ip_lookup_config
from backend.app.services.ip_lookup import save_config as save_ip_lookup_config
from backend.app.services.router_manager import router_manager
from backend.app.services.routeros import RouterOSClient
from backend.app.services.routeros_compat import (
    MINIMUM_VERSION,
    VERIFIED_VERSION,
    check_version,
    format_version,
)

logger = logging.getLogger("mikroman.system")

router = APIRouter(prefix="/system", tags=["System & Settings"])


async def get_router_client(db: AsyncSession = Depends(get_db)) -> RouterOSClient:
    return await router_manager.require_client(session=db)


@router.get("/status", response_model=APIResponse[Dict[str, Any]])
async def get_system_status(router_client: RouterOSClient = Depends(get_router_client)):
    """Fetch live RouterOS system status, CPU, memory, health, and reachability.

    Reachability is established by the resource read itself rather than by a
    separate probe: if ``/system/resource`` answers, the REST API is up, and one
    fewer request reaches the router per call. ``get_system_health`` already
    swallows its own errors, since not every board has sensors.
    """
    try:
        resource: RouterSystemResource = await router_client.get_system_resource()
    except Exception as e:
        logger.warning(f"RouterOS status probe failed: {e}")
        return APIResponse(
            success=False,
            message="Cannot reach RouterOS REST API",
            data={"connected": False, "error": str(e)}
        )

    health: RouterSystemHealth = await router_client.get_system_health()
    # Static hardware identity - model, serial, and the SoC name that stands in
    # for a CPU part number on MikroTik hardware. Cached in the client, so this
    # is a real request only on the first status call after a (re)connect.
    board = await router_client.get_routerboard()

    # Advisory only: the router answered, so it is usable. This tells the
    # operator which features their RouterOS version cannot provide, instead of
    # leaving them to wonder why a panel is empty.
    compat = check_version(resource.version)

    # RouterOS only ever reports the SoC *family* on RouterBOARD hardware, so the
    # exact part number is looked up from the product code where it is published.
    cpu = resolve_cpu_identity(
        product_code=board.model,
        board_name=resource.board_name,
        firmware_type=board.firmware_type,
        resource_cpu=resource.cpu,
        architecture=resource.architecture_name,
    )

    return APIResponse(
        data={
            "connected": True,
            "resource": resource.model_dump(),
            "routerboard": board.model_dump(),
            "cpu_model": cpu.model,
            # False means the value above is the bootloader platform family, not
            # a part number; the UI says so rather than implying precision.
            "cpu_model_exact": cpu.exact,
            "cpu_platform": cpu.platform,
            "health": health.model_dump(),
            "app_version": settings.APP_VERSION,
            "routeros_compat": {
                "version": compat.version_text,
                "supported": compat.supported,
                "minimum": format_version(MINIMUM_VERSION),
                "verified_up_to": format_version(VERIFIED_VERSION),
                "degraded": compat.degraded,
                "warnings": compat.warnings,
            },
        }
    )


@router.get("/interfaces", response_model=APIResponse[List[InterfaceDTO]])
async def get_interfaces(router_client: RouterOSClient = Depends(get_router_client)):
    """Fetch list of network interfaces and counters.

    An unreachable router is a normal state, not a server error. This returned
    500 whenever the router was off the network, which is precisely when the
    operator is most likely to be in Settings trying to fix the connection - and
    a failed request there looks like the app itself is broken.
    """
    try:
        interfaces = await router_client.get_interfaces()
    except Exception as e:
        logger.warning(f"Could not read interfaces from the router: {e}")
        return APIResponse(
            data=[],
            message="Router unreachable - interface list unavailable",
        )
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
async def get_settings(
    router_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """Fetch stored application settings."""
    eff_router_id = router_id
    if eff_router_id is None:
        active_r = await router_manager.get_active_router(db)
        if active_r:
            eff_router_id = active_r.id

    result = await db.execute(select(AppSetting))
    settings_list = result.scalars().all()
    data = {s.key: s.value for s in settings_list}

    # Overlay router-scoped values for router-specific settings
    router_specific_keys = [
        "unassigned_device_speed_limit",
        "temp_warning_threshold",
        "auto_scan_enabled",
        "pause_allowed_networks",
        "isp_download_speed",
        "isp_upload_speed",
        "monitored_wan_interfaces",
    ]
    if eff_router_id is not None:
        for rk in router_specific_keys:
            scoped_key = f"{rk}_{eff_router_id}"
            if scoped_key in data:
                data[rk] = data[scoped_key]
            elif eff_router_id not in (1, None):
                # Distinct secondary router: do not inherit router 1's settings
                data.pop(rk, None)

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
        # Only when credentials were actually configured. Serving the bare
        # default put "admin" into the connection form, which then probed the
        # router with a username the operator never supplied.
        data["router_user"] = settings.ROUTEROS_USER if settings.ROUTEROS_PASSWORD else ""
    if "unassigned_device_speed_limit" not in data:
        data["unassigned_device_speed_limit"] = "5M/5M"
    if "temp_warning_threshold" not in data:
        data["temp_warning_threshold"] = "80"
    if "auto_scan_enabled" not in data:
        data["auto_scan_enabled"] = "true"
    if "pause_allowed_networks" not in data:
        data["pause_allowed_networks"] = "192.168.0.0/16, 10.0.0.0/8, 172.16.0.0/12"
    if "monitored_wan_interfaces" not in data:
        data["monitored_wan_interfaces"] = ""

    return APIResponse(data=data)


@router.post("/settings", response_model=APIResponse[bool])
async def save_settings(
    payload: Dict[str, str],
    router_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """Save application settings."""
    eff_router_id = router_id
    if eff_router_id is None:
        active_r = await router_manager.get_active_router(db)
        if active_r:
            eff_router_id = active_r.id

    router_specific_keys = [
        "unassigned_device_speed_limit",
        "temp_warning_threshold",
        "auto_scan_enabled",
        "pause_allowed_networks",
        "isp_download_speed",
        "isp_upload_speed",
        "monitored_wan_interfaces",
    ]

    for k, v in payload.items():
        setting = await db.get(AppSetting, k)
        if setting:
            setting.value = v
        else:
            setting = AppSetting(key=k, value=v)
            db.add(setting)

        # If it's a router-specific setting and eff_router_id is known, also write the scoped setting
        if k in router_specific_keys and eff_router_id is not None:
            scoped_key = f"{k}_{eff_router_id}"
            scoped_setting = await db.get(AppSetting, scoped_key)
            if scoped_setting:
                scoped_setting.value = v
            else:
                db.add(AppSetting(key=scoped_key, value=v))

    await db.commit()

    # If unassigned_device_speed_limit changed, update unassigned devices for this router
    if "unassigned_device_speed_limit" in payload:
        new_unassigned_limit = payload["unassigned_device_speed_limit"]
        from backend.app.db.models import Device
        from backend.app.services.traffic_controller import TrafficController
        dev_query = select(Device).where(Device.user_id == None)  # noqa: E711
        if eff_router_id is not None:
            dev_query = dev_query.where((Device.router_id == eff_router_id) | (Device.router_id.is_(None)))
        unassigned_res = await db.execute(dev_query)
        unassigned_devs = unassigned_res.scalars().all()
        for d in unassigned_devs:
            d.speed_limit = new_unassigned_limit
        await db.commit()

        # Resync unassigned device queues on RouterOS
        try:
            client = await router_manager.get_client(eff_router_id, session=db)
            if client:
                tc = TrafficController(client, router_id=eff_router_id)
                for d in unassigned_devs:
                    await tc.sync_device_queue(d.id, db)
        except Exception as e:
            logger.debug(f"Failed to sync unassigned device queues: {e}")

    # If pause_allowed_networks changed, resync RouterOS firewall rules
    if "pause_allowed_networks" in payload:
        try:
            client = await router_manager.get_client(eff_router_id, session=db)
            if client:
                from backend.app.services.traffic_controller import TrafficController
                tc = TrafficController(client, router_id=eff_router_id)
                await tc.ensure_pause_firewall_rules(db)
        except Exception as e:
            logger.debug(f"Failed to sync pause firewall rules: {e}")

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


@router.get("/ip-lookup", response_model=APIResponse[Dict[str, Any]])
async def get_ip_lookup(db: AsyncSession = Depends(get_db)):
    """Configured external IP-lookup services for the WAN tile.

    Returns the whole catalogue - built-ins plus the user's own entries - so the
    settings form can render it without a second round trip, along with which
    are enabled and which one a plain click follows.
    """
    config = await get_ip_lookup_config(db)
    return APIResponse(data={
        "services": [s.model_dump() for s in all_services(config)],
        "enabled_ids": config.enabled_ids,
        "default_id": config.default_id,
        "builtin_ids": [s.id for s in BUILTIN_SERVICES],
    })


@router.post("/ip-lookup", response_model=APIResponse[Dict[str, Any]])
async def save_ip_lookup(payload: IpLookupConfig, db: AsyncSession = Depends(get_db)):
    """Store the lookup-service selection and any custom URL templates.

    A rejected template is a user error, not a server fault, so it comes back as
    a 400 carrying the specific reason rather than a generic failure.
    """
    try:
        resolved = await save_ip_lookup_config(db, payload)
    except TemplateError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return APIResponse(data={
        "services": [s.model_dump() for s in all_services(resolved)],
        "enabled_ids": resolved.enabled_ids,
        "default_id": resolved.default_id,
        "builtin_ids": [s.id for s in BUILTIN_SERVICES],
    }, message="IP lookup services saved")
