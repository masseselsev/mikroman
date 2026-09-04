"""Pydantic schemas for live connection tracking, termination, and destination analytics."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class LiveConnectionItem(BaseModel):
    id: str
    protocol: str
    src_ip: str
    src_port: Optional[int] = None
    dst_ip: str
    dst_port: Optional[int] = None
    device_id: Optional[int] = None
    device_name: Optional[str] = None
    user_id: Optional[int] = None
    user_name: Optional[str] = None
    domain: Optional[str] = None
    country_code: Optional[str] = None
    country_name: Optional[str] = None
    flag_emoji: Optional[str] = None
    tcp_state: Optional[str] = None
    orig_rate: int = 0
    repl_rate: int = 0
    orig_bytes: int = 0
    repl_bytes: int = 0
    total_bytes: int = 0
    timeout: Optional[str] = None
    is_immune: bool = False


class KillConnectionRequest(BaseModel):
    router_id: Optional[int] = None
    src_ip: Optional[str] = None
    dst_ip: Optional[str] = None


class UserDestinationStatItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: Optional[int] = None
    device_id: Optional[int] = None
    destination_ip: str
    domain: Optional[str] = None
    country_code: Optional[str] = None
    country_name: Optional[str] = None
    flag_emoji: Optional[str] = None
    bytes_in: int = 0
    bytes_out: int = 0
    total_bytes: int = 0
    hit_count: int = 1
    last_seen: Optional[datetime] = None

