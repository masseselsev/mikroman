from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.app.schemas.device import DeviceDTO


class UserBase(BaseModel):
    name: str = Field(
        ...,
        min_length=1,
        max_length=60,
        pattern=r"^[a-zA-Z0-9_\-\. ]+$",
        description="User name containing only English letters, numbers, spaces, hyphens, or underscores"
    )
    avatar_icon: str = "user"
    speed_limit: str = "unlimited"  # e.g., "10M/50M", "5M/20M", "unlimited"
    is_paused: bool = False
    priority: int = 1


class UserCreate(UserBase):
    device_macs: Optional[List[str]] = None


class UserUpdate(BaseModel):
    name: Optional[str] = Field(
        None,
        min_length=1,
        max_length=60,
        pattern=r"^[a-zA-Z0-9_\-\. ]+$",
        description="User name containing only English letters, numbers, spaces, hyphens, or underscores"
    )
    avatar_icon: Optional[str] = None
    speed_limit: Optional[str] = None
    is_paused: Optional[bool] = None
    priority: Optional[int] = None
    device_macs: Optional[List[str]] = None


class UserReorderRequest(BaseModel):
    """New card order, as user ids from first to last."""
    user_ids: List[int]


class UserDTO(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sort_order: int = 0
    created_at: datetime
    updated_at: datetime
    devices: List[DeviceDTO] = []

    # Real-time computed fields (populated by telemetry engine)
    current_rate_in: int = 0   # bps down
    current_rate_out: int = 0  # bps up
    bytes_today_in: int = 0    # bytes downloaded today
    bytes_today_out: int = 0   # bytes uploaded today
    # All-time and current-billing-cycle volume, summed from this user's
    # devices' daily rollups. Shown next to the "today" figure on the card.
    bytes_total_in: int = 0    # bytes downloaded, all time
    bytes_total_out: int = 0   # bytes uploaded, all time
    bytes_cycle_in: int = 0    # bytes downloaded, this billing cycle
    bytes_cycle_out: int = 0   # bytes uploaded, this billing cycle
    # Most recent moment any of this user's devices was seen on the network.
    last_seen: Optional[datetime] = None
