import asyncio
import json
import logging
import time
from typing import Optional, Set

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from backend.app.core.config import settings
from backend.app.db.models import AppSetting, Router
from backend.app.db.session import AsyncSessionLocal
from backend.app.services.hardware import resolve_cpu_identity
from backend.app.services.public_network import public_network_resolver
from backend.app.services.router_manager import router_manager
from backend.app.services.router_time import store_router_offset
from backend.app.services.traffic_controller import TrafficController

logger = logging.getLogger("mikroman.ws")
router = APIRouter(tags=["WebSocket Telemetry"])

# The WAN address changes rarely but the telemetry loop ticks once a second, so
# it is cached rather than queried on every frame.
_WAN_IP_TTL_SECONDS = 60
_wan_ip_cache: dict = {}


async def _telemetry_interval(session) -> float:
    """Seconds between telemetry frames, configurable at runtime.

    Each frame costs several RouterOS REST calls, and on modest hardware that
    polling measurably raises router CPU, so the rate is a setting rather than a
    constant. Bounded to keep a mistyped value from hammering the router.
    """
    setting = await session.get(AppSetting, "telemetry_interval_seconds")
    if setting and setting.value:
        try:
            return max(1.0, min(float(setting.value), 60.0))
        except ValueError:
            pass
    return settings.TELEMETRY_STREAM_INTERVAL_SECONDS


_CLOCK_TTL_SECONDS = 60
_clock_cache: dict = {}


async def _get_router_clock(client, router_id: Optional[int]) -> dict:
    """Router timezone and offset, cached.

    Only the offset is needed: the browser advances the clock from it, so a live
    router time costs one request a minute rather than one per frame.
    """
    key = str(router_id)
    cached = _clock_cache.get(key)
    now = time.time()
    if cached and (now - cached["at"]) < _CLOCK_TTL_SECONDS:
        return cached["clock"]
    try:
        clock = await client.get_system_clock()
    except Exception as e:
        logger.debug(f"Could not read router clock: {e}")
        return cached["clock"] if cached else {}
    _clock_cache[key] = {"clock": clock, "at": now}
    # Persist the offset so daily rollup boundaries follow the router's calendar
    # even before the first telemetry frame of a fresh process.
    try:
        async with AsyncSessionLocal() as session:
            await store_router_offset(session, clock.get("gmt_offset_minutes"))
    except Exception as e:
        logger.debug(f"Could not persist router UTC offset: {e}")
    return clock


async def _get_wan_ip(client, router_id: Optional[int], monitored: list) -> Optional[str]:
    """Public-facing address of the monitored uplink, cached briefly.

    Returns ``None`` when no WAN interface has been chosen for this router - the
    tile then reads "-" rather than a guessed LAN address, matching the loud
    "no WAN selected" state on the bandwidth tiles. When a WAN *is* chosen but
    no address sits on it, falls back to any non-loopback address so an unusual
    topology still shows something.
    """
    if not monitored:
        return None

    cache_key = str(router_id)
    cached = _wan_ip_cache.get(cache_key)
    now = time.time()
    if cached and (now - cached["at"]) < _WAN_IP_TTL_SECONDS:
        return cached["ip"]

    try:
        addresses = await client.get_ip_addresses()
    except Exception as e:
        logger.debug(f"Could not read IP addresses for WAN display: {e}")
        return cached["ip"] if cached else None

    preferred = None
    fallback = None
    for entry in addresses:
        raw = entry.get("address") or ""
        ip = raw.split("/")[0]
        iface = entry.get("interface")
        if not ip or ip.startswith("127."):
            continue
        if monitored and iface in monitored:
            preferred = ip
            break
        if fallback is None:
            fallback = ip

    resolved = preferred or fallback
    _wan_ip_cache[cache_key] = {"ip": resolved, "at": now}
    return resolved


class ConnectionManager:
    """Manages active browser WebSocket connections."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"WebSocket client connected. Total active: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        logger.info(f"WebSocket client disconnected. Total active: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        dead_connections = set()
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.add(connection)
        for dead in dead_connections:
            self.active_connections.discard(dead)


ws_manager = ConnectionManager()


@router.websocket("/ws/telemetry")
async def websocket_telemetry_endpoint(
    websocket: WebSocket,
    router_id: Optional[int] = Query(None)
):
    """WebSocket endpoint pushing real-time router resource and user bandwidth telemetry every 1s."""
    await ws_manager.connect(websocket)
    # Seeded so a failure before the per-tick lookup still paces the retry.
    tick_interval = settings.TELEMETRY_STREAM_INTERVAL_SECONDS

    try:
        while True:
            try:
                async with AsyncSessionLocal() as session:
                    client = await router_manager.get_client(router_id, session=session)
                    if not client:
                        await websocket.send_json({
                            "type": "telemetry_waiting",
                            "message": "No active router configured",
                            "timestamp": time.time()
                        })
                        await asyncio.sleep(await _telemetry_interval(session))
                        continue

                    tick_interval = await _telemetry_interval(session)

                    # Resolve the router this frame is for *before* reading any
                    # per-user data, so the stats query can be scoped to it.
                    # Without this the user list, the client counts and the
                    # bandwidth totals were summed across every managed router.
                    eff_router_id = router_id
                    if not eff_router_id:
                        active_r = await router_manager.get_default_or_first_router(session)
                        if active_r:
                            eff_router_id = active_r.id

                    traffic_ctrl = TrafficController(client, router_id=eff_router_id)
                    res = await client.get_system_resource()
                    health = await client.get_system_health()
                    # Cached after the first tick; the SoC name and core count
                    # do not change without a reboot.
                    board = await client.get_routerboard()

                    # Backfill the RouterBoard serial onto rows added before it
                    # was stored, so archive -> re-add can match this box later.
                    if eff_router_id and board and board.serial_number:
                        row = await session.get(Router, eff_router_id)
                        if row is not None and not row.serial_number:
                            row.serial_number = board.serial_number
                            await session.commit()

                    users_stats = await traffic_ctrl.get_realtime_traffic_stats(
                        session, router_id=eff_router_id
                    )

                    # Read configured monitored interfaces for this router
                    setting_key = f"monitored_interfaces_{eff_router_id}" if eff_router_id else "monitored_interfaces_default"
                    setting = await session.get(AppSetting, setting_key)
                    monitored_ifaces = []
                    if setting and setting.value:
                        try:
                            monitored_ifaces = json.loads(setting.value)
                        except Exception:
                            monitored_ifaces = []

                    # The monitored set is exactly what the admin ticked in the
                    # WAN selector - never guessed. With nothing selected there
                    # is no WAN to measure: report an empty set and zero rate so
                    # the bandwidth tiles render their loud "no WAN selected"
                    # state instead of silently graphing an interface the
                    # operator never chose.
                    ifaces_rates = []
                    if monitored_ifaces:
                        ifaces_rates = await client.monitor_interface_traffic(monitored_ifaces)
                        total_rx = sum(r.get("rx_bits_per_second", 0.0) for r in ifaces_rates)
                        total_tx = sum(r.get("tx_bits_per_second", 0.0) for r in ifaces_rates)
                    else:
                        total_rx = 0.0
                        total_tx = 0.0

                # Public identity is per router: ask this router for its own
                # public address (/ip/cloud, kept current by RouterOS DDNS) and
                # let the resolver look the operator up for *that* address. It
                # falls back to a container-side echo only when the router
                # cannot say. Cached for 15 min, so a provider-side upstream or
                # routing change is picked up on its own within the window.
                try:
                    cloud_ip = await client.get_cloud_public_address()
                except Exception:
                    cloud_ip = None
                public_net = await public_network_resolver.resolve(
                    router_id=eff_router_id, hint_ip=cloud_ip
                )
                cpu_identity = resolve_cpu_identity(
                    product_code=board.model,
                    board_name=res.board_name,
                    firmware_type=board.firmware_type,
                    resource_cpu=res.cpu,
                    architecture=res.architecture_name,
                )

                payload = {
                    "type": "telemetry_tick",
                    "timestamp": time.time(),
                    "router": {
                        "board_name": res.board_name,
                        "version": res.version,
                        "cpu_load": res.cpu_load,
                        # The real processor. RouterOS only reports the SoC
                        # *family* on MikroTik hardware (firmware-type, e.g.
                        # "ipq5300"), so the exact part is looked up from the
                        # product code where MikroTik publishes it.
                        "cpu_model": cpu_identity.model,
                        "cpu_model_exact": cpu_identity.exact,
                        "cpu_platform": cpu_identity.platform,
                        "cpu_arch": res.architecture_name or res.cpu,
                        "cpu_count": res.cpu_count,
                        "cpu_frequency_mhz": res.cpu_frequency,
                        "routerboard_model": board.model,
                        "routerboard_serial": board.serial_number,
                        "free_memory_mb": round(res.free_memory / (1024 * 1024), 1),
                        "total_memory_mb": round(res.total_memory / (1024 * 1024), 1),
                        "temperature": health.temperature,
                        "voltage": health.voltage,
                        "uptime": res.uptime,
                        "wan_rx_bps": total_rx,
                        "wan_tx_bps": total_tx,
                        "monitored_interfaces": monitored_ifaces,
                        "wan_ip": await _get_wan_ip(client, eff_router_id, monitored_ifaces),
                        "clock": await _get_router_clock(client, eff_router_id),
                        "public_ip": public_net.ip,
                        # Who the uplink belongs to on the internet. Useful on
                        # its own, and the only way to tell two links apart when
                        # both hand out carrier-grade NAT addresses.
                        "isp": public_net.isp,
                        "asn": public_net.asn,
                        # The client tile counts people and their hardware:
                        #  - user_count           how many profiles exist here
                        #  - client_device_count  devices assigned to those profiles
                        #  - active_clients       of those devices, how many are online now
                        "user_count": len(users_stats),
                        "client_device_count": sum(u.get("device_count", 0) for u in users_stats),
                        "active_clients": sum(u.get("active_device_count", 0) for u in users_stats),
                    },
                    "users": users_stats
                }
                await websocket.send_json(payload)
            except Exception as e:
                logger.debug(f"Telemetry stream tick error: {e}")
                await websocket.send_json({"type": "telemetry_error", "error": str(e), "timestamp": time.time()})

            await asyncio.sleep(tick_interval)
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket session error: {e}")
        ws_manager.disconnect(websocket)

