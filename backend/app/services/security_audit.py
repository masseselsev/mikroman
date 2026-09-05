"""Reports RouterOS management services that accept connections from anywhere.

``/ip/service`` entries carry an optional ``address`` field: a list of source
prefixes allowed to reach that service. Left empty - which is the RouterOS
default - the service answers any source address that can route to the router.

Whether that is actually dangerous depends on reachability, which this cannot
know: a box behind CGNAT with no port forward is not reachable from the
internet at all. So the finding is stated as what it is - "no source-address
restriction" - rather than as "exposed to the internet", and the operator
decides. Narrowing ``address`` is cheap and is the right default either way.
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import AlertLog

logger = logging.getLogger("mikroman.security_audit")

ALERT_TYPE = "open_management_service"

# Re-alerting every poll tick would bury the log. Once a day is enough for a
# condition that only changes when someone edits the router.
ALERT_COOLDOWN_HOURS = 24

# Anything that grants management or shell access. `www`/`www-ssl` are included
# because that is the REST API MikroMan itself talks to.
MANAGEMENT_SERVICES = {"api", "api-ssl", "ftp", "ssh", "telnet", "winbox", "www", "www-ssl"}

# Values RouterOS shows for "no restriction".
UNRESTRICTED = {"", "0.0.0.0/0", "::/0"}


def find_unrestricted_services(services: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enabled management services with no source-address restriction.

    ``services`` is the raw ``/ip/service`` list. A disabled service is not a
    finding - it answers nobody - and a service outside
    :data:`MANAGEMENT_SERVICES` is not one either.
    """
    findings: List[Dict[str, Any]] = []
    for svc in services or []:
        name = str(svc.get("name") or "").strip().lower()
        if name not in MANAGEMENT_SERVICES:
            continue
        if str(svc.get("disabled", "false")).lower() == "true":
            continue

        address = str(svc.get("address") or "").strip()
        # RouterOS returns a comma-separated list; any all-zero prefix in it
        # makes the whole restriction meaningless.
        parts = {p.strip() for p in address.split(",")} if address else {""}
        if parts & UNRESTRICTED:
            findings.append({
                "name": name,
                "port": str(svc.get("port") or ""),
                "address": address,
            })

    # RouterOS 7.24 returns one row per listening socket, not one per service:
    # a router with several addresses shows `www-ssl` five times and `winbox`
    # twice, each with its own `.id`. They are the same finding to an operator,
    # so collapse them by name and port - otherwise the alert reads
    # "www-ssl:443, www-ssl:443, www-ssl:443, ..." and buries the rest.
    unique: Dict[tuple, Dict[str, Any]] = {}
    for f in findings:
        unique.setdefault((f["name"], f["port"]), f)
    return list(unique.values())


async def audit_router_services(client) -> List[Dict[str, Any]]:
    """Read ``/ip/service`` off the router and return the findings."""
    try:
        async with client._get_client() as http:
            resp = await http.get("/ip/service")
            if resp.status_code != 200:
                return []
            raw = resp.json()
            services = raw if isinstance(raw, list) else [raw]
    except Exception as e:
        logger.debug(f"Could not read /ip/service for the security audit: {e}")
        return []
    return find_unrestricted_services(services)


async def _alerted_recently(session: AsyncSession, router_id: Optional[int]) -> bool:
    since = datetime.now() - timedelta(hours=ALERT_COOLDOWN_HOURS)
    row = (await session.execute(
        select(AlertLog.id).where(
            AlertLog.alert_type == ALERT_TYPE,
            AlertLog.router_id == router_id,
            AlertLog.created_at >= since,
        ).limit(1)
    )).first()
    return row is not None


async def check_and_alert(session: AsyncSession, router_id: Optional[int], client) -> List[Dict[str, Any]]:
    """Audit the router and record one alert per day while findings persist.

    Returns the findings so a caller can also surface them directly.
    """
    findings = await audit_router_services(client)
    if not findings or await _alerted_recently(session, router_id):
        return findings

    listed = ", ".join(
        f"{f['name']}{':' + f['port'] if f['port'] else ''}" for f in findings
    )
    session.add(AlertLog(
        router_id=router_id,
        alert_type=ALERT_TYPE,
        message=(
            f"Management services accept connections from any source address: {listed}. "
            f"Set /ip/service address= to the networks that should reach them."
        ),
        metadata_payload={"services": findings},
    ))
    await session.commit()
    logger.warning(f"Router {router_id}: unrestricted management services: {listed}")
    return findings
