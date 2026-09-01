from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field


class DeviceHistoryDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int
    mac_address: str
    hostname: Optional[str] = None
    ip_address: Optional[str] = None
    event_type: str
    details: Optional[str] = None
    created_at: datetime


class DeviceBase(BaseModel):
    router_id: Optional[int] = None
    mac_address: str
    ip_address: Optional[str] = None
    hostname: Optional[str] = None
    custom_name: Optional[str] = None
    vendor: Optional[str] = None
    last_interface: Optional[str] = None
    last_wifi_signal: Optional[int] = None
    is_active: bool = True
    is_hidden: bool = False
    # A workload on the router itself, seen on a veth interface, rather than a
    # client on the network. Kept out of the unassigned inbox and the household
    # breakdown - nobody owns it.
    is_container: bool = False
    speed_limit: str = "default"  # "default" (inherits user limit), "unlimited", or "10M/30M"
    is_paused: bool = False
    priority: int = 1  # 0 = Low, 1 = Normal, 2 = High
    # Set when this record is a secondary adapter of another physical device.
    linked_to_device_id: Optional[int] = None
    connection_kind: Optional[str] = None  # 'wired' | 'wireless'
    # Radio links of the current wireless association. A WiFi 7 multi-link
    # client is bonded over several radios at once, each with its own signal,
    # which the 'mld1' interface name alone does not convey.
    wifi_links: Optional[List[dict]] = None


class DeviceCreate(DeviceBase):
    user_id: Optional[int] = None


class DeviceUpdate(BaseModel):
    router_id: Optional[int] = None
    user_id: Optional[int] = None
    custom_name: Optional[str] = None
    ip_address: Optional[str] = None  # send explicit null to clear a stale lease
    is_active: Optional[bool] = None
    is_hidden: Optional[bool] = None
    speed_limit: Optional[str] = None
    is_paused: Optional[bool] = None
    priority: Optional[int] = None
    # When unassigning (user_id -> null): also subtract this device's recorded
    # daily volume back out of the profile's totals. Defaults to true.
    detach_traffic: Optional[bool] = None


class DeviceSplitRequest(BaseModel):
    """Break a wrongly-merged MAC back out into its own device record."""
    mac_address: str = Field(..., description="An address from this device's history to split off")


class DeviceSpeedLimitUpdate(BaseModel):
    speed_limit: str  # "default", "unlimited", or "10M/30M"


class DevicePauseUpdate(BaseModel):
    is_paused: bool


class DeviceMergeRequest(BaseModel):
    target_device_id: int
    note: Optional[str] = None


class DeviceLinkRequest(BaseModel):
    """Attach this device to another as an additional network adapter."""
    primary_device_id: int


class DeviceSuggestionDTO(BaseModel):
    unassigned_device_id: int
    suggested_target_device_id: int
    suggested_user_id: int
    suggested_user_name: str
    target_device_name: str
    confidence: float
    reason: str


class DeviceDTO(DeviceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: Optional[int] = None
    last_seen: datetime
    history: List[DeviceHistoryDTO] = []

    # Live figures populated by the telemetry engine from firewall counters.
    # A device with no counter sample reports 0 rather than a stale value.
    current_rate_in: int = 0    # bps download
    current_rate_out: int = 0   # bps upload
    bytes_today_in: int = 0     # bytes downloaded today
    bytes_today_out: int = 0    # bytes uploaded today
    # All-time totals, summed from the daily per-device rollups (which already
    # include today's running figure). Drives the compact "today / total /
    # share" readout beside the device name.
    bytes_total_in: int = 0     # bytes downloaded, all time
    bytes_total_out: int = 0    # bytes uploaded, all time
    # Same, but only over the current ISP billing cycle - the window the quota
    # is measured against.
    bytes_cycle_in: int = 0     # bytes downloaded, this billing cycle
    bytes_cycle_out: int = 0    # bytes uploaded, this billing cycle

    @computed_field
    @property
    def is_randomized_mac(self) -> bool:
        """Determines if the MAC address has the IEEE 802 Locally Administered bit set."""
        if not self.mac_address or len(self.mac_address) < 2:
            return False
        try:
            first_byte = int(self.mac_address[:2], 16)
            return (first_byte & 0x02) != 0
        except ValueError:
            return False
