"""RouterOS live firewall connection tracking and DNS cache inspection."""

import logging
from typing import Any, Dict, List, Optional

from backend.app.services.guards import guard_immune_targets

logger = logging.getLogger("mikroman.routeros.connections")

DEFAULT_CONNECTION_PROPLIST = [
    ".id",
    "protocol",
    "src-address",
    "dst-address",
    "reply-src-address",
    "reply-dst-address",
    "tcp-state",
    "timeout",
    "orig-rate",
    "repl-rate",
    "orig-bytes",
    "repl-bytes",
    "assured",
    "fasttrack",
]


class ConnectionsMixin:
    """`/ip/firewall/connection` and `/ip/dns/cache` operations for :class:`RouterOSClient`."""

    async def get_active_connections(
        self, proplist: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Fetch active firewall connections with field limiting via .proplist."""
        fields = proplist or DEFAULT_CONNECTION_PROPLIST
        params = {".proplist": ",".join(fields)}
        async with self._get_client() as client:
            resp = await client.get("/ip/firewall/connection", params=params)
            resp.raise_for_status()
            raw = resp.json()
            if not isinstance(raw, list):
                raw = [raw] if raw else []
            return raw

    async def get_dns_cache_entries(self) -> Dict[str, str]:
        """Fetch DNS cache from RouterOS and return an IP -> Domain mapping."""
        async with self._get_client() as client:
            try:
                resp = await client.get("/ip/dns/cache")
                if resp.status_code == 404:
                    return {}
                resp.raise_for_status()
                raw = resp.json()
                if not isinstance(raw, list):
                    raw = [raw] if raw else []
                # Only address records carry an IP in `data`. A CNAME's `data`
                # is another hostname, which would otherwise be indexed as if it
                # were an address and never match a connection endpoint.
                dns_map: Dict[str, str] = {}
                for entry in raw:
                    rec_type = str(entry.get("type") or "A").strip().upper()
                    if rec_type not in ("A", "AAAA"):
                        continue
                    ip = entry.get("data") or entry.get("address")
                    name = entry.get("name")
                    if ip and name:
                        dns_map[str(ip).strip()] = str(name).strip()
                return dns_map
            except Exception as e:
                logger.debug(f"Could not read RouterOS DNS cache: {e}")
                return {}

    async def remove_firewall_connection(
        self,
        connection_id: str,
        src_ip: Optional[str] = None,
        dst_ip: Optional[str] = None,
    ) -> bool:
        """Forcibly remove an active firewall connection.

        Guarded against removing management sessions to immune hosts.
        """
        immune = self.get_immune_ips() if hasattr(self, "get_immune_ips") else set()
        if src_ip:
            guard_immune_targets(src_ip, immune, action="kill_connection")
        if dst_ip:
            guard_immune_targets(dst_ip, immune, action="kill_connection")

        clean_id = connection_id.strip()
        async with self._get_client() as client:
            # RouterOS REST accepts POST /ip/firewall/connection/remove with {"numbers": id}
            resp = await client.post("/ip/firewall/connection/remove", json={"numbers": clean_id})
            if resp.status_code in (400, 404):
                text = resp.text.lower()
                if "no such item" in text or "already" in text:
                    return True
            resp.raise_for_status()
            return True
