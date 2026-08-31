import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.v1.endpoints.telegram import set_telegram_service
from backend.app.api.v1.endpoints.ws import router as ws_router
from backend.app.api.v1.router import api_v1_router
from backend.app.core.config import settings
from backend.app.db.session import AsyncSessionLocal, init_db
from backend.app.services.device_manager import DeviceManager
from backend.app.services.router_manager import NoRouterConfiguredError, router_manager
from backend.app.services.telegram_bot import TelegramBotService

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("mikroman.main")

telegram_service: TelegramBotService = None
bg_sync_task: asyncio.Task = None


async def check_quota_thresholds(session, router_id, tg_service) -> None:
    """Alert once per billing cycle for each quota threshold that has been passed.

    Runs on the background tick so a warning does not depend on someone having
    the dashboard open.
    """
    from backend.app.api.v1.endpoints.analytics import build_quota_status
    from backend.app.db.models import AlertLog
    from backend.app.services.quota import crossed_thresholds, get_quota_config, mark_fired
    from backend.app.utils_format import format_bytes_human

    config = await get_quota_config(session)
    if not config.limit_bytes:
        return

    status = await build_quota_status(session, router_id)
    newly = crossed_thresholds(
        used_bytes=status.used_bytes,
        limit_bytes=config.limit_bytes,
        thresholds=config.thresholds,
        already_fired=status.thresholds_reached,
    )
    for threshold in newly:
        message = (
            f"\U0001F4CA <b>ISP quota {threshold}% reached</b>\n"
            f"Used <b>{format_bytes_human(status.used_bytes)}</b> of "
            f"{format_bytes_human(config.limit_bytes)} "
            f"({status.used_pct:.1f}%)\n"
            f"Cycle ends {status.cycle_end} \u00b7 {status.days_remaining} day(s) left\n"
            f"Remaining budget: {format_bytes_human(status.remaining_bytes)} "
            f"(~{format_bytes_human(status.projected_daily_budget)}/day)"
        )
        session.add(AlertLog(
            router_id=router_id,
            alert_type="quota_threshold",
            message=(
                f"ISP quota {threshold}% reached: "
                f"{format_bytes_human(status.used_bytes)} of {format_bytes_human(config.limit_bytes)}"
            ),
            metadata_payload={"threshold": threshold, "used_bytes": status.used_bytes},
        ))
        await session.commit()
        await mark_fired(session, status.cycle_start, threshold)
        logger.info(f"ISP quota threshold {threshold}% reached for router {router_id}")

        if config.notify_telegram and tg_service:
            await tg_service.send_alert_to_admins(message, parse_mode="HTML")


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

                                # Collapse the rows left behind when a device
                                # rotated its private MAC more than once - an
                                # access-point change can produce several in a
                                # row, and discovery-time adoption only handles
                                # the single-prior-record case.
                                try:
                                    await dev_mgr.consolidate_rotated_devices(session)
                                except Exception as ce:
                                    logger.debug(f"Rotation consolidation tick error for router {r.id}: {ce}")

                            # Maintain RouterOS Simple Queues and FastTrack exemptions for all active users & unassigned devices
                            try:
                                from backend.app.db.models import Device, User
                                from backend.app.services.traffic_controller import TrafficController
                                tc = TrafficController(client)
                                from sqlalchemy import select

                                # Before shaping anything, make sure the stored
                                # intent is sane: a device that has an owner must
                                # not still be carrying the quarantine limit, or
                                # the sync below would faithfully re-apply it.
                                await tc.reconcile_device_limits(session)

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

                                # Remove managed queues whose owning user or device
                                # is gone, or that no longer needs its own queue.
                                # Runs after the syncs so freshly created queues
                                # are already accounted for.
                                await tc.reconcile_managed_queues(session)
                            except Exception as qe:
                                logger.debug(f"Queue sync tick error for router {r.id}: {qe}")

                            # Router uptime, read once for this tick. If it has
                            # gone backwards since the last tick the router
                            # rebooted and every byte counter on it reset to
                            # zero; the accounting passes below need to know
                            # that so they credit the bytes since the reboot
                            # rather than a bogus delta. A network outage on its
                            # own is not a reboot - the counters keep running.
                            router_uptime_s = None
                            try:
                                from backend.app.services.routeros import parse_uptime_seconds
                                _res = await client.get_system_resource()
                                router_uptime_s = parse_uptime_seconds(_res.uptime)
                            except Exception as ue:
                                logger.debug(f"Could not read uptime for router {r.id}: {ue}")

                            # Collect hardware and interface time-series metrics
                            try:
                                from backend.app.services.metrics_collector import metrics_collector
                                await metrics_collector.collect_and_store(session, r.id, client)
                            except Exception as me:
                                logger.debug(f"Metrics collection tick error for router {r.id}: {me}")

                            # Record gateway-level rollups from WAN interface counters
                            try:
                                from backend.app.services.analytics_engine import AnalyticsEngine
                                await AnalyticsEngine.record_traffic_snapshot(
                                    session, r.id, client, router_uptime_seconds=router_uptime_s
                                )
                            except Exception as te:
                                logger.warning(f"Gateway rollup tick error for router {r.id}: {te}")

                            # Quota thresholds for the ISP billing cycle. Checked
                            # here rather than on request so an alert fires even
                            # with no browser open.
                            try:
                                await check_quota_thresholds(session, r.id, telegram_service)
                            except Exception as qe:
                                logger.warning(f"Quota threshold check error for router {r.id}: {qe}")

                            # Per-device accounting via firewall mangle counters.
                            # Simple Queue byte counters are unreliable on RouterOS 7.x
                            # (measured frozen at zero while traffic flowed), so device
                            # and user volume is measured in the firewall forward chain.
                            try:
                                from backend.app.services.traffic_accounting import TrafficAccountingService
                                acct = TrafficAccountingService(client, router_id=r.id)
                                # collect() first: it reads the final counter of
                                # any device that has just gone inactive before
                                # sync_counter_rules() prunes that device's rule,
                                # so the last interval of its traffic is not lost.
                                await acct.collect(session, router_uptime_seconds=router_uptime_s)
                                await acct.sync_counter_rules(session)
                            except Exception as ae:
                                logger.warning(f"Traffic accounting tick error for router {r.id}: {ae}")

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

@app.exception_handler(NoRouterConfiguredError)
async def no_router_configured_handler(request: Request, exc: NoRouterConfiguredError):
    """Answer "no router set up yet" as a plain 503 rather than a 500.

    This is an expected state on a fresh install, not a fault: the setup wizard
    has simply not been completed. Previously these requests built a client from
    the environment defaults and authenticated as `admin` with an empty
    password, so a first run announced itself in the router's log as a series of
    failed logins.
    """
    return JSONResponse(
        status_code=503,
        content={"success": False, "message": str(exc), "data": None},
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
