"""Background log scraper and maintenance service for RouterOS devices."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import RouterLog
from backend.app.services.log_classifier import classify_log_entry
from backend.app.services.routeros.client import RouterOSClient

logger = logging.getLogger("mikroman.log_collector")

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_routeros_log_time(time_str: str, now: Optional[datetime] = None) -> datetime:
    """Parse various RouterOS timestamp formats into a datetime object.

    Formats seen in RouterOS v7:
    - '2026-09-04 12:00:00'
    - 'sep/04/2026 12:00:00'
    - 'sep/04 12:00:00'
    - '12:00:00'
    """
    ref = now or datetime.now()
    raw = (time_str or "").strip()
    if not raw:
        return ref

    # ISO-like format: '2026-09-04 12:00:00'
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass

    # 'sep/04/2026 12:00:00'
    try:
        parts = raw.split(" ")
        if len(parts) == 2:
            d_parts = parts[0].split("/")
            if len(d_parts) == 3:
                month = MONTH_MAP.get(d_parts[0].lower(), 1)
                day = int(d_parts[1])
                year = int(d_parts[2])
                h, m, s = [int(x) for x in parts[1].split(":")]
                return datetime(year, month, day, h, m, s)
            elif len(d_parts) == 2:
                # 'sep/04 12:00:00' -> current year
                month = MONTH_MAP.get(d_parts[0].lower(), 1)
                day = int(d_parts[1])
                h, m, s = [int(x) for x in parts[1].split(":")]
                return datetime(ref.year, month, day, h, m, s)
    except Exception:
        pass

    # '12:00:00' -> today
    try:
        parts = raw.split(":")
        if len(parts) == 3:
            h, m, s = [int(x) for x in parts]
            return datetime(ref.year, ref.month, ref.day, h, m, s)
    except Exception:
        pass

    return ref


class LogCollector:
    """Collects and stores logs from RouterOS devices."""

    async def collect_logs_for_router(
        self,
        session: AsyncSession,
        router_id: int,
        client: RouterOSClient,
        limit: int = 500,
    ) -> int:
        """Fetch latest logs from RouterOS and ingest unseen rows into SQLite."""
        try:
            raw_entries: List[Dict[str, Any]] = await client.get_logs(limit=limit)
        except Exception as e:
            logger.warning("Failed to scrape logs from router %s: %s", router_id, e)
            return 0

        if not raw_entries:
            return 0

        # Collect external IDs from incoming batch
        incoming_ext_ids = [e.get(".id") for e in raw_entries if e.get(".id")]
        existing_ids = set()

        if incoming_ext_ids:
            query = select(RouterLog.external_id).where(
                RouterLog.router_id == router_id,
                RouterLog.external_id.in_(incoming_ext_ids),
            )
            result = await session.execute(query)
            existing_ids = set(result.scalars().all())

        now = datetime.now()
        new_models: List[RouterLog] = []

        for entry in raw_entries:
            ext_id = entry.get(".id")
            if ext_id and ext_id in existing_ids:
                continue

            time_str = entry.get("time", "")
            topics = entry.get("topics", "")
            message = entry.get("message", "")

            parsed_time = parse_routeros_log_time(time_str, now=now)
            sev, cat = classify_log_entry(topics, message)

            new_models.append(
                RouterLog(
                    router_id=router_id,
                    external_id=ext_id,
                    timestamp=parsed_time,
                    topics=topics,
                    message=message,
                    severity=sev,
                    category=cat,
                )
            )

        if new_models:
            session.add_all(new_models)
            await session.commit()
            logger.info("Ingested %d new log entries for router %s", len(new_models), router_id)

        return len(new_models)

    async def prune_old_logs(
        self,
        session: AsyncSession,
        router_id: int,
        retention_days: int = 14,
        max_records: int = 10000,
    ) -> int:
        """Delete log entries older than retention window or exceeding max records."""
        cutoff = datetime.now() - timedelta(days=retention_days)

        # 1. Delete expired logs
        stmt = delete(RouterLog).where(
            RouterLog.router_id == router_id,
            RouterLog.timestamp < cutoff,
        )
        res = await session.execute(stmt)
        deleted = res.rowcount or 0

        # 2. Check if remaining records exceed max_records
        count_q = select(RouterLog.id).where(RouterLog.router_id == router_id).order_by(RouterLog.timestamp.desc())
        all_ids = (await session.execute(count_q)).scalars().all()

        if len(all_ids) > max_records:
            excess_ids = all_ids[max_records:]
            del_stmt = delete(RouterLog).where(RouterLog.id.in_(excess_ids))
            res_excess = await session.execute(del_stmt)
            deleted += res_excess.rowcount or 0

        if deleted > 0:
            await session.commit()
            logger.info("Pruned %d old log entries for router %s", deleted, router_id)

        return deleted
