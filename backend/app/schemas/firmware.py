from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class PackageUpdateInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    installed_version: str
    latest_version: Optional[str] = None
    channel: str
    status: str
    update_available: bool


class RouterBoardInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    is_routerboard: bool = False
    model: Optional[str] = None
    serial_number: Optional[str] = None
    current_firmware: Optional[str] = None
    upgrade_firmware: Optional[str] = None
    firmware_available: bool = False


class RouterFirmwareStatusOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    router_id: int
    router_name: str
    packages: PackageUpdateInfo
    routerboard: RouterBoardInfo
    checked_at: datetime


class FirmwareChannelUpdatePayload(BaseModel):
    channel: Literal["stable", "long-term", "testing", "development"]


class FirmwareUpgradePayload(BaseModel):
    confirm_name: str
    stage_bootloader: bool = True


class BootloaderUpgradePayload(BaseModel):
    confirm_name: str
    reboot: bool = False


class ChangelogOut(BaseModel):
    version: str
    notes: str

