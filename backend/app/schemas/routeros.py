from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class RouterSystemResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    board_name: Optional[str] = None
    model: Optional[str] = None
    version: Optional[str] = None
    cpu_load: int = 0
    free_memory: int = 0
    total_memory: int = 0
    uptime: Optional[str] = None
    cpu_count: int = 1
    cpu_frequency: Optional[int] = None
    architecture_name: Optional[str] = None
    wan_ip: Optional[str] = None


class RouterSystemHealth(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    temperature: Optional[float] = None
    voltage: Optional[float] = None


class DHCPLeaseDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    address: str
    mac_address: str
    host_name: Optional[str] = None
    server: Optional[str] = None
    status: Optional[str] = "bound"
    comment: Optional[str] = None
    expires_after: Optional[str] = None


class ARPTableEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    address: str
    mac_address: str
    interface: Optional[str] = None
    complete: bool = True


class WiFiRegistrationDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    mac_address: str
    interface: str
    ssid: Optional[str] = None
    signal_strength: Optional[int] = None  # dBm
    tx_rate: Optional[str] = None
    rx_rate: Optional[str] = None
    uptime: Optional[str] = None
    band: Optional[str] = None  # e.g. '5ghz-be', '2ghz-ax'
    # WiFi 7 multi-link: one client associates over several radios at once.
    # RouterOS reports it as a single mld* entry carrying parallel lists of the
    # member radios and the per-link MAC addresses.
    links: List["WiFiLinkDTO"] = []


class WiFiLinkDTO(BaseModel):
    """One radio link of a (possibly multi-link) wireless association."""
    model_config = ConfigDict(from_attributes=True)

    interface: str
    mac_address: Optional[str] = None
    signal_strength: Optional[int] = None  # dBm
    band: Optional[str] = None


class InterfaceDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    name: str
    type: Optional[str] = None
    running: bool = True
    disabled: bool = False
    rx_byte: int = 0
    tx_byte: int = 0
    rx_rate: int = 0  # bps
    tx_rate: int = 0  # bps
    # Link quality counters. A rising error or drop count is the earliest sign
    # of a failing cable, duplex mismatch or saturated link.
    rx_error: int = 0
    tx_error: int = 0
    rx_drop: int = 0
    tx_drop: int = 0
    mac_address: Optional[str] = None
    mtu: Optional[str] = None


# WiFiRegistrationDTO references WiFiLinkDTO before it is declared.
WiFiRegistrationDTO.model_rebuild()
