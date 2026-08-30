from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class SpeedLimitUpdate(BaseModel):
    speed_limit: str  # e.g., "5M/20M", "10M/50M", "unlimited"


class PauseStateUpdate(BaseModel):
    is_paused: bool


class SimpleQueueItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    name: str
    target: str
    max_limit: str
    rate: Optional[str] = "0/0"  # "upload_bps/download_bps"
    bytes: Optional[str] = "0/0"
    comment: Optional[str] = None
    disabled: bool = False
    # RouterOS reports "none" for a top-level queue; required to detect drift in
    # the hierarchical parent/child shaping tree.
    parent: Optional[str] = None


class RealtimeTelemetrySnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timestamp: float
    router_cpu: int
    router_ram_free_mb: float
    router_temp: Optional[float] = None
    wan_rx_rate_bps: int = 0
    wan_tx_rate_bps: int = 0
    total_active_devices: int = 0
    users_traffic: List[dict] = []
