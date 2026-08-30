from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field


class QuotaConfigDTO(BaseModel):
    """ISP data allowance for one billing cycle, and when to warn about it."""
    limit_bytes: int = Field(default=0, ge=0, description="Cycle allowance in bytes; 0 disables the quota")
    thresholds: List[int] = Field(default_factory=list, description="Percentages at which to alert, e.g. [50, 80, 100]")
    notify_telegram: bool = True


class QuotaStatusDTO(BaseModel):
    """Consumption against the quota for the current billing cycle."""
    limit_bytes: int = 0
    used_bytes: int = 0
    remaining_bytes: int = 0
    used_pct: float = 0.0
    cycle_start: Optional[date] = None
    cycle_end: Optional[date] = None
    days_remaining: int = 0
    # Average daily allowance for the rest of the cycle to stay within quota.
    projected_daily_budget: int = 0
    thresholds: List[int] = Field(default_factory=list)
    thresholds_reached: List[int] = Field(default_factory=list)
    enabled: bool = False


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


class AccountingHealth(BaseModel):
    """Cross-check between gateway volume and per-device accounted volume.

    The gateway figure comes from WAN interface counters; the accounted figure is
    the sum of per-device mangle counters. A large gap means the per-device
    accounting path is broken and the breakdown below must not be trusted.

    This exists because the previous implementation silently reported
    ``max(gateway, users, devices)``, which made a totally dead per-device
    accounting path look like a plausible dashboard for two days.
    """
    gateway_bytes: int = 0
    accounted_bytes: int = 0
    coverage_pct: float = 0.0
    # 'ok'       - device counters track the gateway
    # 'partial'  - range predates per-device accounting; breakdown is incomplete
    # 'degraded' - accounting is active but attributing almost nothing (a fault)
    # 'no_data'  - nothing recorded for this range yet
    status: str = "ok"
    message: Optional[str] = None


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
    accounting_health: AccountingHealth = AccountingHealth()
