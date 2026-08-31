"""Schemas for WAN speed tests run from a container on the router."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class SpeedTestResultDTO(BaseModel):
    """One completed run. Every figure is optional - a partial result is still
    a result, and is more useful than discarding the run because one field of
    Ookla's output did not parse."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    router_id: Optional[int] = None
    created_at: datetime
    download_mbps: Optional[float] = None
    upload_mbps: Optional[float] = None
    ping_ms: Optional[float] = None
    jitter_ms: Optional[float] = None
    packet_loss_pct: Optional[float] = None
    server_name: Optional[str] = None
    isp: Optional[str] = None
    result_url: Optional[str] = None
    status: str = "ok"
    error: Optional[str] = None


class SpeedTestStatusDTO(BaseModel):
    """Whether a speed test can be run here, and what the last one said.

    ``can_run`` is the single thing the UI needs to decide between showing a
    button and showing an explanation; ``reason`` says which explanation.
    """
    can_run: bool = False
    # 'ready' | 'no_container' | 'package_missing' | 'unreachable'
    reason: str = "no_container"
    container_id: Optional[str] = None
    container_status: Optional[str] = None
    image: Optional[str] = None
    logging_enabled: bool = False
    last_result: Optional[SpeedTestResultDTO] = None


class SpeedTestCreateRequest(BaseModel):
    """Create the speedtest container.

    ``interface`` and ``root_dir`` have no safe defaults: the veth must already
    exist with a working route out, and the root directory must be on storage
    with room for the image. Guessing either produces a container that fails in
    a way that looks like a MikroMan bug.
    """
    interface: str = Field(..., min_length=1, max_length=64)
    root_dir: str = Field(..., min_length=1, max_length=200)
    image: Optional[str] = Field(default=None, max_length=300)


class SpeedTestRunResponse(BaseModel):
    """A finished run, plus the raw output when it could not be understood."""
    result: SpeedTestResultDTO
    raw_output: Optional[str] = None


class SpeedTestHistoryDTO(BaseModel):
    results: List[SpeedTestResultDTO] = []
