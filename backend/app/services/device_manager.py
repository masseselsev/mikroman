import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import AlertLog, Device
from backend.app.schemas.routeros import ARPTableEntry, DHCPLeaseDTO, WiFiRegistrationDTO
from backend.app.services.routeros import RouterOSClient

logger = logging.getLogger("mikroman.device_manager")

OUI_PREFIXES: Dict[str, str] = {
    "AC:DE:48": "Apple", "F0:18:98": "Apple", "DC:A9:04": "Apple", "3C:22:FB": "Apple",
    "F4:34:F0": "Apple", "BC:D1:1F": "Apple", "A8:66:7F": "Apple", "00:17:88": "Philips Hue",
    "50:EC:50": "Samsung", "8C:77:12": "Samsung", "D0:03:DF": "Samsung", "98:52:B1": "Samsung",
    "00:1A:7D": "Sony", "FC:0F:E6": "Sony", "70:9E:29": "Sony Interactive (PlayStation)",
    "B8:27:EB": "Raspberry Pi", "DC:A6:32": "Raspberry Pi", "E4:5F:01": "Raspberry Pi",
    "00:E0:4C": "Realtek", "38:F9:D3": "Intel", "00:15:00": "Intel", "48:51:B7": "Intel",
    "64:64:4A": "Xiaomi", "7C:49:EB": "Xiaomi", "04:CF:8C": "Xiaomi", "AC:C1:EE": "Xiaomi",
    "60:45:BD": "Google", "54:60:09": "Google", "D8:6C:63": "Google", "F4:F5:DB": "Google",
    "30:FD:38": "Microsoft", "DC:B4:C4": "Microsoft", "00:50:F2": "Microsoft",
    "48:A9:D2": "Espressif (IoT)", "24:6F:28": "Espressif (IoT)", "30:AE:A4": "Espressif (IoT)",
    "00:0C:29": "VMware", "00:50:56": "VMware", "52:54:00": "QEMU/KVM"
}


def lookup_vendor(mac: str) -> str:
    """Lookup hardware vendor using MAC OUI prefix."""
    clean_mac = mac.upper().replace("-", ":")
    prefix_6 = clean_mac[:8]
    if prefix_6 in OUI_PREFIXES:
        return OUI_PREFIXES[prefix_6]
    return "Unknown Vendor"


class DeviceManager:
    """Manages discovery, inventory, and user assignment of network devices."""

    def __init__(self, router_client: RouterOSClient, router_id: Optional[int] = None):
        self.router_client = router_client
        self.router_id = router_id

    async def sync_devices_from_router(self, session: AsyncSession) -> Tuple[List[Device], List[Device]]:
        """Syncs RouterOS DHCP leases and ARP table into SQLite DB.

        Returns:
            Tuple of (all_active_devices, newly_discovered_devices)
        """
        try:
            leases: List[DHCPLeaseDTO] = await self.router_client.get_dhcp_leases()
            arps: List[ARPTableEntry] = await self.router_client.get_arp_table()
            wifis: List[WiFiRegistrationDTO] = await self.router_client.get_wifi_registrations()
        except Exception as e:
            logger.error(f"Failed to query network discovery endpoints: {e}")
            return [], []

        wifi_map = {w.mac_address: w for w in wifis}
        arp_map = {a.mac_address: a for a in arps}

        # Query all existing devices in DB
        result = await session.execute(select(Device))
        db_devices = {d.mac_address: d for d in result.scalars().all()}

        active_macs = set()
        newly_discovered: List[Device] = []

        # Process DHCP Leases
        for lease in leases:
            mac = lease.mac_address
            active_macs.add(mac)
            wifi_info = wifi_map.get(mac)
            arp_info = arp_map.get(mac)

            if mac in db_devices:
                device = db_devices[mac]
                device.ip_address = lease.address
                if lease.host_name:
                    device.hostname = lease.host_name
                if wifi_info:
                    device.last_wifi_signal = wifi_info.signal_strength
                    device.last_interface = wifi_info.interface
                elif arp_info and arp_info.interface:
                    device.last_interface = arp_info.interface
                device.is_active = True
                device.last_seen = datetime.now(timezone.utc).replace(tzinfo=None)
            else:
                vendor = lookup_vendor(mac)
                device = Device(
                    mac_address=mac,
                    router_id=self.router_id,
                    ip_address=lease.address,
                    hostname=lease.host_name,
                    custom_name=lease.host_name,
                    vendor=vendor,
                    last_interface=wifi_info.interface if wifi_info else (arp_info.interface if arp_info else None),
                    last_wifi_signal=wifi_info.signal_strength if wifi_info else None,
                    is_active=True,
                    last_seen=datetime.now(timezone.utc).replace(tzinfo=None)
                )
                session.add(device)
                newly_discovered.append(device)
                db_devices[mac] = device

                # Create alert log entry for new device
                alert = AlertLog(
                    router_id=self.router_id,
                    alert_type="new_device",
                    message=f"New device discovered: {device.hostname or 'Unknown'} ({device.mac_address}) at {device.ip_address}",
                    metadata_payload={"mac": mac, "ip": lease.address, "vendor": vendor, "hostname": lease.host_name}
                )
                session.add(alert)

        # Mark devices not seen in current scan as inactive
        for mac, dev in db_devices.items():
            if mac not in active_macs:
                dev.is_active = False

        await session.commit()
        return list(db_devices.values()), newly_discovered
