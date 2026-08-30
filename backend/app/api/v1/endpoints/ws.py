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
                        await asyncio.sleep(settings.TELEMETRY_STREAM_INTERVAL_SECONDS)
                        continue

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
                    },
                    "users": users_stats
                }
                await websocket.send_json(payload)
            except Exception as e:
                logger.debug(f"Telemetry stream tick error: {e}")
                await websocket.send_json({"type": "telemetry_error", "error": str(e), "timestamp": time.time()})

            await asyncio.sleep(settings.TELEMETRY_STREAM_INTERVAL_SECONDS)
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket session error: {e}")
        ws_manager.disconnect(websocket)

