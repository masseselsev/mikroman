"""WAN speed tests run from a container on the router itself.

Measuring from the router rather than from wherever MikroMan is installed is the
whole point: a test run on a laptop over Wi-Fi measures the laptop's Wi-Fi, and
reports the ISP link as slower than it is. See
:mod:`backend.app.services.speedtest` for why this is driven through the
container's log rather than by executing a command in it.
"""
import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import SpeedTestResult
from backend.app.db.session import get_db
from backend.app.schemas.common import APIResponse
from backend.app.schemas.speedtest import (
    SpeedTestCreateRequest,
    SpeedTestResultDTO,
    SpeedTestRunResponse,
    SpeedTestStatusDTO,
)
from backend.app.services.container_manager import ContainerManager
from backend.app.services.router_manager import router_manager
from backend.app.services.speedtest import DEFAULT_IMAGE, SpeedTestRunner

logger = logging.getLogger("mikroman.speedtest")

router = APIRouter(prefix="/routers/{router_id}/speedtest", tags=["Speed test"])

# One run at a time per router. Two concurrent Ookla transfers on the same link
# measure each other's contention, not the line.
_running: set = set()


async def _client(router_id: int, db: AsyncSession):
    client = await router_manager.get_client(router_id, session=db)
    if client is None:
        raise HTTPException(status_code=404, detail="Router not found or not reachable")
    return client


async def _latest(db: AsyncSession, router_id: int) -> SpeedTestResult | None:
    stmt = (
        select(SpeedTestResult)
        .where(SpeedTestResult.router_id == router_id)
        .order_by(desc(SpeedTestResult.created_at))
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


@router.get("", response_model=APIResponse[SpeedTestStatusDTO])
async def speedtest_status(router_id: int, db: AsyncSession = Depends(get_db)):
    """Whether a test can be run here, and the most recent result."""
    last = await _latest(db, router_id)
    last_dto = SpeedTestResultDTO.model_validate(last) if last else None

    try:
        client = await _client(router_id, db)
    except HTTPException:
        return APIResponse(data=SpeedTestStatusDTO(
            can_run=False, reason="unreachable", last_result=last_dto
        ))

    support = await ContainerManager(client)._probe_support()
    if support.status != "ready":
        # 'not_installed' / 'disabled' / 'unreachable' all mean the same thing
        # to this endpoint: there is nowhere to run a test.
        return APIResponse(data=SpeedTestStatusDTO(
            can_run=False,
            reason="package_missing" if support.status != "unreachable" else "unreachable",
            last_result=last_dto,
        ))

    runner = SpeedTestRunner(client)
    try:
        container = await runner.find_container()
    except Exception as e:
        logger.debug(f"Could not list containers for speed test status: {e}")
        container = None

    if container is None:
        return APIResponse(data=SpeedTestStatusDTO(
            can_run=False, reason="no_container", last_result=last_dto
        ))

    logging_ok = False
    try:
        rules = await client.get_logging_rules()
        logging_ok = any(
            "container" in (r.get("topics") or "").lower()
            and (r.get("disabled") or "false") != "true"
            for r in rules
        )
    except Exception:
        pass

    return APIResponse(data=SpeedTestStatusDTO(
        can_run=True,
        reason="ready",
        container_id=container.get(".id"),
        container_status=container.get("status"),
        image=container.get("remote-image") or container.get("image"),
        logging_enabled=logging_ok,
        last_result=last_dto,
    ))


@router.post("/container", response_model=APIResponse[SpeedTestStatusDTO])
async def create_speedtest_container(
    router_id: int,
    payload: SpeedTestCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create the speedtest container on this router.

    The image is pulled by RouterOS, which takes a while and happens in the
    background; the container is not usable until its status turns to
    ``stopped``. The status endpoint reports that, so the UI can wait.
    """
    client = await _client(router_id, db)
    support = await ContainerManager(client)._probe_support()
    if support.status != "ready":
        raise HTTPException(status_code=409, detail=support.message or "Container support unavailable")

    runner = SpeedTestRunner(client)
    if await runner.find_container() is not None:
        raise HTTPException(status_code=409, detail="A speed test container already exists")

    try:
        await runner.create_container(
            interface=payload.interface,
            root_dir=payload.root_dir,
            image=payload.image or DEFAULT_IMAGE,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Router rejected the container: {e}")

    await runner.ensure_logging()
    return await speedtest_status(router_id, db)


@router.post("/run", response_model=APIResponse[SpeedTestRunResponse])
async def run_speedtest(router_id: int, db: AsyncSession = Depends(get_db)):
    """Run a test and record the result.

    Blocks for as long as the test takes - up to a couple of minutes. That is
    deliberate: the alternative is a job id and a polling endpoint for something
    that happens at most a few times a day, at the cost of a state machine that
    can strand a run.
    """
    if router_id in _running:
        raise HTTPException(
            status_code=409,
            detail="A speed test is already running on this router.",
        )

    client = await _client(router_id, db)
    runner = SpeedTestRunner(client)

    _running.add(router_id)
    try:
        reading = await runner.run()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(f"Speed test failed on router {router_id}: {e}")
        reading = None
        error = str(e)
    else:
        error = None
    finally:
        _running.discard(router_id)

    record = SpeedTestResult(
        router_id=router_id,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        status="failed" if reading is None else reading.status,
        error=error if reading is None else reading.error,
    )
    if reading is not None:
        record.download_mbps = reading.download_mbps
        record.upload_mbps = reading.upload_mbps
        record.ping_ms = reading.ping_ms
        record.jitter_ms = reading.jitter_ms
        record.packet_loss_pct = reading.packet_loss_pct
        record.server_name = reading.server_name
        record.isp = reading.isp
        record.result_url = reading.result_url
        # Kept only when nothing parsed, so a parser fix can be checked against
        # real output instead of an assumption about its shape.
        record.raw_output = reading.raw_output if reading.status != "ok" else None

    db.add(record)
    await db.commit()
    await db.refresh(record)

    return APIResponse(
        data=SpeedTestRunResponse(
            result=SpeedTestResultDTO.model_validate(record),
            raw_output=record.raw_output,
        ),
        message=record.error,
    )


@router.get("/history", response_model=APIResponse[list[SpeedTestResultDTO]])
async def speedtest_history(router_id: int, limit: int = 20, db: AsyncSession = Depends(get_db)):
    """Recent runs, newest first. A single reading of a noisy quantity says
    much less than a trend does."""
    stmt = (
        select(SpeedTestResult)
        .where(SpeedTestResult.router_id == router_id)
        .order_by(desc(SpeedTestResult.created_at))
        .limit(max(1, min(limit, 100)))
    )
    rows = (await db.execute(stmt)).scalars().all()
    return APIResponse(data=[SpeedTestResultDTO.model_validate(r) for r in rows])
