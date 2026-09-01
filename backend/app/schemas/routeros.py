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
    # `/system/resource` reports `cpu` as the instruction set on RouterBOARD
    # hardware ("ARM64", "MMIPS") but as the real part on x86 and CHR
    # ("Intel(R) Atom(TM) CPU C3558 @ 2.20GHz"), so it is only a good CPU label
    # on those platforms - see RouterBoardInfo.firmware_type for the SoC name.
    cpu: Optional[str] = None
    cpu_count: int = 1
    cpu_frequency: Optional[int] = None
    architecture_name: Optional[str] = None
    wan_ip: Optional[str] = None


class RouterBoardInfo(BaseModel):
    """Static hardware identity from `/system/routerboard`.

    `firmware_type` is the bootloader/SoC platform - "ipq5300", "al21400",
    "ar9344" - and is the closest thing RouterOS exposes to a CPU part number
    on MikroTik hardware. It is only populated when `is_routerboard` is true;
    a CHR, an x86 install or a container returns nothing useful here and the
    caller should fall back to RouterSystemResource.cpu.
    """

    model_config = ConfigDict(from_attributes=True)

    is_routerboard: bool = False
    model: Optional[str] = None
    serial_number: Optional[str] = None
    firmware_type: Optional[str] = None
    current_firmware: Optional[str] = None
    upgrade_firmware: Optional[str] = None
    factory_firmware: Optional[str] = None


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
    # True when this interface carries a default route - the real "which links
    # face the internet" answer, filled in by the interfaces endpoint from
    # ``/ip/route`` rather than guessed from the name.
    is_wan: bool = False
    # The physical (or bridge) interface this one rides on, when it is a VLAN,
    # a PPPoE client or a bridge port. Lets the picker show the nesting.
    parent: Optional[str] = None


# WiFiRegistrationDTO references WiFiLinkDTO before it is declared.
WiFiRegistrationDTO.model_rebuild()
