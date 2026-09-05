"""Turns live conntrack samples into the persistent per-destination history.

``UserDestinationStat`` backs the "Destinations & Domains" tab, but nothing was
writing to it: the connection tracker read ``/ip/firewall/connection`` for the
live modal and threw the numbers away when the request finished, so the tab was
permanently empty.

RouterOS reports **cumulative** byte counters per connection, not deltas, so
this service keeps the previous sample of every connection it has seen and
records the difference. A connection id RouterOS has recycled shows a counter
that went *down*; that is treated as a fresh connection rather than as a
negative delta.

The state is deliberately in-memory. Losing it on restart costs at most one
sample's worth of bytes, which is not worth a table.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import Device, User, UserDestinationStat
from backend.app.services.geoip import resolve_ip_location

logger = logging.getLogger("mikroman.destinations")

# A destination row nobody has touched in this long is dropped. The tab reads
# "where has this user been going lately", not "ever".
DESTINATION_RETENTION_DAYS = 90


def _endpoint_ip(addr: Optional[str]) -> str:
    """The address half of a RouterOS ``address:port`` endpoint, IPv6-safe."""
    if not addr:
        return ""
    raw = str(addr).strip()
    if raw.startswith("["):
        end = raw.find("]")
        return raw[1:end] if end != -1 else raw.lstrip("[")
    if raw.count(":") == 1:
        host, _, port = raw.partition(":")
        return host if port.isdigit() else raw
    return raw


class DestinationCollector:
    """Accumulates per-destination volume and hit counts from conntrack samples."""

    def __init__(self) -> None:
        # (router_id, connection id) -> (orig_bytes, repl_bytes) at last sample
        self._seen: Dict[Tuple[int, str], Tuple[int, int]] = {}

    def forget_router(self, router_id: int) -> None:
        """Drop cached counters for a router that is gone or was replaced."""
        for key in [k for k in self._seen if k[0] == router_id]:
            self._seen.pop(key, None)

    async def sample_router(
        self,
        session: AsyncSession,
        router_id: int,
        client,
    ) -> int:
        """Fold one conntrack sample into ``UserDestinationStat``.

        Returns the number of destination rows created or updated. Every failure
        path is non-fatal: this runs on a background tick and must never take
        the tick down with it.
        """
        try:
            raw_conns: List[dict] = await client.get_active_connections()
        except Exception as e:
            logger.debug(f"Destination sample skipped for router {router_id}: {e}")
            return 0
        if not raw_conns:
            return 0

        try:
            dns_cache = await client.get_dns_cache_entries()
        except Exception:
            dns_cache = {}

        # LAN address -> (device, owning user)
        dev_rows = (await session.execute(
            select(Device, User)
            .outerjoin(User, Device.user_id == User.id)
            .where((Device.router_id == router_id) | (Device.router_id.is_(None)))
        )).all()
        ip_map = {
            dev.ip_address.strip(): (dev, usr)
            for dev, usr in dev_rows
            if dev.ip_address
        }

        # (user_id, device_id, destination_ip) -> [bytes_in, bytes_out, hits, domain]
        pending: Dict[Tuple[Optional[int], Optional[int], str], List] = {}
        live_keys = set()

        for raw in raw_conns:
            conn_id = str(raw.get(".id") or "")
            if not conn_id:
                continue
            src_ip = _endpoint_ip(raw.get("src-address"))
            dst_ip = _endpoint_ip(raw.get("dst-address"))

            # Only traffic originated by a device we know about is attributable.
            attribution = ip_map.get(src_ip)
            if not attribution:
                continue
            device, user = attribution
            if resolve_ip_location(dst_ip).country_code == "LOCAL":
                continue  # LAN-to-LAN is not a "destination"

            orig = int(raw.get("orig-bytes") or 0)
            repl = int(raw.get("repl-bytes") or 0)

            key = (router_id, conn_id)
            live_keys.add(key)
            prev = self._seen.get(key)
            if prev is None:
                # First sighting: count the hit, and take the counters as-is -
                # the connection may already have been running for a while.
                d_out, d_in, hit = orig, repl, 1
            else:
                p_orig, p_repl = prev
                if orig < p_orig or repl < p_repl:
                    # Counter went backwards: RouterOS recycled the id.
                    d_out, d_in, hit = orig, repl, 1
                else:
                    d_out, d_in, hit = orig - p_orig, repl - p_repl, 0
            self._seen[key] = (orig, repl)

            if not (d_in or d_out or hit):
                continue

            # `orig` is what the originator sent (upload); `repl` what came back.
            agg_key = (user.id if user else None, device.id, dst_ip)
            slot = pending.setdefault(agg_key, [0, 0, 0, None])
            slot[0] += d_in
            slot[1] += d_out
            slot[2] += hit
            slot[3] = slot[3] or dns_cache.get(dst_ip)

        # Forget connections this router no longer reports, so the cache tracks
        # the conntrack table rather than growing forever.
        for key in [k for k in self._seen if k[0] == router_id and k not in live_keys]:
            self._seen.pop(key, None)

        if not pending:
            return 0

        now = datetime.now(timezone.utc)
        touched = 0
        for (user_id, device_id, dest_ip), (b_in, b_out, hits, domain) in pending.items():
            row = (await session.execute(
                select(UserDestinationStat).where(
                    UserDestinationStat.user_id.is_(None) if user_id is None
                    else UserDestinationStat.user_id == user_id,
                    UserDestinationStat.device_id == device_id,
                    UserDestinationStat.destination_ip == dest_ip,
                )
            )).scalars().first()

            if row is None:
                session.add(UserDestinationStat(
                    user_id=user_id,
                    device_id=device_id,
                    destination_ip=dest_ip,
                    domain=domain,
                    country_code=resolve_ip_location(dest_ip).country_code,
                    bytes_in=b_in,
                    bytes_out=b_out,
                    total_bytes=b_in + b_out,
                    hit_count=max(1, hits),
                    last_seen=now,
                ))
            else:
                row.bytes_in += b_in
                row.bytes_out += b_out
                row.total_bytes = row.bytes_in + row.bytes_out
                row.hit_count += hits
                row.last_seen = now
                # A destination often has no name on its first sighting and
                # gains one once the client's DNS lookup lands in the cache.
                if domain and not row.domain:
                    row.domain = domain
            touched += 1

        await session.commit()
        return touched

    @staticmethod
    async def prune(session: AsyncSession, retention_days: int = DESTINATION_RETENTION_DAYS) -> int:
        """Drop destination rows untouched for longer than the retention window."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        res = await session.execute(
            delete(UserDestinationStat).where(UserDestinationStat.last_seen < cutoff)
        )
        removed = res.rowcount or 0
        if removed:
            await session.commit()
            logger.info(f"Pruned {removed} stale destination rows")
        return removed


destination_collector = DestinationCollector()
