"""API endpoints for live firewall connection tracking and termination."""

import logging
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import Device, User
from backend.app.db.session import get_db
from backend.app.schemas.common import APIResponse, PaginatedResponse
from backend.app.schemas.connection import KillConnectionRequest, LiveConnectionItem
from backend.app.services.geoip import resolve_ip_location
from backend.app.services.guards import WriteGuardViolation
from backend.app.services.router_manager import router_manager

logger = logging.getLogger("mikroman.connections")

router = APIRouter(prefix="/connections", tags=["Connections"])


def _split_endpoint(addr_str: Optional[str]) -> Tuple[str, Optional[int]]:
    """Split a RouterOS ``address:port`` endpoint into its two halves.

    RouterOS writes IPv4 endpoints as ``1.2.3.4:443`` and IPv6 ones as
    ``[2001:db8::1]:443`` (or bare, with no port). Splitting unconditionally on
    the last colon mangled every bare IPv6 address into ``2001:db8:``, which
    then failed both the geo lookup and the device attribution.
    """
    if not addr_str:
        return "", None
    raw = str(addr_str).strip()

    if raw.startswith("["):
        end = raw.find("]")
        if end == -1:
            return raw.lstrip("["), None
        host = raw[1:end]
        rest = raw[end + 1:]
        if rest.startswith(":") and rest[1:].isdigit():
            return host, int(rest[1:])
        return host, None

    # Exactly one colon means IPv4 + port; two or more means a bare IPv6 address.
    if raw.count(":") == 1:
        host, _, port = raw.partition(":")
        return (host, int(port)) if port.isdigit() else (raw, None)

    return raw, None


@router.get("", response_model=APIResponse[PaginatedResponse[LiveConnectionItem]])
async def get_live_connections(
    router_id: Optional[int] = Query(None, description="Target router ID"),
    device_id: Optional[int] = Query(None, description="Filter by device ID"),
    user_id: Optional[int] = Query(None, description="Filter by user ID"),
    protocol: Optional[str] = Query(None, description="Filter by protocol (tcp, udp, icmp)"),
    search: Optional[str] = Query(None, description="Filter by IP, domain, or device name"),
    limit: int = Query(250, ge=1, le=1000, description="Max connections to return"),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve active firewall connections enriched with Geo-IP and device attribution.

    A router-unreachable or fetch failure is not caught here: it used to be,
    landing as a silent `200 OK` with `data: []`. The polling UI cannot tell
    that apart from "genuinely zero connections right now" and replaces its
    list wholesale on every poll, so a single transient failure (the client's
    circuit breaker, a slow response while the conntrack table is large) wiped
    the table for a cycle before the next poll quietly repopulated it - with no
    error shown, because none was ever raised. Letting the failure propagate
    (the same convention every other router-backed endpoint already follows)
    gives the frontend a real error to catch, so it keeps the last good list
    on screen instead of pretending there is nothing to show.
    """
    client = await router_manager.require_client(session=db, router_id=router_id)
    raw_conns = await client.get_active_connections()

    try:
        dns_cache = await client.get_dns_cache_entries()
    except Exception:
        dns_cache = {}

    immune_ips = client.get_immune_ips() if hasattr(client, "get_immune_ips") else set()

    # Build IP to Device/User mapping from SQLite
    dev_stmt = select(Device, User).outerjoin(User, Device.user_id == User.id)
    if router_id is not None:
        dev_stmt = dev_stmt.where((Device.router_id == router_id) | (Device.router_id.is_(None)))
    dev_rows = (await db.execute(dev_stmt)).all()

    ip_map: Dict[str, Tuple[Device, Optional[User]]] = {}
    for dev, usr in dev_rows:
        if dev.ip_address:
            ip_map[dev.ip_address.strip()] = (dev, usr)

    search_lower = search.strip().lower() if search else None
    proto_lower = protocol.strip().lower() if protocol else None

    items: List[LiveConnectionItem] = []
    matched = 0
    for raw in raw_conns:
        src_str = raw.get("src-address") or ""
        dst_str = raw.get("dst-address") or ""
        src_ip, src_port = _split_endpoint(src_str)
        dst_ip, dst_port = _split_endpoint(dst_str)
        proto = str(raw.get("protocol") or "unknown").lower()

        # Check protocol filter
        if proto_lower and proto != proto_lower:
            continue

        # Determine source device attribution
        dev_info = ip_map.get(src_ip) or ip_map.get(dst_ip)
        dev_obj, usr_obj = dev_info if dev_info else (None, None)

        if device_id is not None and (not dev_obj or dev_obj.id != device_id):
            continue
        if user_id is not None and (not usr_obj or usr_obj.id != user_id):
            continue

        dev_name = dev_obj.custom_name or dev_obj.hostname if dev_obj else None
        user_name = usr_obj.name if usr_obj else None

        # Geo-IP & domain resolution for the remote endpoint.
        # The remote half is whichever side is not on this LAN. Asking the geo
        # engine (which knows every RFC1918/link-local/loopback range) is right
        # where the old `startswith("192.168.")` test was wrong for the 10/8 and
        # 172.16/12 networks MikroMan also supports.
        dst_geo = resolve_ip_location(dst_ip)
        if dst_geo.country_code != "LOCAL":
            remote_ip, geo = dst_ip, dst_geo
        elif resolve_ip_location(src_ip).country_code != "LOCAL":
            remote_ip, geo = src_ip, resolve_ip_location(src_ip)
        else:
            # LAN-to-LAN: keep the destination as the "remote" end.
            remote_ip, geo = dst_ip, dst_geo
        domain = dns_cache.get(remote_ip)

        # Check search match
        if search_lower:
            haystack = f"{src_ip} {dst_ip} {domain or ''} {dev_name or ''} {user_name or ''} {geo.country_name} {geo.country_code}".lower()
            if search_lower not in haystack:
                continue

        # This connection passes every filter - it counts toward the total
        # regardless of whether it also fits under `limit`, so a truncated
        # response can still say honestly how much was left out (the header
        # badge used to just print `len(items)`, which reads as "there are
        # exactly 250 connections" even on a router carrying several times
        # that many).
        matched += 1
        if len(items) >= limit:
            continue

        # Rates and byte counts
        orig_rate = int(raw.get("orig-rate") or 0)
        repl_rate = int(raw.get("repl-rate") or 0)
        orig_bytes = int(raw.get("orig-bytes") or 0)
        repl_bytes = int(raw.get("repl-bytes") or 0)

        is_immune = src_ip in immune_ips or dst_ip in immune_ips

        item = LiveConnectionItem(
            id=str(raw.get(".id") or ""),
            protocol=proto,
            src_ip=src_ip,
            src_port=src_port,
            dst_ip=dst_ip,
            dst_port=dst_port,
            device_id=dev_obj.id if dev_obj else None,
            device_name=dev_name,
            user_id=usr_obj.id if usr_obj else None,
            user_name=user_name,
            domain=domain,
            country_code=geo.country_code,
            country_name=geo.country_name,
            flag_emoji=geo.flag_emoji,
            tcp_state=raw.get("tcp-state"),
            orig_rate=orig_rate,
            repl_rate=repl_rate,
            orig_bytes=orig_bytes,
            repl_bytes=repl_bytes,
            total_bytes=orig_bytes + repl_bytes,
            timeout=raw.get("timeout"),
            is_immune=is_immune,
        )
        items.append(item)

    return APIResponse(data=PaginatedResponse(total=matched, items=items))


@router.post("/{connection_id}/kill", response_model=APIResponse[bool])
async def kill_connection(
    connection_id: str,
    payload: KillConnectionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Terminate an active firewall connection on RouterOS, guarded against immune targets."""
    client = await router_manager.require_client(session=db, router_id=payload.router_id)
    try:
        await client.remove_firewall_connection(
            connection_id=connection_id,
            src_ip=payload.src_ip,
            dst_ip=payload.dst_ip,
        )
    except WriteGuardViolation as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to kill connection {connection_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to kill connection: {e}")

    return APIResponse(data=True, message="Connection terminated")

