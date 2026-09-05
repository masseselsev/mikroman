from __future__ import annotations

import logging
import socket
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import Router, RouterLog
from backend.app.db.session import get_db
from backend.app.schemas.common import APIResponse
from backend.app.schemas.log import (
    CreateLoggingRuleRequest,
    LoggingRuleItem,
    RouterLogItem,
    RouterLogStats,
)
from backend.app.services.guards import WriteGuardViolation
from backend.app.services.log_classifier import classify_log_entry, is_self_api_login
from backend.app.services.log_collector import parse_routeros_log_time
from backend.app.services.router_manager import router_manager
from backend.app.services.routeros.client import RouterOSClient

logger = logging.getLogger("mikroman.api.logs")
router = APIRouter(prefix="/logs", tags=["Logs"])


async def get_client_for_router(session: AsyncSession, router_id: Optional[int] = None) -> RouterOSClient:
    """Helper to acquire RouterOSClient for router_id or active router."""
    return await router_manager.require_client(session=session, router_id=router_id)


def _local_ip_toward(host: str, port: int) -> Optional[str]:
    """The address this container's traffic to ``host:port`` leaves from.

    A UDP "connect" only asks the kernel to pick a route - it sends nothing on
    the wire - so this is a synchronous, effectively instant way to answer
    "what IP does the router see me as" without depending on anything RouterOS
    itself reports back.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, port))
            return s.getsockname()[0]
    except OSError as e:
        logger.debug(f"Could not determine the local IP toward {host}:{port}: {e}")
        return None


async def _own_login_signature(session: AsyncSession, router_id: int) -> Tuple[Optional[str], Optional[str]]:
    """(username, own_ip) MikroMan's own REST logins to this router carry.

    Used only to *hide* clutter from the log viewer, never to decide anything
    that touches the router - the write guards remain the actual authority on
    what is safe to change.
    """
    r = await session.get(Router, router_id)
    if not r or not r.username:
        return None, None
    return r.username, _local_ip_toward(r.host, r.port)


async def _resolve_router_id(session: AsyncSession, router_id: Optional[int]) -> Optional[int]:
    if router_id is not None:
        return router_id
    r_stmt = select(Router.id).where(Router.is_active.is_(True)).limit(1)
    return (await session.execute(r_stmt)).scalar_one_or_none()


@router.get("", response_model=APIResponse[List[RouterLogItem]])
async def get_logs(
    router_id: Optional[int] = Query(None, description="Target router ID"),
    source: str = Query("db", pattern="^(db|live)$", description="Data source: 'db' or 'live'"),
    category: Optional[str] = Query(None, description="Category filter (auth, interface, dhcp, wireless, firewall, system)"),
    severity: Optional[str] = Query(None, description="Severity filter (info, warning, error, critical)"),
    search: Optional[str] = Query(None, description="Substring search in message or topics"),
    hide_self_api: bool = Query(
        False, description="Hide MikroMan's own REST login/logout lines (same account, same source address)"
    ),
    limit: int = Query(250, ge=1, le=1000, description="Max entries to return"),
    db: AsyncSession = Depends(get_db),
):
    """Fetch router logs from SQLite history or live from the RouterOS device."""
    target_router_id = await _resolve_router_id(db, router_id)
    if not target_router_id:
        return APIResponse(data=[], message="No active router found")

    own_username, own_ip = (
        await _own_login_signature(db, target_router_id) if hide_self_api else (None, None)
    )

    if source == "live":
        try:
            client = await get_client_for_router(db, target_router_id)
            raw_entries = await client.get_logs(limit=limit)
        except Exception as e:
            logger.warning("Failed to fetch live logs: %s", e)
            return APIResponse(data=[], message=str(e))

        items: List[RouterLogItem] = []
        now = datetime.now()
        search_lower = (search or "").lower()

        for entry in raw_entries:
            topics = entry.get("topics", "")
            message = entry.get("message", "")
            time_str = entry.get("time", "")

            sev, cat = classify_log_entry(topics, message)

            if category and cat.lower() != category.lower():
                continue
            if severity and sev.lower() != severity.lower():
                continue
            if search_lower and search_lower not in topics.lower() and search_lower not in message.lower():
                continue
            if hide_self_api and is_self_api_login(message, own_username, own_ip):
                continue

            parsed_time = parse_routeros_log_time(time_str, now=now)
            items.append(
                RouterLogItem(
                    id=None,
                    router_id=target_router_id,
                    external_id=entry.get(".id"),
                    timestamp=parsed_time,
                    topics=topics,
                    message=message,
                    severity=sev,
                    category=cat,
                )
            )
        return APIResponse(data=items[-limit:])

    # source == "db"
    stmt = select(RouterLog).where(RouterLog.router_id == target_router_id)

    if category:
        stmt = stmt.where(RouterLog.category == category.lower())
    if severity:
        stmt = stmt.where(RouterLog.severity == severity.lower())
    if search:
        s_term = f"%{search.strip()}%"
        stmt = stmt.where((RouterLog.message.ilike(s_term)) | (RouterLog.topics.ilike(s_term)))

    # Filtering self-logins happens in Python (see `is_self_api_login`), after
    # the query - it is one predicate shared with the live branch above rather
    # than a second copy re-expressed as SQL `LIKE` patterns, which would drift
    # from the regex the moment either one changed. Over-fetching leaves room
    # for that filter to still return a full page of `limit` rows.
    stmt = stmt.order_by(RouterLog.timestamp.desc()).limit(limit * 3 if hide_self_api else limit)
    rows = (await db.execute(stmt)).scalars().all()

    if hide_self_api:
        rows = [r for r in rows if not is_self_api_login(r.message, own_username, own_ip)][:limit]

    # Return newest at bottom (chronological order) for terminal display
    return APIResponse(data=[RouterLogItem.model_validate(r) for r in reversed(rows)])


@router.get("/stats", response_model=APIResponse[RouterLogStats])
async def get_log_stats(
    router_id: Optional[int] = Query(None, description="Target router ID"),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve 24h summary statistics for router logs."""
    target_router_id = await _resolve_router_id(db, router_id)
    if not target_router_id:
        return APIResponse(
            data=RouterLogStats(
                router_id=0, total_logs=0, critical_count=0, error_count=0, warning_count=0, auth_failures_count=0
            )
        )

    since = datetime.now() - timedelta(days=1)
    base_q = select(RouterLog).where(
        RouterLog.router_id == target_router_id,
        RouterLog.timestamp >= since,
    )

    total = (await db.execute(select(func.count()).select_from(base_q.subquery()))).scalar_one() or 0
    crit = (await db.execute(select(func.count()).select_from(base_q.where(RouterLog.severity == "critical").subquery()))).scalar_one() or 0
    err = (await db.execute(select(func.count()).select_from(base_q.where(RouterLog.severity == "error").subquery()))).scalar_one() or 0
    warn = (await db.execute(select(func.count()).select_from(base_q.where(RouterLog.severity == "warning").subquery()))).scalar_one() or 0
    auth_fail = (await db.execute(select(func.count()).select_from(base_q.where(RouterLog.category == "auth", RouterLog.severity.in_(["critical", "error"])).subquery()))).scalar_one() or 0

    return APIResponse(
        data=RouterLogStats(
            router_id=target_router_id,
            total_logs=total,
            critical_count=crit,
            error_count=err,
            warning_count=warn,
            auth_failures_count=auth_fail,
        )
    )


@router.get("/rules", response_model=APIResponse[List[LoggingRuleItem]])
async def get_logging_rules(
    router_id: Optional[int] = Query(None, description="Target router ID"),
    db: AsyncSession = Depends(get_db),
):
    """List configured `/system/logging` actions on the router."""
    client = await get_client_for_router(db, router_id)
    raw_rules = await client.get_logging_rules()

    items = []
    for r in raw_rules:
        c = (r.get("comment") or "").strip()
        items.append(
            LoggingRuleItem(
                id=r.get(".id", ""),
                topics=r.get("topics", ""),
                action=r.get("action", "memory"),
                prefix=r.get("prefix"),
                comment=c or None,
                is_managed=c.startswith("mikroman:"),
            )
        )
    return APIResponse(data=items)


@router.post("/rules", response_model=APIResponse[dict])
async def create_logging_rule(
    req: CreateLoggingRuleRequest,
    router_id: Optional[int] = Query(None, description="Target router ID"),
    db: AsyncSession = Depends(get_db),
):
    """Add a new topic rule to `/system/logging` on the router."""
    client = await get_client_for_router(db, router_id)
    rule_id = await client.add_logging_rule(topics=req.topics, action=req.action, prefix=req.prefix)
    return APIResponse(data={"id": rule_id}, message=f"Logging rule created for topics: {req.topics}")


@router.delete("/rules/{rule_id}", response_model=APIResponse[bool])
async def delete_logging_rule(
    rule_id: str,
    router_id: Optional[int] = Query(None, description="Target router ID"),
    db: AsyncSession = Depends(get_db),
):
    """Remove a logging rule from `/system/logging`. Only MikroMan-managed rules can be deleted."""
    client = await get_client_for_router(db, router_id)
    try:
        success = await client.remove_logging_rule(rule_id)
    except WriteGuardViolation as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete logging rule: {e}")

    if not success:
        raise HTTPException(status_code=404, detail="Rule not found or could not be removed")
    return APIResponse(data=True, message="Logging rule removed successfully")


@router.delete("", response_model=APIResponse[int])
async def clear_stored_logs(
    router_id: Optional[int] = Query(None, description="Target router ID"),
    db: AsyncSession = Depends(get_db),
):
    """Clear historical persisted logs from SQLite for a router."""
    target_router_id = await _resolve_router_id(db, router_id)
    if not target_router_id:
        return APIResponse(data=0, message="Router not found")

    res = await db.execute(delete(RouterLog).where(RouterLog.router_id == target_router_id))
    await db.commit()
    return APIResponse(data=res.rowcount or 0, message=f"Cleared {res.rowcount} logs from history")
