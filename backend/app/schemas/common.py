from datetime import datetime
from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    model_config = ConfigDict(from_attributes=True)

    success: bool = True
    message: Optional[str] = None
    data: Optional[T] = None


ApiResponse = APIResponse


class PaginatedResponse(BaseModel, Generic[T]):
    model_config = ConfigDict(from_attributes=True)

    total: int
    items: List[T]


class AppSettingDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    value: str
    description: Optional[str] = None
    updated_at: Optional[datetime] = None


class AlertLogDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    alert_type: str
    message: str
    metadata_payload: Optional[dict] = None
    created_at: datetime
