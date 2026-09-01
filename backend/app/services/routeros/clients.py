"""Who is on the network: DHCP leases, ARP entries, wireless associations.

Three partial views of the same question, deliberately kept separate rather than
pre-merged: a device may hold a lease without being in ARP, appear in ARP with a
static address and no lease, or be associated to a radio under an ``mld*`` alias
that names no physical interface. Reconciling them is the device manager's job,
and it needs the disagreements intact to do it.
"""
import logging
from typing import List

from backend.app.schemas.routeros import (
    ARPTableEntry,
    DHCPLeaseDTO,
    InterfaceDTO,
    WiFiRegistrationDTO,
)
from backend.app.services.routeros.parsing import build_wifi_links, parse_signal_list

logger = logging.getLogger("mikroman.routeros")


def _looks_like_ip(value: str) -> bool:
    """True for an IPv4/IPv6 literal, false for an interface name.

    Used to tell a resolved next-hop interface from an unresolved gateway
    address in ``/ip/route``.
    """
    v = value.strip()
    if ":" in v:  # any colon -> IPv6 literal (interface names never contain one)
        return True
    parts = v.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


class ClientsMixin:
    """DHCP, ARP, wireless registration and interface listing."""

    async def get_dhcp_leases(self) -> List[DHCPLeaseDTO]:
        """Fetch active DHCP leases."""
        async with self._get_client() as client:
            resp = await client.get("/ip/dhcp-server/lease")
            resp.raise_for_status()
            raw_leases = resp.json()
            if not isinstance(raw_leases, list):
                raw_leases = [raw_leases]

            results = []
            for item in raw_leases:
                if not item.get("mac-address") or not item.get("address"):
                    continue
                results.append(DHCPLeaseDTO(
                    id=item.get(".id"),
                    address=item.get("address"),
                    mac_address=item.get("mac-address").upper(),
                    host_name=item.get("host-name") or item.get("comment"),
                    server=item.get("server"),
                    status=item.get("status", "bound"),
                    comment=item.get("comment"),
                    expires_after=item.get("expires-after")
                ))
            return results

    async def get_arp_table(self) -> List[ARPTableEntry]:
        """Fetch ARP table entries."""
        async with self._get_client() as client:
            resp = await client.get("/ip/arp")
            resp.raise_for_status()
            raw_arp = resp.json()
            if not isinstance(raw_arp, list):
                raw_arp = [raw_arp]

            results = []
            for item in raw_arp:
                if not item.get("mac-address") or not item.get("address"):
                    continue
                results.append(ARPTableEntry(
                    id=item.get(".id"),
                    address=item.get("address"),
                    mac_address=item.get("mac-address").upper(),
                    interface=item.get("interface"),
                    complete=item.get("complete", "true") == "true" or item.get("complete") is True
                ))
            return results

    async def get_wifi_registrations(self) -> List[WiFiRegistrationDTO]:
        """Fetch connected WiFi clients (supports WifiWave2 / WiFi and legacy wireless)."""
        async with self._get_client() as client:
            endpoints = ["/interface/wifi/registration-table", "/interface/wireless/registration-table"]
            for ep in endpoints:
                try:
                    resp = await client.get(ep)
                    if resp.status_code == 200:
                        raw = resp.json()
                        if not isinstance(raw, list):
                            raw = [raw]
                        results = []
                        for item in raw:
                            mac = item.get("mac-address") or item.get("mac")
                            if not mac:
                                continue
                            signals = parse_signal_list(item.get("signal-strength") or item.get("signal"))
                            iface = item.get("interface", "wifi")
                            band = item.get("band")
                            results.append(WiFiRegistrationDTO(
                                mac_address=mac.upper(),
                                interface=iface,
                                ssid=item.get("ssid"),
                                signal_strength=signals[0] if signals else None,
                                tx_rate=str(item.get("tx-rate", "")),
                                rx_rate=str(item.get("rx-rate", "")),
                                uptime=item.get("uptime"),
                                band=band,
                                links=build_wifi_links(
                                    interface=iface,
                                    band=band,
                                    signals=signals,
                                    mld_interfaces=item.get("mld-interfaces"),
                                    mld_link_addresses=item.get("mld-link-addresses"),
                                )
                            ))
                        return results
                except Exception:
                    continue
            return []

    async def get_interfaces(self) -> List[InterfaceDTO]:
        """Fetch network interfaces."""
        async with self._get_client() as client:
            resp = await client.get("/interface")
            resp.raise_for_status()
            raw = resp.json()
            if not isinstance(raw, list):
                raw = [raw]

            results = []
            for item in raw:
                results.append(InterfaceDTO(
                    id=item.get(".id"),
                    name=item.get("name", "unknown"),
                    type=item.get("type"),
                    running=item.get("running", "true") == "true" or item.get("running") is True,
                    disabled=item.get("disabled", "false") == "true" or item.get("disabled") is True,
                    rx_byte=int(item.get("rx-byte", 0)),
                    tx_byte=int(item.get("tx-byte", 0)),
                    rx_rate=int(item.get("rx-bits-per-second", 0) or item.get("rx-rate", 0)),
                    tx_rate=int(item.get("tx-bits-per-second", 0) or item.get("tx-rate", 0)),
                    rx_error=int(item.get("rx-error", 0) or 0),
                    tx_error=int(item.get("tx-error", 0) or 0),
                    rx_drop=int(item.get("rx-drop", 0) or 0),
                    tx_drop=int(item.get("tx-drop", 0) or 0),
                    mac_address=item.get("mac-address"),
                    mtu=str(item.get("mtu")) if item.get("mtu") is not None else None
                ))
            return results

    async def get_wan_interfaces(self) -> List[str]:
        """Interface names that carry a default route.

        This is what "WAN" actually means - the link the router sends
        internet-bound traffic out of - rather than a guess from the name.
        A multi-WAN router returns more than one; a router with no default
        route (offline, or routing handled upstream) returns an empty list and
        the caller falls back to its own heuristic.

        RouterOS 7 reports the resolved next hop in ``immediate-gw`` as
        ``<gateway-ip>%<interface>`` for a routed link, or as a bare interface
        name for a point-to-point link such as PPPoE. ``gateway`` is used as a
        fallback for builds that do not populate ``immediate-gw``.
        """
        async with self._get_client() as client:
            resp = await client.get("/ip/route")
            resp.raise_for_status()
            rows = resp.json()
            if not isinstance(rows, list):
                rows = [rows]

        wan: List[str] = []
        for r in rows:
            if r.get("dst-address") not in ("0.0.0.0/0", "::/0"):
                continue
            # An inactive or disabled default route is not a live WAN.
            if str(r.get("active", "true")).lower() in ("false", "no"):
                continue
            if str(r.get("disabled", "false")).lower() in ("true", "yes"):
                continue
            hop = str(r.get("immediate-gw") or r.get("gateway") or "").strip()
            # "1.2.3.4%ether1, 1.2.3.4%ether2" -> each after the '%'
            for part in hop.split(","):
                part = part.strip()
                if not part:
                    continue
                if "%" in part:
                    name = part.split("%", 1)[1].strip()
                elif _looks_like_ip(part):
                    continue  # an unresolved IP hop tells us no interface
                else:
                    name = part  # bare interface name (PPPoE and the like)
                if name and name not in wan:
                    wan.append(name)
        return wan
