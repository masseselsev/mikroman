from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field


class QuotaConfigDTO(BaseModel):
    """ISP data allowance for one billing cycle, and when to warn about it."""
    limit_bytes: int = Field(default=0, ge=0, description="Cycle allowance in bytes; 0 disables the quota")
    thresholds: List[int] = Field(default_factory=list, description="Percentages at which to alert, e.g. [50, 80, 100]")
    notify_telegram: bool = True
    portal_url: Optional[str] = Field(default=None, description="Link to the ISP usage/billing page or the modem's stats page")
    portal_label: Optional[str] = Field(default=None, max_length=40, description="Short label for the portal link button")


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
    # --- end-of-cycle forecast -------------------------------------------------
    # Length of the cycle and how much of it has passed (today counts).
    cycle_days_total: int = 0
    cycle_days_elapsed: int = 0
    # Conservative projection: the cycle-so-far daily average, held for the whole
    # cycle. This is the headline number and what `on_track` is judged on.
    projected_bytes_linear: int = 0
    projected_pct_linear: float = 0.0
    # "At current pace": the recent daily mean, blended with last cycle's daily
    # average on a weight that ramps from 0 to 1 over the first 7 days, then
    # extrapolated over the days left. Reacts to a binge without swinging wildly
    # on day one. Shown as a secondary figure.
    pace_bytes_per_day: int = 0
    projected_bytes_at_pace: int = 0
    projected_pct_at_pace: float = 0.0
    # The previous full billing cycle, for context and as the early-cycle anchor.
    prev_cycle_bytes: int = 0
    prev_cycle_bytes_per_day: int = 0
    # 0.0 = pace is entirely last cycle's average, 1.0 = entirely this cycle's
    # recent mean. Ramps over the first 7 days.
    pace_blend_weight: float = 1.0
    # 'blended' - recent mean blended with last cycle
    # 'recent'  - recent mean only (no previous cycle on record)
    # 'sparse'  - too little data anywhere; equals the conservative projection
    pace_basis: str = "recent"
    # True while the conservative projection lands at or under the limit.
    on_track: bool = True
    thresholds: List[int] = Field(default_factory=list)
    thresholds_reached: List[int] = Field(default_factory=list)
    enabled: bool = False
    portal_url: Optional[str] = None
    portal_label: Optional[str] = None
    # Echoed back so the settings form can restore the saved choice. Without it
    # the UI had to assume a value, and assumed True - which silently re-enabled
    # Telegram alerts for anyone who had turned them off.
    notify_telegram: bool = True


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

    # --- coverage split -------------------------------------------------
    # ``coverage_pct`` above is judged ONLY over the days per-device accounting
    # was actually running for the whole day. Mixing in earlier days compares a
    # full period of gateway volume against a partial period of device volume
    # and produces an alarming number that describes bookkeeping history rather
    # than any real loss, which is exactly what these fields exist to separate.
    accounting_started: Optional[date] = None
    # Gateway volume in this range recorded on or before ``accounting_started``.
    # Unattributable by construction, not a fault.
    pre_accounting_bytes: int = 0
    # Gateway volume in this range from the days after accounting was running
    # end to end - the only volume ``coverage_pct`` is measured against.
    measured_bytes: int = 0
    # Per-device volume attributed within that same measured window.
    measured_accounted_bytes: int = 0
    # Volume attributed on the pre-accounting days. Not zero: the switch-on day
    # is counted as pre-accounting because it is a partial day, but the hours
    # after the rules went up were attributed normally, and older installs also
    # carry per-user volume from the queue-based accounting that preceded this.
    #
    # Defined as ``accounted_bytes - measured_accounted_bytes`` so the two
    # always add back up to the range total the breakdown tables below show. An
    # earlier version summed each window independently, and the banner's figure
    # then failed to reconcile with the user table - which reads as a counting
    # bug even though both numbers were right for their own window.
    pre_accounting_accounted_bytes: int = 0


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
