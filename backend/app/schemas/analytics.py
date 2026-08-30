from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field


class BillingCycleConfig(BaseModel):
    """Configuration for ISP monthly billing cycle anchor day."""
    anchor_day: int = Field(default=1, ge=1, le=31, description="Day of the month when ISP traffic counters reset (1-31)")


class DailyTrafficPoint(BaseModel):
    """Daily aggregated bandwidth datapoint for timeline charting."""
    record_date: date
    bytes_in: int = 0   # Download bytes
    bytes_out: int = 0  # Upload bytes
    total_bytes: int = 0


class GatewayTrafficSummary(BaseModel):
    """Overall gateway / router traffic metrics for the selected time window."""
    total_bytes_in: int = 0
    total_bytes_out: int = 0
    total_bytes: int = 0
    peak_rx_bps: float = 0.0
    peak_tx_bps: float = 0.0
    monitored_interfaces: List[str] = []


class UserTrafficSummary(BaseModel):
    """Per-user profile traffic consumption and share calculation."""
    user_id: int
    user_name: str
    avatar_icon: str = "user"
    bytes_in: int = 0
    bytes_out: int = 0
    total_bytes: int = 0
    pct_of_total: float = 0.0
    device_count: int = 0


class DeviceTrafficSummary(BaseModel):
    """Per-device traffic consumption, hardware metadata, and parent user attribution."""
    device_id: int
    mac_address: str
    hostname: Optional[str] = None
    custom_name: Optional[str] = None
    ip_address: Optional[str] = None
    vendor: Optional[str] = None
    user_id: Optional[int] = None
    user_name: Optional[str] = None
    bytes_in: int = 0
    bytes_out: int = 0
    total_bytes: int = 0
    pct_of_total: float = 0.0
    speed_limit: str = "default"
    is_paused: bool = False
    is_hidden: bool = False


class TrafficAnalyticsResponse(BaseModel):
    """Comprehensive historical traffic accounting across Gateway, Users, and Devices."""
    start_date: date
    end_date: date
    range_preset: str
    billing_anchor_day: int
    gateway: GatewayTrafficSummary
    users: List[UserTrafficSummary]
    devices: List[DeviceTrafficSummary]
    timeline: List[DailyTrafficPoint]
