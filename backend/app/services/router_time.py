"""Daily boundaries anchored to the router's clock rather than the container's.

The container almost always runs UTC while the router sits in a local timezone.
On a UTC+5 router every moment between 19:00 and midnight local time belongs to
the *previous* UTC date, so traffic recorded during those hours was filed under
yesterday, and "today" on the dashboard meant a different day than the router's
own clock displayed.

Everything the dashboard reports - daily rollups, billing cycles, date range
presets - is about the router, so the router's date is the correct key.

The offset is stored **per router** (``router_gmt_offset_minutes_<id>``): with
several managed routers in different timezones a single shared value would be
whatever the last-polled router wrote, so switching to a router in another zone
skewed its rollup dates and history view until its next telemetry tick. A
call that cannot name a router (there are a few legacy ones) falls back to the
un-suffixed key, which also carries the value for a single-router install.
"""
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import AppSetting

logger = logging.getLogger("mikroman.router_time")

ROUTER_OFFSET_SETTING_KEY = "router_gmt_offset_minutes"


def _offset_key(router_id: Optional[int]) -> str:
    return f"{ROUTER_OFFSET_SETTING_KEY}_{router_id}" if router_id else ROUTER_OFFSET_SETTING_KEY


def shift_to_router_local(now_utc: datetime, offset_minutes: Optional[int]) -> datetime:
    """Convert a UTC moment into the router's local wall-clock time."""
    if not offset_minutes:
        return now_utc
    return now_utc + timedelta(minutes=offset_minutes)


async def store_router_offset(
    session: AsyncSession, offset_minutes: Optional[int], router_id: Optional[int] = None
) -> None:
    """Persist a router's UTC offset so date keys survive a restart.

    Written whenever that router's clock is read; without it a freshly started
    container would file traffic against UTC dates until the first poll. Also
    mirrored to the un-suffixed key so a caller that has no router id still
    reads a sane value.
    """
    if offset_minutes is None:
        return
    keys = [_offset_key(router_id)]
    if router_id:
        keys.append(ROUTER_OFFSET_SETTING_KEY)
    for key in keys:
        setting = await session.get(AppSetting, key)
        if setting:
            if setting.value != str(offset_minutes):
                setting.value = str(offset_minutes)
        else:
            session.add(AppSetting(
                key=key,
                value=str(offset_minutes),
                description="Router UTC offset in minutes; anchors daily rollup boundaries",
            ))
    await session.commit()


async def get_router_offset(
    session: AsyncSession, router_id: Optional[int] = None
) -> Optional[int]:
    """A router's stored UTC offset in minutes, or None if not yet known.

    Prefers the per-router value; falls back to the shared key for a call that
    could not name a router, or a router whose own offset has not been stored
    yet (freshly added, first poll pending).
    """
    for key in ([_offset_key(router_id), ROUTER_OFFSET_SETTING_KEY] if router_id else [ROUTER_OFFSET_SETTING_KEY]):
        setting = await session.get(AppSetting, key)
        if setting and setting.value:
            try:
                return int(setting.value)
            except (TypeError, ValueError):
                logger.warning("Stored router UTC offset %r is not a number; ignoring", key)
    return None


async def router_local_date(
    session: AsyncSession,
    now_utc: Optional[datetime] = None,
    router_id: Optional[int] = None,
) -> date:
    """Today's date on the router.

    Falls back to UTC when no offset is known, rather than guessing one.
    """
    moment = now_utc or datetime.now(timezone.utc).replace(tzinfo=None)
    return shift_to_router_local(moment, await get_router_offset(session, router_id)).date()


async def router_local_now(
    session: AsyncSession, router_id: Optional[int] = None
) -> datetime:
    """Current wall-clock time on the router."""
    moment = datetime.now(timezone.utc).replace(tzinfo=None)
    return shift_to_router_local(moment, await get_router_offset(session, router_id))
