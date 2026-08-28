import asyncio
import logging
import time
from typing import Optional, Set

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from backend.app.core.config import settings
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

                # Calculate gateway aggregate speeds
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

