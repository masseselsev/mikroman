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
