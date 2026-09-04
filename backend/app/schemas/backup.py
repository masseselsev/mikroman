from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class RouterBackupBase(BaseModel):
    is_pinned: bool = False
    note: Optional[str] = None

class RouterBackupUpdate(BaseModel):
    is_pinned: Optional[bool] = None
    note: Optional[str] = None

class RouterBackupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    router_id: int
    created_at: datetime
    outcome: str
    source: str
    fingerprint: Optional[str] = None
    rsc_bytes: int = 0
    backup_bytes: int = 0
    is_pinned: bool = False
    note: Optional[str] = None
    model: Optional[str] = None
    serial: Optional[str] = None
    os_version: Optional[str] = None
    error_message: Optional[str] = None
    duration_ms: int = 0
    has_rsc: bool = False
    has_binary: bool = False

class RouterBackupListResponse(BaseModel):
    items: List[RouterBackupResponse]
    total: int
    page: int
    page_size: int
