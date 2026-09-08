import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.v1.endpoints.telegram import set_telegram_service
from backend.app.api.v1.endpoints.ws import router as ws_router
from backend.app.api.v1.router import api_v1_router
from backend.app.core.config import settings
from backend.app.core.diagnostics import record
from backend.app.core.logging_config import configure_logging, log_file_error, log_file_path
from backend.app.core.tunables import (
    alert_new_device_enabled,
    heavy_sync_interval_seconds,
    poll_interval_seconds,
)
from backend.app.db.session import AsyncSessionLocal, encrypt_legacy_secrets, init_db
from backend.app.services.backup_scheduler import backup_scheduler
from backend.app.services.device_manager import DeviceManager
from backend.app.services.guards import WriteGuardViolation
from backend.app.services.router_manager import NoRouterConfiguredError, router_manager
from backend.app.services.telegram_bot import TelegramBotService

configure_logging()
logger = logging.getLogger("mikroman.main")

telegram_service: TelegramBotService = None
bg_sync_task: asyncio.Task = None
log_scrape_task: asyncio.Task = None


async def check_quota_thresholds(session, router_id, tg_service) -> None:
    """Alert once per billing cycle for each quota threshold that has been passed.

    Runs on the background tick so a warning does not depend on someone having
    the dashboard open.
    """
    from backend.app.api.v1.endpoints.analytics import build_quota_status
    from backend.app.db.models import AlertLog
    from backend.app.services.quota import crossed_thresholds, get_quota_config, mark_fired
    from backend.app.utils_format import format_bytes_human

    config = await get_quota_config(session, router_id=router_id)
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
        await mark_fired(session, status.cycle_start, threshold, router_id=router_id)
        logger.info(f"ISP quota threshold {threshold}% reached for router {router_id}")

        if config.notify_telegram and tg_service:
            await tg_service.send_alert_to_admins(message, parse_mode="HTML")


async def _backfill_interface_rollups_once():
    """Rebuild the full retention window of per-interface / gateway rollups from
    the samples, once, at startup.

    The per-tick recompute only reaches a few days back, so a version that
    misfiled older days - or simply days recorded before this table existed -
    would never be corrected without this. Cheap enough to do inline: it reads
    at most 30 days of samples per router and rewrites a few hundred rows.
    """
    try:
        from backend.app.services.interface_rollups import recompute_interface_rollups
        async with AsyncSessionLocal() as session:
            for r in await router_manager.get_all_active_routers(session):
                try:
                    n = await recompute_interface_rollups(session, r.id)
                    if n:
                        logger.info(f"Backfilled interface rollups for router {r.id}: {n} day(s)")
                except Exception as e:
                    logger.warning(f"Interface rollup backfill failed for router {r.id}: {e}")
    except Exception as e:
        logger.warning(f"Interface rollup backfill skipped: {e}")


async def _reconcile_queues(client, session, router_id: int) -> None:
    """Make the router's Simple Queues match what the database says they should be.

    Reads the whole queue table, the address lists and the fasttrack rules, then
    adds, changes or removes what drifted. Costs a dozen or more REST calls on a
    router with several users, which is why it is on the heavy clock rather than
    on every telemetry tick - and why an action taken in the UI does not wait for
    it, because those endpoints call the same functions inline.
    """
    try:
        from sqlalchemy import select

        from backend.app.db.models import Device, User
        from backend.app.services.traffic_controller import TrafficController

        tc = TrafficController(client, router_id=router_id)

        # Before shaping anything, make sure the stored intent is sane: a device
        # that has an owner must not still be carrying the quarantine limit, or
        # the sync below would faithfully re-apply it.
        await tc.reconcile_device_limits(session, router_id=router_id)

        users_res = await session.execute(
            select(User).where((User.router_id == router_id) | (User.router_id.is_(None)))
        )
        for u in users_res.scalars().all():
            active_ips = [
                d.ip_address for d in u.devices
                if d.is_active and d.ip_address and (d.router_id == router_id or d.router_id is None)
            ]
            try:
                await tc.sync_user_queue(u.id, u.name, active_ips, u.speed_limit)
            except WriteGuardViolation as e:
                logger.warning(f"Skipped queue sync due to WriteGuard: {e}")
            except Exception as e:
                logger.debug(f"User queue sync error: {e}")

        # Sync unassigned quarantine devices and custom device queues for this router
        devs_res = await session.execute(
            select(Device).where(
                Device.is_active,
                (Device.router_id == router_id) | (Device.router_id.is_(None)),
                Device.user_id.is_(None) | (Device.speed_limit != "default")
            )
        )
        for dev in devs_res.scalars().all():
            try:
                await tc.sync_device_queue(dev.id, session)
            except WriteGuardViolation as e:
                logger.warning(f"Skipped queue sync due to WriteGuard: {e}")
            except Exception as e:
                logger.debug(f"Device queue sync error: {e}")

        # Remove managed queues whose owning user or device is gone, or that no
        # longer needs its own queue. Runs after the syncs so freshly created
        # queues are already accounted for.
        await tc.reconcile_managed_queues(session, router_id=router_id)
    except Exception as qe:
        logger.debug(f"Queue sync tick error for router {router_id}: {qe}")


async def _reconcile_traffic_state(client, session, router, router_uptime_s) -> None:
    """Rollups, quota alerts and per-device accounting for one router.

    The three write the counters back into SQLite and read the firewall mangle
    tables, so together they were the bulk of the background traffic. Each keeps
    its own error handling: a router that refuses one read should not stop the
    others, and none of them invalidates the tick as a whole (which is why
    failures here are logged but do not open the sync-fault bookkeeping).
    """
    # Rebuild the recent gateway / per-interface rollups from the samples the
    # telemetry half just wrote. Deriving them from interface_metrics (rather
    # than a live counter delta) attributes each byte to the day it moved and
    # survives a restart.
    try:
        from backend.app.services.interface_rollups import recompute_recent
        await recompute_recent(session, router.id)
    except Exception as te:
        logger.warning(f"Interface rollup tick error for router {router.id}: {te}")

    # Quota thresholds for the ISP billing cycle. Checked here rather than on
    # request so an alert fires even with no browser open.
    try:
        await check_quota_thresholds(session, router.id, telegram_service)
    except Exception as qe:
        logger.warning(f"Quota threshold check error for router {router.id}: {qe}")

    # Per-device accounting via firewall mangle counters. Simple Queue byte
    # counters are unreliable on RouterOS 7.x (measured frozen at zero while
    # traffic flowed), so device and user volume is measured in the firewall
    # forward chain.
    try:
        from backend.app.services.traffic_accounting import TrafficAccountingService

        acct = TrafficAccountingService(client, router_id=router.id)
        # collect() first: it reads the final counter of any device that has just
        # gone inactive before sync_counter_rules() prunes that device's rule, so
        # the last interval of its traffic is not lost.
        await acct.collect(session, router_uptime_seconds=router_uptime_s)
        await acct.sync_counter_rules(session)
    except Exception as ae:
        logger.warning(f"Traffic accounting tick error for router {router.id}: {ae}")


def _heavy_deadline(started: float, interval: float, index: int, count: int) -> float:
    """The monotonic instant at which router ``index`` first becomes due.

    Spread across the interval instead of starting every router at once. With
    three routers and no spread, the first tick after start runs all three
    expensive halves back to back - a burst of dozens of REST calls against
    three devices and, worse, three long SQLite write transactions in a row,
    which is exactly the shape that trips the 5-second ``busy_timeout``. Only
    the first router is due immediately; the rest join in over one interval and
    keep their own cadence after that.
    """
    if count <= 1:
        return started
    return started + interval * index / count


async def _sync_one_router(r, *, auto_scan_enabled: bool, heavy_due: bool, sync_failures: dict) -> None:
    """One router's slice of a background tick, in its own database session.

    The fleet used to be served by a single sequential loop over one session, so
    every router sampled at the speed of the slowest member. Measured on the
    router-hosted container with three routers - one on LAN, two across the
    Internet with a 1.0-second TLS handshake each and periodic connect timeouts
    that open the circuit breaker for 15 s - the *local* router was getting one
    sample every ~100 seconds on a loop configured for one every 10, and the
    third router one every fifteen minutes. The graphs then looked like holes in
    the data, which is a data-density bug wearing the costume of a link problem.

    Per-router tasks make each router's cadence its own: a router that is slow,
    down, or in cooldown delays nobody but itself.
    """
    new_devices = []
    failed = False
    async with AsyncSessionLocal() as session:
        try:
            client = await router_manager.get_client(r.id, session=session)
            if client:
                if heavy_due and auto_scan_enabled:
                    dev_mgr = DeviceManager(client, router_id=r.id)
                    _, new_devices = await dev_mgr.sync_devices_from_router(session)

                    # Collapse the rows left behind when a device rotated its
                    # private MAC more than once - an access-point change can
                    # produce several in a row, and discovery-time adoption only
                    # handles the single-prior-record case.
                    try:
                        await dev_mgr.consolidate_rotated_devices(session)
                    except Exception as ce:
                        logger.debug(f"Rotation consolidation tick error for router {r.id}: {ce}")

                if heavy_due:
                    # Maintain RouterOS Simple Queues and FastTrack exemptions
                    # for active users and unassigned devices of this router.
                    await _reconcile_queues(client, session, r.id)

                telemetry_started = time.monotonic()
                # Router uptime, read once for this tick. If it has gone
                # backwards since the last tick the router rebooted and every
                # byte counter on it reset to that, so they credit the bytes
                # since the reboot rather than a bogus delta. A network outage on
                # its own is not a reboot - the counters keep running.
                router_uptime_s = None
                router_resource = None
                try:
                    from backend.app.services.routeros import parse_uptime_seconds
                    router_resource = await client.get_system_resource()
                    router_uptime_s = parse_uptime_seconds(router_resource.uptime)
                except Exception as ue:
                    logger.debug(f"Could not read uptime for router {r.id}: {ue}")

                # Collect hardware and interface time-series metrics. The
                # resource read above is handed in rather than repeated: same
                # call, and at one per tick per router it was a third of
                # `/system/resource` traffic on its own.
                try:
                    from backend.app.services.metrics_collector import metrics_collector
                    hardware_alerts = await metrics_collector.collect_and_store(
                        session, r.id, client, resource=router_resource
                    )
                except Exception as me:
                    hardware_alerts = []
                    logger.debug(f"Metrics collection tick error for router {r.id}: {me}")
                record("sync.telemetry", time.monotonic() - telemetry_started)

                if heavy_due:
                    heavy_started = time.monotonic()
                    await _reconcile_traffic_state(client, session, r, router_uptime_s)
                    record("sync.heavy", time.monotonic() - heavy_started)

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

                    if telegram_service and await alert_new_device_enabled(session):
                        for dev in new_devices:
                            msg = (
                                f"🔔 <b>New Device Discovered on {r.name}!</b>\n"
                                f"• Host: <code>{dev.hostname or 'Unknown'}</code>\n"
                                f"• IP: <code>{dev.ip_address}</code>\n"
                                f"• MAC: <code>{dev.mac_address}</code>\n"
                                f"• Vendor: <code>{dev.vendor or 'Unknown'}</code>"
                            )
                            await telegram_service.send_alert_to_admins(msg, parse_mode="HTML")

                # The collector stored the alert row; the bot is the caller's
                # business, because importing it here would make a leaf module
                # depend on the application.
                if hardware_alerts and telegram_service:
                    for alert in hardware_alerts:
                        await telegram_service.send_alert_to_admins(
                            f"⚠️ <b>{r.name}</b>: {alert['message']}", parse_mode="HTML"
                        )
        except Exception as e:
            failed = True
            text = f"{type(e).__name__}: {e}"
            # One warning per distinct fault rather than one per tick: at debug
            # level (what this was) a router that stopped answering for hours
            # left no trace at all, and the outage only ever surfaced as a
            # strange gap in the graphs.
            if sync_failures.get(r.id) != text:
                sync_failures[r.id] = text
                logger.warning(f"Sync tick failing for router {r.name} ({r.id}): {text}")
        if not failed and sync_failures.pop(r.id, None) is not None:
            logger.info(f"Router {r.name} ({r.id}) is syncing again")


async def background_sync_worker():
    """Periodic background discovery and health monitor for all configured active routers.

    The tick has two halves on deliberately different clocks.

    * **Every ``POLL_INTERVAL_SECONDS``** (default 10 s): the hardware and
      bandwidth samples the graphs are drawn from, plus router uptime so a
      reboot does not read as a negative delta. Four REST calls per router.
    * **Every ``HEAVY_SYNC_INTERVAL_SECONDS``** (default 60 s): device discovery,
      simple-queue and mangle-counter reconciliation, rollup recompute, quota
      thresholds. Measured on the router-hosted container these were ~40% of all
      REST traffic against the devices (303 of 741 calls in a 320-second window)
      for state that on a home network changes about as often as once a minute.

    Nothing waits on this loop to take effect: pausing a device or changing a
    limit is applied by its own endpoint, which calls the same sync functions
    inline. Slowing the reconciliation back only widens how long a device can
    join without being noticed, from ten seconds to a minute.

    Routers are handled concurrently, each in its own session (see
    :func:`_sync_one_router`), and each carries its own heavy-pass deadline, so
    the expensive halves do not all land in the same tick and stack their SQLite
    write transactions on top of each other.
    """
    # Last sync fault per router, kept so a failure that repeats every tick is
    # logged once. Lives for the whole run of the worker, not a single tick.
    sync_failures = {}
    last_heavy: dict = {}
    await _backfill_interface_rollups_once()
    while True:
        started = time.monotonic()
        # Seeded with the environment value so a failure inside the tick below
        # still leaves a sane sleep interval: an unbound name here would raise
        # out of the worker and stop background collection entirely.
        poll_every = float(settings.POLL_INTERVAL_SECONDS)
        try:
            async with AsyncSessionLocal() as session:
                from backend.app.db.models import AppSetting
                auto_scan_sett = await session.get(AppSetting, "auto_scan_enabled")
                is_auto_scan_enabled = (auto_scan_sett.value.lower() != "false") if auto_scan_sett else True
                # Re-read both clocks every tick: the Settings dialog is the place
                # an operator adjusts load, and a change that needs a restart to
                # take effect is indistinguishable from one that was ignored.
                poll_every = await poll_interval_seconds(session)
                heavy_every = await heavy_sync_interval_seconds(session)
                active_routers = await router_manager.get_all_active_routers(session)

            passes = []
            for index, r in enumerate(active_routers):
                not_before = last_heavy.get(
                    r.id,
                    _heavy_deadline(started, heavy_every, index, len(active_routers)),
                )
                heavy_due = started >= not_before
                if heavy_due:
                    # Record the attempt, not the outcome: if the expensive half
                    # fails, it should be retried on the next heavy pass, not
                    # hammered every telemetry tick until it clears.
                    last_heavy[r.id] = started
                passes.append(_sync_one_router(
                    r, auto_scan_enabled=is_auto_scan_enabled, heavy_due=heavy_due,
                    sync_failures=sync_failures,
                ))
            if passes:
                await asyncio.gather(*passes)
        except Exception as e:
            logger.debug(f"Background sync tick error: {e}")

        await asyncio.sleep(poll_every)


LOG_SCRAPE_INTERVAL_SECONDS = 60.0
# ~6 hours at the interval above.
DESTINATION_PRUNE_EVERY_TICKS = 360
# How long per-device event rows are kept. They used to be kept forever, and
# this is the one table that can grow with nobody touching it: two hosts sharing
# one MAC made discovery "change" the IP and hostname on every sweep, 35 123
# rows in six days on a single device — all of which every device read paid for.
DEVICE_HISTORY_RETENTION_DAYS = 90


async def _trim_device_history_once():
    """Reclaim the device event log once, at start-up.

    The periodic pass is gated on a tick count, which on this loop works out at
    roughly eight hours — too late to matter to the operator watching memory climb
    now, and the churn rows are days old, so the 90-day age rule would not touch
    them at all. Same reasoning as the rollup backfill: a fix that only lands
    after the next restart-on-tick is not a fix for the machine that is already
    loaded.

    Runs after `init_db`, before the first telemetry tick, and never fails the
    start-up: a trim that cannot run just leaves the table as it was.
    """
    try:
        from backend.app.services.device_manager import cap_device_history, prune_device_history

        async with AsyncSessionLocal() as session:
            removed = await prune_device_history(session)
            removed += await cap_device_history(session)
            if removed:
                logger.info(f"Trimmed {removed} device history row(s) at start-up")
    except Exception as e:
        logger.warning(f"Device history trim skipped: {e}")


async def log_scrape_worker():
    """Pull each active router's log into SQLite, then prune what aged out.

    RouterOS keeps its log in a small memory ring - a busy box overwrites the
    oldest lines within minutes, so anything not copied out is gone. This loop
    is what makes `GET /api/v1/logs?source=db` able to answer for yesterday.

    Off by default is not an option worth having here (an empty history is
    indistinguishable from a quiet router), so it runs unless the operator
    turns `log_scraping_enabled` off in Settings.
    """
    from backend.app.db.models import AppSetting
    from backend.app.services.destination_collector import destination_collector
    from backend.app.services.log_collector import LogCollector
    from backend.app.services.security_audit import check_and_alert

    collector = LogCollector()
    ticks = 0
    while True:
        tick_started = time.monotonic()
        try:
            async with AsyncSessionLocal() as session:
                enabled = await session.get(AppSetting, "log_scraping_enabled")
                if enabled and str(enabled.value).strip().lower() in ("false", "0", "no", "off"):
                    await asyncio.sleep(LOG_SCRAPE_INTERVAL_SECONDS)
                    continue

                retention_setting = await session.get(AppSetting, "log_retention_days")
                try:
                    retention_days = int(retention_setting.value) if retention_setting else 14
                except (TypeError, ValueError):
                    retention_days = 14
                retention_days = max(1, min(retention_days, 365))

                for r in await router_manager.get_all_active_routers(session):
                    try:
                        client = await router_manager.get_client(r.id, session=session)
                        if not client:
                            continue
                        await collector.collect_logs_for_router(session, r.id, client)
                        await collector.prune_old_logs(session, r.id, retention_days=retention_days)
                    except Exception as e:
                        logger.debug(f"Log scrape failed for router {r.id}: {e}")

                    # Same tick, same conntrack read cadence: fold the live
                    # connections into the persistent per-destination history
                    # that the "Destinations & Domains" tab reads.
                    try:
                        await destination_collector.sample_router(session, r.id, client)
                    except Exception as e:
                        logger.debug(f"Destination sample failed for router {r.id}: {e}")

                    # Raise one alert a day while any management service still
                    # accepts connections from any source address.
                    try:
                        await check_and_alert(session, r.id, client)
                    except Exception as e:
                        logger.debug(f"Security audit failed for router {r.id}: {e}")

                ticks += 1
                if ticks % DESTINATION_PRUNE_EVERY_TICKS == 0:
                    try:
                        await destination_collector.prune(session)
                    except Exception as e:
                        logger.debug(f"Destination prune failed: {e}")
                    # Device history had no retention at all. The duplicated-MAC
                    # churn has a source-side fix now, but the rows already
                    # recorded on installed systems stay, and they are the rows
                    # every device page and analytics read pays for.
                    try:
                        from backend.app.services.device_manager import (
                            cap_device_history,
                            prune_device_history,
                        )

                        removed = await prune_device_history(
                            session, retention_days=DEVICE_HISTORY_RETENTION_DAYS
                        )
                        # Age alone would not shrink an installed database: the
                        # churn rows are days old, so a 90-day rule leaves them
                        # until spring. This is the pass that actually reclaims
                        # them, and the guard against a future writer bug.
                        removed += await cap_device_history(session)
                        if removed:
                            logger.info(
                                f"Trimmed {removed} device history row(s) "
                                f"(older than {DEVICE_HISTORY_RETENTION_DAYS} days, or beyond the "
                                f"newest per-device window)"
                            )
                    except Exception as e:
                        logger.debug(f"Device history prune failed: {e}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Log scrape worker tick failed: {e}")

        # The duration is the interesting part: a scrape that sleeps for 60 s and
        # then takes 30 s is collecting half as often as it appears to, and the
        # time is usually spent waiting on SQLite's write lock rather than on the
        # router.
        record("log_scrape.tick", time.monotonic() - tick_started)
        await asyncio.sleep(LOG_SCRAPE_INTERVAL_SECONDS)


async def _cancel_and_wait(*tasks) -> None:
    """Cancel the given tasks and wait for them to actually stop."""
    live = [t for t in tasks if t is not None]
    for task in live:
        task.cancel()
    if live:
        await asyncio.gather(*live, return_exceptions=True)


async def _drain(coro, label: str, timeout: float = 3.0) -> None:
    """Await one shutdown step within a deadline, never past it.

    RouterOS sends SIGTERM and then SIGKILL after the container's ``stop-time``
    (10 s by default). Shutting down without a budget is how the container ended
    up recorded as ``Exited (137)`` - killed rather than closed, with the last
    log lines lost. One step that hangs must not cost the others their turn: a
    dropped Telegram connection is recoverable, an unclean teardown is what makes
    the next start question SQLite's consistency.
    """
    try:
        await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"Shutdown step '{label}' did not finish within {timeout:.0f}s, moving on")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(f"Shutdown step '{label}' failed: {type(e).__name__}: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global telegram_service, bg_sync_task, log_scrape_task
    logger.info("Initializing MikroMan Database...")
    await init_db()
    # Router credentials are encrypted at rest; anything written by an older
    # build is still plain text on disk until it is rewritten once.
    await encrypt_legacy_secrets()
    # Reclaim the device event log before any worker loads it. The periodic pass
    # is hours away; the rows it targets are already there.
    await _trim_device_history_once()

    telegram_service = TelegramBotService(
        router_manager=router_manager,
        session_factory=AsyncSessionLocal
    )
    set_telegram_service(telegram_service)
    await telegram_service.start()

    bg_sync_task = asyncio.create_task(background_sync_worker())
    log_scrape_task = asyncio.create_task(log_scrape_worker())
    # Config-drift snapshots. The scheduler re-reads `backup_enabled` and
    # `backup_interval_hours` on every pass, so a change in Settings takes
    # effect without a restart.
    await backup_scheduler.start()
    log_path = log_file_path()
    if log_path:
        logger.info(f"Persistent app log: {log_path} (readable at GET /api/v1/logs?source=app)")
    else:
        logger.warning(f"No persistent app log ({log_file_error() or 'disabled'}); console only")
    logger.info("MikroMan Engine initialized successfully.")

    yield

    # Budget: 3 + 2 + 3 + 2 = 10 s of work at most, matching the container's
    # default stop-time. Steps that need no budget get none of it.
    await _drain(_cancel_and_wait(bg_sync_task, log_scrape_task), "background workers", timeout=3.0)
    await _drain(backup_scheduler.stop(), "backup scheduler", timeout=2.0)
    if telegram_service:
        await _drain(telegram_service.stop(), "telegram bot", timeout=3.0)
    await _drain(router_manager.aclose(), "router connections", timeout=2.0)
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
