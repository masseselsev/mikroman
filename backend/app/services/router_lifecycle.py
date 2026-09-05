"""Archiving, restoring, purging and hardware-swapping a managed router.

Deleting a router is not one operation. The operator may want to keep every
user, device and traffic total for a box that is only being re-cabled or
re-added later (``archive``), or to erase all trace of it (``purge``). And a
failed router is often *replaced* rather than removed - the new hardware
should inherit the old one's users, devices, history and settings without a
manual re-entry (``swap_hardware``).

FK enforcement is off on the SQLite deployment, so ``ON DELETE CASCADE`` in the
models does not fire. Every child table is therefore deleted explicitly here,
children before parents, inside the caller's transaction.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    AlertLog,
    AppSetting,
    Device,
    DeviceHistory,
    DeviceTrafficBucket,
    DeviceTrafficRollup,
    InterfaceMetric,
    InterfaceTrafficRollup,
    Router,
    RouterBackup,
    RouterLog,
    RouterSelfTrafficRollup,
    RouterTrafficRollup,
    SpeedTestResult,
    SystemMetric,
    TrafficRollup,
    User,
    UserDestinationStat,
    UserTrafficBucket,
)

logger = logging.getLogger("mikroman.router_lifecycle")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def reassign_default_if_needed(session: AsyncSession) -> Optional[int]:
    """Ensure exactly one live router is the default. Returns its id, if any."""
    live = (
        await session.execute(
            select(Router)
            .where(Router.archived_at.is_(None))
            .order_by(Router.is_default.desc(), Router.id.asc())
        )
    ).scalars().all()
    if not live:
        return None
    if not any(r.is_default for r in live):
        live[0].is_default = True
    # Collapse any accidental multiples.
    seen_default = False
    for r in live:
        if r.is_default and not seen_default:
            seen_default = True
        elif r.is_default:
            r.is_default = False
    return next((r.id for r in live if r.is_default), None)


async def archive_router(session: AsyncSession, router: Router) -> None:
    """Hide the router and stop every loop from touching it; keep all its data.

    The row, its users, devices, rollups, metrics and ``*_<id>`` settings are
    left exactly as they are. ``get_client`` and every ``get_*_router`` helper
    filter on ``archived_at IS NULL``, so nothing polls or accounts it while it
    sleeps.
    """
    router.archived_at = _now()
    router.is_default = False
    router.is_active = False
    await session.flush()
    await reassign_default_if_needed(session)


async def restore_router(
    session: AsyncSession, router: Router, connection: Optional[Dict] = None
) -> None:
    """Bring an archived router back. Optionally refresh its connection fields.

    ``connection`` carries the details from the add-router form when the
    restore was triggered by the operator adding a router whose serial matched
    this archived one - the box may now be on a new address or have new
    credentials.
    """
    router.archived_at = None
    router.is_active = True
    if connection:
        for field in ("name", "host", "port", "use_ssl", "ssl_verify", "username", "comment"):
            if connection.get(field) is not None:
                setattr(router, field, connection[field])
        if connection.get("password"):
            router.password = connection["password"]
        if connection.get("serial_number"):
            router.serial_number = connection["serial_number"]
    await session.flush()
    await reassign_default_if_needed(session)


async def find_archived_by_serial(
    session: AsyncSession, serial: Optional[str]
) -> Optional[Router]:
    """The archived router with this RouterBoard serial, if one exists."""
    if not serial:
        return None
    return (
        await session.execute(
            select(Router).where(
                Router.serial_number == serial,
                Router.archived_at.is_not(None),
            )
        )
    ).scalars().first()


async def purge_router(session: AsyncSession, router: Router) -> Dict[str, int]:
    """Delete the router and everything that belonged to it.

    Order matters: rows that reference users/devices go before the users and
    devices, which go before the router row. Returns per-table delete counts
    for the confirmation message and the log.
    """
    rid = router.id
    counts: Dict[str, int] = {}

    async def _count(model, whereclause) -> int:
        return int(
            (await session.execute(select(func.count()).select_from(model).where(whereclause))).scalar_one()
        )

    user_ids = select(User.id).where(User.router_id == rid)
    device_ids = select(Device.id).where(Device.router_id == rid)

    # 1. Rows hanging off this router's users / devices.
    counts["user_traffic_buckets"] = await _count(UserTrafficBucket, UserTrafficBucket.user_id.in_(user_ids))
    await session.execute(delete(UserTrafficBucket).where(UserTrafficBucket.user_id.in_(user_ids)))

    counts["traffic_rollups"] = await _count(TrafficRollup, TrafficRollup.user_id.in_(user_ids))
    await session.execute(delete(TrafficRollup).where(TrafficRollup.user_id.in_(user_ids)))

    counts["device_traffic_rollups"] = await _count(DeviceTrafficRollup, DeviceTrafficRollup.device_id.in_(device_ids))
    await session.execute(delete(DeviceTrafficRollup).where(DeviceTrafficRollup.device_id.in_(device_ids)))
    counts["device_traffic_buckets"] = await _count(DeviceTrafficBucket, DeviceTrafficBucket.device_id.in_(device_ids))
    await session.execute(delete(DeviceTrafficBucket).where(DeviceTrafficBucket.device_id.in_(device_ids)))

    counts["device_history"] = await _count(DeviceHistory, DeviceHistory.device_id.in_(device_ids))
    await session.execute(delete(DeviceHistory).where(DeviceHistory.device_id.in_(device_ids)))

    counts["user_destination_stats"] = await _count(
        UserDestinationStat,
        UserDestinationStat.user_id.in_(user_ids) | UserDestinationStat.device_id.in_(device_ids),
    )
    await session.execute(
        delete(UserDestinationStat).where(
            UserDestinationStat.user_id.in_(user_ids) | UserDestinationStat.device_id.in_(device_ids)
        )
    )

    # An adapter on another router must not be left pointing at a device that
    # is about to disappear.
    await session.execute(
        update(Device)
        .where(Device.linked_to_device_id.in_(device_ids))
        .values(linked_to_device_id=None)
    )

    # 2. The users and devices themselves.
    counts["devices"] = await _count(Device, Device.router_id == rid)
    await session.execute(delete(Device).where(Device.router_id == rid))

    counts["users"] = await _count(User, User.router_id == rid)
    await session.execute(delete(User).where(User.router_id == rid))

    # 3. Router-level series and logs.
    for model in (
        RouterTrafficRollup,
        InterfaceTrafficRollup,
        RouterSelfTrafficRollup,
        SystemMetric,
        InterfaceMetric,
        SpeedTestResult,
    ):
        counts[model.__tablename__] = await _count(model, model.router_id == rid)
        await session.execute(delete(model).where(model.router_id == rid))

    counts["alert_logs"] = await _count(AlertLog, AlertLog.router_id == rid)
    await session.execute(delete(AlertLog).where(AlertLog.router_id == rid))

    counts["router_logs"] = await _count(RouterLog, RouterLog.router_id == rid)
    await session.execute(delete(RouterLog).where(RouterLog.router_id == rid))

    # Backups own files on disk; drop those before the rows that name them,
    # otherwise the archives are stranded with nothing pointing at them.
    backups = (
        await session.execute(select(RouterBackup).where(RouterBackup.router_id == rid))
    ).scalars().all()
    counts["router_backups"] = len(backups)
    for b in backups:
        if getattr(b, "backup_file_path", None):
            try:
                from backend.app.services.backup_service import BACKUP_STORAGE_DIR
                (Path(BACKUP_STORAGE_DIR) / b.backup_file_path).unlink(missing_ok=True)
            except Exception as err:
                logger.warning(f"Could not remove backup file for router {rid}: {err}")
    await session.execute(delete(RouterBackup).where(RouterBackup.router_id == rid))

    # 4. Per-router settings: every `<base>_<id>` key. The `_<id>` suffix with a
    #    literal underscore is how all router-scoped settings are written, so an
    #    exact-suffix LIKE catches them without touching `<base>_<other id>`.
    like_pat = f"%\\_{rid}"
    counts["app_settings"] = await _count(AppSetting, AppSetting.key.like(like_pat, escape="\\"))
    await session.execute(delete(AppSetting).where(AppSetting.key.like(like_pat, escape="\\")))

    # 5. The router row.
    await session.delete(router)
    await session.flush()
    await reassign_default_if_needed(session)

    logger.info("Purged router %s: %s", rid, {k: v for k, v in counts.items() if v})
    return counts


async def reset_hardware_history(session: AsyncSession, router_id: int) -> Dict[str, int]:
    """Drop the health series that belong to specific silicon, keep the rest.

    Used on a hardware swap when the operator does not want the new box's CPU,
    temperature and interface graphs to continue the dead one's line. Gateway
    and per-user/-device traffic totals are deliberately left alone - the
    internet line and the billing cycle did not change.
    """
    counts: Dict[str, int] = {}
    for model in (SystemMetric, InterfaceMetric, SpeedTestResult):
        counts[model.__tablename__] = int(
            (await session.execute(
                select(func.count()).select_from(model).where(model.router_id == router_id)
            )).scalar_one()
        )
        await session.execute(delete(model).where(model.router_id == router_id))
    return counts
