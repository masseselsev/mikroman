"""Firewall operations: address lists, filter rules, and mangle counters.

Two unrelated jobs share this menu. Address lists back the pause/block feature.
Mangle ``action=passthrough`` rules are how per-device volume is measured, after
Simple Queue counters were found frozen at zero on RouterOS 7.25 while the
forward chain accounted 243.8 MB against 246 MB of real traffic. Passthrough
only increments a counter and hands the packet on unchanged - it cannot drop,
alter or reroute anything.
"""
import logging
from typing import Any, Dict, List, Optional

from backend.app.services.guards import guard_foreign_resources, guard_immune_targets

logger = logging.getLogger("mikroman.routeros")


class FirewallMixin:
    """`/ip/firewall/*` operations for :class:`RouterOSClient`."""

    # --- Firewall Address List Operations (Pause / Block) ---

    async def get_address_list(self, list_name: str = "mikroman_blocked") -> List[Dict[str, Any]]:
        """Fetch entries from a firewall address-list."""
        async with self._get_client() as client:
            resp = await client.get("/ip/firewall/address-list")
            resp.raise_for_status()
            raw = resp.json()
            if not isinstance(raw, list):
                raw = [raw]
            return [item for item in raw if item.get("list") == list_name]

    async def add_to_address_list(self, address: str, list_name: str = "mikroman_blocked", comment: str = "mikroman:paused") -> str:
        """Add an IP to a firewall address-list.

        Idempotent: RouterOS answers a duplicate ``address``+``list`` pair with
        ``400 failure: already have such entry`` - that is the desired end
        state, not an error, so it is swallowed. This method is re-run on every
        pause save.
        """
        if list_name == "mikroman_blocked":
            immune = self.get_immune_ips() if hasattr(self, "get_immune_ips") else set()
            guard_immune_targets(address, immune, action="block")

        async with self._get_client() as client:
            payload = {"address": address, "list": list_name, "comment": comment}
            resp = await client.put("/ip/firewall/address-list", json=payload)
            if resp.status_code == 400 and "already have such entry" in resp.text:
                return ""
            resp.raise_for_status()
            return resp.json().get(".id", "")

    async def remove_from_address_list(self, entry_id: str, comment: Optional[str] = None) -> None:
        """Remove IP entry from address-list."""
        if comment is not None:
            guard_foreign_resources(comment, action="delete", resource_type="address-list")

        async with self._get_client() as client:
            resp = await client.delete(f"/ip/firewall/address-list/{entry_id}")
            resp.raise_for_status()

    async def get_firewall_filter_rules(self) -> List[Dict[str, Any]]:
        """Fetch firewall filter rules from RouterOS."""
        async with self._get_client() as client:
            resp = await client.get("/ip/firewall/filter")
            if resp.status_code != 200:
                return []
            raw = resp.json()
            return raw if isinstance(raw, list) else [raw]

    async def create_firewall_filter_rule(self, payload: Dict[str, Any]) -> str:
        """Create a firewall filter rule on RouterOS."""
        async with self._get_client() as client:
            resp = await client.put("/ip/firewall/filter", json=payload)
            resp.raise_for_status()
            return resp.json().get(".id", "")

    async def update_firewall_filter_rule(self, rule_id: str, payload: Dict[str, Any]) -> bool:
        """Update a firewall filter rule on RouterOS."""
        async with self._get_client() as client:
            resp = await client.patch(f"/ip/firewall/filter/{rule_id}", json=payload)
            return resp.status_code in (200, 201, 204)

    async def delete_firewall_filter_rule(self, rule_id: str, comment: Optional[str] = None) -> None:
        """Delete a firewall filter rule."""
        if comment is not None:
            guard_foreign_resources(comment, action="delete", resource_type="firewall-filter")

        async with self._get_client() as client:
            resp = await client.delete(f"/ip/firewall/filter/{rule_id}")
            resp.raise_for_status()

    async def move_firewall_filter_rule(self, rule_id: str, before_id: str) -> bool:
        """Place ``rule_id`` immediately before ``before_id`` in the chain.

        A drop rule left at the bottom of the forward chain sits after
        ``fasttrack-connection`` / ``accept established,related`` and is never
        reached. RouterOS 7's REST ``move`` wants rule ids, not position
        numbers, in both ``numbers`` and ``destination``.
        """
        if not rule_id or not before_id or rule_id == before_id:
            return False
        async with self._get_client() as client:
            try:
                resp = await client.post(
                    "/ip/firewall/filter/move",
                    json={"numbers": rule_id, "destination": before_id},
                )
                return resp.status_code in (200, 201, 204)
            except Exception:
                return False

    async def get_firewall_raw_rules(self) -> List[Dict[str, Any]]:
        """Fetch ``/ip/firewall/raw`` rules. The raw table runs before connection
        tracking and FastTrack, so a drop here stops even an already-established
        connection - which is what "pause this device now" needs."""
        async with self._get_client() as client:
            resp = await client.get("/ip/firewall/raw")
            if resp.status_code != 200:
                return []
            raw = resp.json()
            return raw if isinstance(raw, list) else [raw]

    async def create_firewall_raw_rule(self, payload: Dict[str, Any]) -> str:
        """Create an ``/ip/firewall/raw`` rule."""
        async with self._get_client() as client:
            resp = await client.put("/ip/firewall/raw", json=payload)
            resp.raise_for_status()
            return resp.json().get(".id", "")

    async def update_firewall_raw_rule(self, rule_id: str, payload: Dict[str, Any]) -> bool:
        """Update an ``/ip/firewall/raw`` rule."""
        async with self._get_client() as client:
            resp = await client.patch(f"/ip/firewall/raw/{rule_id}", json=payload)
            return resp.status_code in (200, 201, 204)

    async def delete_firewall_raw_rule(self, rule_id: str, comment: Optional[str] = None) -> None:
        """Delete an ``/ip/firewall/raw`` rule."""
        if comment is not None:
            guard_foreign_resources(comment, action="delete", resource_type="firewall-raw")

        async with self._get_client() as client:
            resp = await client.delete(f"/ip/firewall/raw/{rule_id}")
            resp.raise_for_status()

    # --- Firewall Mangle Operations (per-device traffic accounting) ---
    #
    # Simple Queue byte counters proved unusable for accounting on RouterOS 7.25
    # (they stay frozen at zero even while traffic flows), so per-device volume is
    # measured with `action=passthrough` mangle rules instead. Passthrough only
    # increments a counter and hands the packet on - it never alters traffic.

    async def get_mangle_rules(self) -> List[Dict[str, Any]]:
        """Fetch all firewall mangle rules from RouterOS."""
        async with self._get_client() as client:
            resp = await client.get("/ip/firewall/mangle")
            if resp.status_code != 200:
                return []
            raw = resp.json()
            return raw if isinstance(raw, list) else [raw]

    async def create_mangle_rule(self, payload: Dict[str, Any]) -> str:
        """Create a firewall mangle rule and return its RouterOS id."""
        async with self._get_client() as client:
            resp = await client.put("/ip/firewall/mangle", json=payload)
            resp.raise_for_status()
            return resp.json().get(".id", "")

    async def update_mangle_rule(self, rule_id: str, payload: Dict[str, Any]) -> bool:
        """Update an existing firewall mangle rule."""
        async with self._get_client() as client:
            resp = await client.patch(f"/ip/firewall/mangle/{rule_id}", json=payload)
            return resp.status_code in (200, 201, 204)

    async def delete_mangle_rule(self, rule_id: str) -> None:
        """Delete a firewall mangle rule."""
        async with self._get_client() as client:
            resp = await client.delete(f"/ip/firewall/mangle/{rule_id}")
            resp.raise_for_status()
