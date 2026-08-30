import json
import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.db.models import AlertLog, AppSetting, Device, DeviceHistory
from backend.app.schemas.device import DeviceSuggestionDTO
from backend.app.schemas.routeros import ARPTableEntry, DHCPLeaseDTO, WiFiRegistrationDTO
from backend.app.services.routeros import RouterOSClient
from backend.app.services.vendor_lookup import vendor_service

logger = logging.getLogger("mikroman.device_manager")


def lookup_vendor(mac: str) -> str:
    """Lookup hardware vendor using synchronous cache."""
    return vendor_service.lookup_sync(mac)


class DeviceManager:
    """Manages discovery, inventory, MAC history, and user assignment of network devices."""

    def __init__(self, router_client: RouterOSClient, router_id: Optional[int] = None):
        self.router_client = router_client
        self.router_id = router_id

    async def _get_wan_interfaces(self, session: AsyncSession) -> set:
        """Interface names treated as uplinks rather than LAN ports.

        The ARP table also lists the upstream ISP gateway, which sits on the WAN
        port. Without this filter it was ingested as an ordinary client and
        given a quarantine queue and an accounting rule of its own.
        """
        key = f"monitored_interfaces_{self.router_id}" if self.router_id else "monitored_interfaces_default"
        setting = await session.get(AppSetting, key)
        if setting and setting.value:
            try:
                names = json.loads(setting.value)
                if isinstance(names, list) and names:
                    return {str(n) for n in names}
            except (json.JSONDecodeError, TypeError):
                logger.debug(f"Could not parse {key}; falling back to default WAN interface")
        return {"ether1"}

    async def sync_devices_from_router(self, session: AsyncSession) -> Tuple[List[Device], List[Device]]:
        """Syncs RouterOS DHCP leases and ARP table into SQLite DB and logs state changes.

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

        # Drop uplink-side ARP entries before they are treated as LAN clients, and
        # unresolved ones, which RouterOS keeps after a host has left the network
        # and which are therefore no evidence that the device is still online.
        wan_interfaces = await self._get_wan_interfaces(session)
        arps = [
            a for a in arps
            if (a.interface or "") not in wan_interfaces and a.complete
        ]

        wifi_map = {w.mac_address: w for w in wifis}
        arp_map = {a.mac_address: a for a in arps}

        # Query all existing devices in DB with history preloaded
        result = await session.execute(select(Device).options(selectinload(Device.history)))
        db_devices = {d.mac_address: d for d in result.scalars().all()}

        active_macs = set()
        newly_discovered: List[Device] = []
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

        # Process DHCP Leases
        for lease in leases:
            mac = lease.mac_address
            active_macs.add(mac)
            wifi_info = wifi_map.get(mac)
            arp_info = arp_map.get(mac)

            if mac in db_devices:
                device = db_devices[mac]
                old_ip = device.ip_address
                old_host = device.hostname

                # Track IP change
                if lease.address and lease.address != old_ip:
                    device.ip_address = lease.address
                    session.add(DeviceHistory(
                        device_id=device.id,
                        mac_address=mac,
                        hostname=device.hostname or lease.host_name,
                        ip_address=lease.address,
                        event_type="ip_changed",
                        details=f"IP changed from {old_ip or 'None'} to {lease.address}"
                    ))

                # Track Hostname change
                if lease.host_name and lease.host_name != old_host:
                    device.hostname = lease.host_name
                    session.add(DeviceHistory(
                        device_id=device.id,
                        mac_address=mac,
                        hostname=lease.host_name,
                        ip_address=device.ip_address,
                        event_type="hostname_changed",
                        details=f"Hostname changed from '{old_host or 'None'}' to '{lease.host_name}'"
                    ))

                if wifi_info:
                    device.last_wifi_signal = wifi_info.signal_strength
                    device.last_interface = wifi_info.interface
                elif arp_info and arp_info.interface:
                    device.last_interface = arp_info.interface

                device.is_active = True
                device.last_seen = now_utc

                # If vendor was previously Unknown, attempt to resolve it
                if not device.vendor or device.vendor == "Unknown Vendor":
                    device.vendor = await vendor_service.lookup_async(mac, hostname=device.hostname or lease.host_name)
            else:
                # Fetch default speed limit for unassigned / suspicious devices
                setting_res = await session.execute(
                    select(AppSetting).where(AppSetting.key == "unassigned_device_speed_limit")
                )
                setting_row = setting_res.scalar_one_or_none()
                unassigned_limit = setting_row.value if setting_row else "5M/5M"

                is_random = vendor_service.is_randomized_mac(mac)
                vendor = await vendor_service.lookup_async(mac, hostname=lease.host_name)
                device = Device(
                    mac_address=mac,
                    router_id=self.router_id,
                    ip_address=lease.address,
                    hostname=lease.host_name,
                    custom_name=lease.host_name,
                    vendor=vendor,
                    last_interface=wifi_info.interface if wifi_info else (arp_info.interface if arp_info else None),
                    last_wifi_signal=wifi_info.signal_strength if wifi_info else None,
                    speed_limit=unassigned_limit,
                    is_active=True,
                    last_seen=now_utc
                )
                session.add(device)
                await session.flush()  # populate device.id

                # Record discovery in history
                disc_details = (
                    f"First discovered on network with vendor {vendor} (quarantine limited to {unassigned_limit})"
                    if not is_random
                    else f"Discovered with Private/Randomized MAC address (quarantine limited to {unassigned_limit})"
                )
                session.add(DeviceHistory(
                    device_id=device.id,
                    mac_address=mac,
                    hostname=lease.host_name,
                    ip_address=lease.address,
                    event_type="discovered",
                    details=disc_details
                ))

                newly_discovered.append(device)
                db_devices[mac] = device

                # Create alert log entry for new device
                alert = AlertLog(
                    router_id=self.router_id,
                    alert_type="new_device",
                    message=f"New device discovered: {device.hostname or 'Unknown'} ({device.mac_address}) at {device.ip_address} [Vendor: {vendor}]"
                )
                session.add(alert)

        # Process ARP entries for static devices or existing devices without active DHCP leases
        for arp in arps:
            mac = arp.mac_address
            if not mac:
                continue
            active_macs.add(mac)
            wifi_info = wifi_map.get(mac)

            if mac in db_devices:
                device = db_devices[mac]
                if arp.address and (not device.ip_address or device.ip_address == "0.0.0.0"):
                    device.ip_address = arp.address
                if wifi_info:
                    device.last_wifi_signal = wifi_info.signal_strength
                    device.last_interface = wifi_info.interface
                elif arp.interface and not device.last_interface:
                    device.last_interface = arp.interface
            else:
                vendor = await vendor_service.lookup_async(mac)
                device = Device(
                    mac_address=mac,
                    router_id=self.router_id,
                    ip_address=arp.address,
                    hostname=None,
                    vendor=vendor,
                    last_interface=wifi_info.interface if wifi_info else arp.interface,
                    last_wifi_signal=wifi_info.signal_strength if wifi_info else None,
                    is_active=True,
                    last_seen=now_utc
                )
                session.add(device)
                await session.flush()

                session.add(DeviceHistory(
                    device_id=device.id,
                    mac_address=mac,
                    hostname=None,
                    ip_address=arp.address,
                    event_type="discovered",
                    details=f"Discovered via ARP table with vendor {vendor}"
                ))

                newly_discovered.append(device)
                db_devices[mac] = device

        # Mark active / inactive devices
        for mac, device in db_devices.items():
            device.is_active = (mac in active_macs)
            if device.is_active:
                device.last_seen = now_utc

        await session.commit()
        return list(db_devices.values()), newly_discovered

    async def find_merge_suggestions(self, session: AsyncSession) -> List[DeviceSuggestionDTO]:
        """Identifies unassigned devices that likely belong to an existing user device (e.g. MAC rotation)."""
        # Get unassigned devices
        unassigned_res = await session.execute(
            select(Device).where(Device.user_id == None).options(selectinload(Device.history))  # noqa: E711
        )
        unassigned_devs = unassigned_res.scalars().all()

        # Get assigned devices with their parent users
        assigned_res = await session.execute(
            select(Device).where(Device.user_id != None).options(selectinload(Device.user), selectinload(Device.history))  # noqa: E711
        )
        assigned_devs = assigned_res.scalars().all()

        suggestions: List[DeviceSuggestionDTO] = []

        for u_dev in unassigned_devs:
            u_host = (u_dev.hostname or "").strip().lower()
            u_vendor = (u_dev.vendor or "").strip().lower()

            for a_dev in assigned_devs:
                if not a_dev.user:
                    continue

                a_host = (a_dev.hostname or a_dev.custom_name or "").strip().lower()
                a_vendor = (a_dev.vendor or "").strip().lower()

                confidence = 0.0
                reason = ""

                # Rule 1: Exact hostname match on private MAC
                if u_host and a_host and u_host == a_host:
                    confidence = 0.95
                    reason = f"Identical hostname '{u_dev.hostname}' on user '{a_dev.user.name}'"
                # Rule 2: Substring or normalized hostname match
                elif u_host and a_host and (u_host in a_host or a_host in u_host) and len(u_host) > 3:
                    confidence = 0.80
                    reason = f"Matching name pattern '{u_dev.hostname}' ~ '{a_dev.hostname or a_dev.custom_name}'"
                # Rule 3: Target device is inactive, same vendor & private MAC
                elif not a_dev.is_active and u_dev.is_randomized_mac and u_vendor and a_vendor and u_vendor == a_vendor:
                    if "apple" in u_vendor or "pixel" in u_vendor or "samsung" in u_vendor:
                        confidence = 0.70
                        reason = f"Rotated private MAC for offline device '{a_dev.custom_name or a_dev.hostname}'"

                if confidence >= 0.70:
                    suggestions.append(DeviceSuggestionDTO(
                        unassigned_device_id=u_dev.id,
                        suggested_target_device_id=a_dev.id,
                        suggested_user_id=a_dev.user.id,
                        suggested_user_name=a_dev.user.name,
                        target_device_name=a_dev.custom_name or a_dev.hostname or a_dev.mac_address,
                        confidence=confidence,
                        reason=reason
                    ))

        return suggestions

    async def merge_devices(self, session: AsyncSession, source_device_id: int, target_device_id: int, note: Optional[str] = None) -> Device:
        """Merges a newly discovered (unassigned) device into a target user device."""
        source = await session.get(Device, source_device_id, options=[selectinload(Device.history)])
        target = await session.get(Device, target_device_id, options=[selectinload(Device.history)])

        if not source or not target:
            raise ValueError("Source or target device not found")

        old_target_mac = target.mac_address
        new_source_mac = source.mac_address
        source_ip = source.ip_address
        source_hostname = source.hostname
        source_interface = source.last_interface
        source_signal = source.last_wifi_signal
        source_active = source.is_active
        source_seen = source.last_seen
        source_vendor = source.vendor

        # Re-link any existing history records from source to target
        for h in list(source.history):
            h.device_id = target.id

        # Delete source device and flush to avoid unique constraint conflict on mac_address
        await session.delete(source)
        await session.flush()

        # Update target with latest network coordinates from source
        target.mac_address = new_source_mac
        target.ip_address = source_ip or target.ip_address
        if source_hostname:
            target.hostname = source_hostname
        target.last_interface = source_interface or target.last_interface
        target.last_wifi_signal = source_signal or target.last_wifi_signal
        target.is_active = source_active
        target.last_seen = source_seen
        if source_vendor and source_vendor != "Unknown Vendor":
            target.vendor = source_vendor

        # Log rotation / merge in target device history
        session.add(DeviceHistory(
            device_id=target.id,
            mac_address=new_source_mac,
            hostname=target.hostname,
            ip_address=target.ip_address,
            event_type="mac_rotated",
            details=note or f"Rotated MAC from {old_target_mac} to {new_source_mac}"
        ))

        await session.commit()
        await session.refresh(target)
        return target
