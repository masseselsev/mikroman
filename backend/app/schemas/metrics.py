from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class SystemMetricPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timestamp: datetime
    cpu_load: float
    memory_usage_pct: float
    memory_used_mb: float
    memory_total_mb: float
    temperature: Optional[float] = None
    voltage: Optional[float] = None


class SystemMetricsResponse(BaseModel):
    range: str
    points: List[SystemMetricPoint] = []
    current_cpu: float = 0.0
    current_ram_pct: float = 0.0
    current_temp: Optional[float] = None
    current_voltage: Optional[float] = None


class InterfaceRatePoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timestamp: datetime
    rx_rate_bps: float
    tx_rate_bps: float
    rx_rate_formatted: Optional[str] = None
    tx_rate_formatted: Optional[str] = None


class InterfaceHistoryResponse(BaseModel):
    range: str
    interfaces: List[str] = []
    is_summed: bool = True
    points: List[InterfaceRatePoint] = []
    current_rx_bps: float = 0.0
    current_tx_bps: float = 0.0
    total_rx_bytes: int = 0
    total_tx_bytes: int = 0


class MonitoredInterfacesConfig(BaseModel):
    router_id: Optional[int] = None
    selected_interfaces: List[str] = []
