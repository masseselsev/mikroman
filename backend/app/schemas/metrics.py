from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class SystemMetricPoint(BaseModel):
    """One display bucket.

    The plain fields carry the *mean* of everything sampled inside the bucket and
    the ``*_peak`` / ``voltage_min`` fields carry the worst case seen in it, so a
    short excursion is not averaged out of existence on the long ranges.
    """

    model_config = ConfigDict(from_attributes=True)

    timestamp: datetime
    cpu_load: float
    memory_usage_pct: float
    memory_used_mb: float
    memory_total_mb: float
    temperature: Optional[float] = None
    voltage: Optional[float] = None
    cpu_peak: float = 0.0
    temperature_peak: Optional[float] = None
    voltage_min: Optional[float] = None
    voltage_max: Optional[float] = None


class SystemMetricsResponse(BaseModel):
    range: str
    points: List[SystemMetricPoint] = []
    current_cpu: float = 0.0
    current_ram_pct: float = 0.0
    current_temp: Optional[float] = None
    current_voltage: Optional[float] = None
    # Width of one display bucket, so the graph can tell "this bucket was empty"
    # apart from "these two buckets are simply adjacent" and leave a real outage
    # visibly blank instead of drawing a ramp across it.
    bucket_seconds: int = 60


class InterfaceRatePoint(BaseModel):
    """One display bucket of interface traffic.

    ``*_rate_bps`` is the mean over the bucket, ``*_peak_bps`` the highest
    instantaneous summed rate inside it. Reporting only the mean made a
    one-minute 300 Mbps burst read as 0.8 Mbps on the 30-day range, where a
    bucket spans four hours.
    """

    model_config = ConfigDict(from_attributes=True)

    timestamp: datetime
    rx_rate_bps: float
    tx_rate_bps: float
    rx_rate_formatted: Optional[str] = None
    tx_rate_formatted: Optional[str] = None
    rx_peak_bps: float = 0.0
    tx_peak_bps: float = 0.0
    rx_peak_formatted: Optional[str] = None
    tx_peak_formatted: Optional[str] = None


class InterfaceHistoryResponse(BaseModel):
    range: str
    interfaces: List[str] = []
    is_summed: bool = True
    points: List[InterfaceRatePoint] = []
    current_rx_bps: float = 0.0
    current_tx_bps: float = 0.0
    total_rx_bytes: int = 0
    total_tx_bytes: int = 0
    bucket_seconds: int = 60


class MonitoredInterfacesConfig(BaseModel):
    router_id: Optional[int] = None
    selected_interfaces: List[str] = []
