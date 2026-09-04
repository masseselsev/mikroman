from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class RouterLogItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[int] = None
    router_id: int
    external_id: Optional[str] = None
    timestamp: datetime
    topics: str
    message: str
    severity: str
    category: str


class RouterLogStats(BaseModel):
    router_id: int
    total_logs: int
    critical_count: int
    error_count: int
    warning_count: int
    auth_failures_count: int


class LoggingRuleItem(BaseModel):
    id: str
    topics: str
    action: str
    prefix: Optional[str] = None
    comment: Optional[str] = None
    is_managed: bool = False


class CreateLoggingRuleRequest(BaseModel):
    topics: str
    action: str = "memory"
    prefix: Optional[str] = None
