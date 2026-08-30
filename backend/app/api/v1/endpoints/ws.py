import asyncio
import json
import logging
import time
from typing import Optional, Set

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from backend.app.core.config import settings
from backend.app.db.models import AppSetting
from backend.app.db.session import AsyncSessionLocal
from backend.app.services.router_manager import router_manager
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
    return clock


async def _get_wan_ip(client, router_id: Optional[int], monitored: list) -> Optional[str]:
    """Public-facing address of the monitored uplink, cached briefly.

    Falls back to any non-loopback address when no monitored interface matches,
    so the dashboard still shows something useful on unusual topologies.
    """
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
                    traffic_ctrl = TrafficController(client)
                    res = await client.get_system_resource()
                    health = await client.get_system_health()
                    users_stats = await traffic_ctrl.get_realtime_traffic_stats(session)

                    # Determine effective router ID
                    eff_router_id = router_id
                    if not eff_router_id:
                        active_r = await router_manager.get_default_or_first_router(session)
                        if active_r:
                            eff_router_id = active_r.id

                    # Read configured monitored interfaces for this router
                    setting_key = f"monitored_interfaces_{eff_router_id}" if eff_router_id else "monitored_interfaces_default"
                    setting = await session.get(AppSetting, setting_key)
                    monitored_ifaces = []
                    if setting and setting.value:
                        try:
                            monitored_ifaces = json.loads(setting.value)
                        except Exception:
                            monitored_ifaces = []

                    # If interfaces are selected, monitor their live traffic rates
                    ifaces_rates = []
                    if monitored_ifaces and len(monitored_ifaces) > 0:
                        ifaces_rates = await client.monitor_interface_traffic(monitored_ifaces)
                        total_rx = sum(r.get("rx_bits_per_second", 0.0) for r in ifaces_rates)
                        total_tx = sum(r.get("tx_bits_per_second", 0.0) for r in ifaces_rates)
                    elif setting is not None and len(monitored_ifaces) == 0:
                        # User explicitly deselected all interfaces -> 0 bps
                        total_rx = 0.0
                        total_tx = 0.0
                    else:
                        # Default fallback: monitor running physical/WAN interfaces or sum users
                        all_ifaces = await client.get_interfaces()
                        running_ifaces = [i.name for i in all_ifaces if i.running and not i.disabled]
                        default_pick = [n for n in running_ifaces if "ether1" in n or "wan" in n or "bridge" in n or "sfp" in n]
                        if not default_pick and running_ifaces:
                            default_pick = running_ifaces[:2]
                        if default_pick:
                            monitored_ifaces = default_pick
                            ifaces_rates = await client.monitor_interface_traffic(default_pick)
                            total_rx = sum(r.get("rx_bits_per_second", 0.0) for r in ifaces_rates)
                            total_tx = sum(r.get("tx_bits_per_second", 0.0) for r in ifaces_rates)
                        else:
                            total_rx = sum(u["current_rate_in"] for u in users_stats)
                            total_tx = sum(u["current_rate_out"] for u in users_stats)

                payload = {
                    "type": "telemetry_tick",
                    "timestamp": time.time(),
                    "router": {
                        "board_name": res.board_name,
                        "version": res.version,
                        "cpu_load": res.cpu_load,
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
                        # Devices currently online across all profiles - answers
                        # "how many clients is this router actually serving".
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

