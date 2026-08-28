from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class DeviceBase(BaseModel):
    mac_address: str
    ip_address: Optional[str] = None
    hostname: Optional[str] = None
    custom_name: Optional[str] = None
    vendor: Optional[str] = None
    last_interface: Optional[str] = None
    last_wifi_signal: Optional[int] = None
    is_active: bool = True


class DeviceCreate(DeviceBase):
    user_id: Optional[int] = None


class DeviceUpdate(BaseModel):
    user_id: Optional[int] = None
    custom_name: Optional[str] = None
    is_active: Optional[bool] = None


class DeviceDTO(DeviceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: Optional[int] = None
    last_seen: datetime
