"""Runtime tunables: the value in Settings wins, the environment is the fallback.

Why this exists
---------------
MikroMan grew two ways to say the same thing. ``docker-compose.yml`` and the
container environment set a value at start-up; the Settings dialog saved one in
``app_settings``. Where both existed, the operator's edit in the UI was
sometimes consulted and sometimes silently ignored, and the temperature
threshold was the clearest case: the alert path read ``ALERT_TEMP_THRESHOLD_CELSIUS``
from the environment while the metrics path read the ``temp_warning_threshold``
row, so the same board reported "too hot" at two different numbers depending on
which screen you were looking at.

Every name here resolves in one order:

1. the ``app_settings`` row (per-router scoped key first, then the global one),
2. the environment / ``Settings`` default,
3. a literal compiled-in default.

The environment stays meaningful: it is the only value available before the
database exists, and an operator who pinned a number in a compose file should
not find it changed by an upgrade. But it is now a *default*, not an override,
which is what makes the Settings dialog the single place to look.

Nothing here is cached. Each call is one indexed lookup on a table with tens of
rows, and the alternative - a snapshot taken at start-up - is exactly the
behaviour being replaced: a change in the dialog that only applies after a
restart looks identical to a setting that was ignored.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings

logger = logging.getLogger("mikroman.tunables")


async def _stored(session: Optional[AsyncSession], key: str) -> Optional[str]:
    """One ``app_settings`` row, or None. A missing/unreadable table is not an error.

    ``session`` is optional because a couple of call sites (the Telegram service
    at start-up, before any request context exists) have no session to hand and
    must fall back to the environment rather than open one of their own and
    contend for the write lock with the workers.
    """
    if session is None:
        return None
    from backend.app.db.models import AppSetting

    try:
        row = await session.get(AppSetting, key)
    except Exception as e:  # a database that cannot be read must not break a tick
        logger.debug(f"Could not read setting '{key}': {e}")
        return None
    if row is None or row.value is None:
        return None
    text = str(row.value).strip()
    return text or None


async def _scoped(session, key: str, router_id: Optional[int]) -> Optional[str]:
    """Router-scoped row first, then the global one.

    ``{key}_{router_id}`` is the convention the settings endpoints already write;
    reading only the global row would make a per-router threshold look saved in
    the dialog and ignored by the alerts.
    """
    if router_id is not None:
        value = await _stored(session, f"{key}_{router_id}")
        if value is not None:
            return value
    return await _stored(session, key)


async def seconds(
    session: Optional[AsyncSession],
    key: str,
    fallback: float,
    *,
    minimum: float = 1.0,
    maximum: float = 86400.0,
    router_id: Optional[int] = None,
) -> float:
    """A duration setting in seconds, clamped to a range the app survives."""
    raw = await _scoped(session, key, router_id)
    if raw is None:
        return float(fallback)
    try:
        value = float(raw)
    except ValueError:
        logger.warning(f"Setting '{key}' is '{raw}', not a number; using {fallback}")
        return float(fallback)
    return min(max(value, minimum), maximum)


async def flag(
    session: Optional[AsyncSession],
    key: str,
    fallback: bool,
    *,
    router_id: Optional[int] = None,
) -> bool:
    raw = await _scoped(session, key, router_id)
    if raw is None:
        return bool(fallback)
    return raw.strip().lower() not in ("false", "0", "no", "off")


async def text(
    session: Optional[AsyncSession],
    key: str,
    fallback: str,
    *,
    allowed: Optional[tuple] = None,
    router_id: Optional[int] = None,
) -> str:
    raw = await _scoped(session, key, router_id)
    if raw is None:
        return fallback
    if allowed and raw not in allowed:
        logger.warning(f"Setting '{key}' is '{raw}', expected one of {allowed}; using {fallback}")
        return fallback
    return raw


# --- The named tunables the app actually uses ---------------------------------


async def poll_interval_seconds(session: Optional[AsyncSession] = None) -> float:
    """How often the background tick reads telemetry off each router."""
    return await seconds(session, "poll_interval_seconds", settings.POLL_INTERVAL_SECONDS,
                         minimum=5.0, maximum=3600.0)


async def heavy_sync_interval_seconds(session: Optional[AsyncSession] = None) -> float:
    """How often the expensive half of the tick runs (discovery, queues, rollups)."""
    return await seconds(session, "heavy_sync_interval_seconds", settings.HEAVY_SYNC_INTERVAL_SECONDS,
                         minimum=10.0, maximum=86400.0)


async def telemetry_stream_interval_seconds(session: Optional[AsyncSession] = None) -> float:
    """How often a live dashboard frame is pushed to each connected browser."""
    return await seconds(session, "telemetry_interval_seconds",
                         settings.TELEMETRY_STREAM_INTERVAL_SECONDS,
                         minimum=1.0, maximum=300.0)


async def log_retention_days(session: Optional[AsyncSession], default: int = 14) -> int:
    raw = await _stored(session, "log_retention_days")
    if raw is None:
        return default
    try:
        return max(1, min(int(raw), 365))
    except ValueError:
        return default


async def alert_cpu_threshold(session: Optional[AsyncSession] = None) -> int:
    raw = await _stored(session, "alert_cpu_threshold")
    if raw is None:
        return int(settings.ALERT_CPU_THRESHOLD_PERCENT)
    try:
        return max(1, min(int(float(raw)), 100))
    except ValueError:
        return int(settings.ALERT_CPU_THRESHOLD_PERCENT)


async def alert_temp_threshold_celsius(
    session: Optional[AsyncSession] = None,
    router_id: Optional[int] = None,
) -> float:
    """The board-temperature warning line - from one place, for every consumer.

    Reads ``temp_warning_threshold`` because that is the key the Settings dialog
    has always written; ``ALERT_TEMP_THRESHOLD_CELSIUS`` is the fallback for an
    installation that pinned it in the environment and never opened the dialog.
    Before this, the alert path used only the environment and the metrics path
    only the row, so the same router could be "cool" on one screen and "hot" on
    another.
    """
    raw = await _scoped(session, "temp_warning_threshold", router_id)
    if raw is None:
        return float(settings.ALERT_TEMP_THRESHOLD_CELSIUS)
    try:
        return max(-20.0, min(float(raw), 150.0))
    except ValueError:
        return float(settings.ALERT_TEMP_THRESHOLD_CELSIUS)


async def alert_new_device_enabled(session: Optional[AsyncSession] = None) -> bool:
    return await flag(session, "alert_new_device_enabled", settings.ALERT_NEW_DEVICE_ENABLED)


async def telegram_language(session: Optional[AsyncSession] = None) -> str:
    """Bot message language.

    Falls back to the **dashboard language** before the compiled-in default, for
    the same reason :meth:`TelegramBotService.load_persisted_settings` does:
    nobody is served MikroMan in two languages at once, and the environment
    variable is unreachable in the deployment that restarts most often.
    ``telegram_lang`` remains for the case where they genuinely differ.
    """
    explicit = await _scoped(session, "telegram_lang", None)
    if explicit in ("en", "ru"):
        return explicit
    ui_lang = await _stored(session, "lang")
    if ui_lang in ("en", "ru"):
        return ui_lang
    return settings.TELEGRAM_DEFAULT_LANG
