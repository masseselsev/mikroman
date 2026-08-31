import json
import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.db.models import (
    AlertLog,
    AppSetting,
    Device,
    DeviceHistory,
)
from backend.app.schemas.device import DeviceSuggestionDTO
from backend.app.schemas.routeros import ARPTableEntry, DHCPLeaseDTO, WiFiRegistrationDTO
from backend.app.services.device_linking import classify_connection
from backend.app.services.mac_rotation import (
    collect_present_macs,
    find_rotation_candidate,
    is_generic_hostname,
    normalise_hostname,
)
from backend.app.services.routeros import RouterOSClient
from backend.app.services.vendor_lookup import vendor_service

logger = logging.getLogger("mikroman.device_manager")

# Placeholder identities worth re-resolving once a hostname becomes known.
GENERIC_VENDOR_LABELS = {
    "Unknown Vendor",
    "Private MAC (Randomized)",
    "Randomized MAC",
}


def lookup_vendor(mac: str) -> str:
    """Lookup hardware vendor using synchronous cache."""
    return vendor_service.lookup_sync(mac)


def apply_wifi_registration(device: Device, wifi: WiFiRegistrationDTO) -> None:
    """Record a wireless association, including its individual radio links.

    For a WiFi 7 multi-link client the reported interface is an ``mld*`` alias
    that names no actual radio, so the member links are stored alongside it and
    the strongest of them is used as the device's headline signal.
    """
    device.last_interface = wifi.interface
    device.connection_kind = "wireless"

    links = [
        {
            "interface": link.interface,
            "mac_address": link.mac_address,
            "signal": link.signal_strength,
            "band": link.band,
        }
        for link in (wifi.links or [])
    ]
    device.wifi_links = links or None

    measured = [link.signal_strength for link in (wifi.links or []) if link.signal_strength is not None]
    # Best link is what the user experiences; a weak secondary link should not
    # make a well-connected device look bad.
    device.last_wifi_signal = max(measured) if measured else wifi.signal_strength


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

    async def _adopt_rotation(
        self,
        session: AsyncSession,
        new_mac: str,
        hostname: Optional[str],
        db_devices: dict,
        present_macs: set,
        now_utc: datetime,
    ) -> bool:
        """Re-key an existing device onto a rotated MAC, if one clearly matches.

        Returns True when a device was adopted, in which case ``db_devices`` now
        holds it under ``new_mac`` and the ordinary "already known" update path
        takes over from there - IP and hostname changes are recorded by it as
        usual.

        Updating the existing row rather than creating a second one is the whole
        purpose: ownership, the custom name, the speed limit and the accumulated
        traffic history all stay attached to the device they belong to. Creating
        a new row moved the device and left its identity behind.
        """
        candidate = find_rotation_candidate(new_mac, hostname, db_devices.values(), present_macs)
        if candidate is None:
            return False

        old_mac = candidate.mac_address
        candidate.mac_address = new_mac
        candidate.is_active = True
        candidate.last_seen = now_utc

        # A rotated address invalidates whatever vendor was derived from the old
        # one; the OUI of a private MAC identifies nothing.
        candidate.vendor = await vendor_service.lookup_async(
            new_mac, hostname=hostname or candidate.hostname or candidate.custom_name
        )

        detail = (
            f"Private MAC rotated from {old_mac} to {new_mac}. Recognised as the same device "
            f"because '{hostname or candidate.hostname}' returned on a new private address at the "
            f"moment {old_mac} left the network - typically caused by the Wi-Fi network being "
            f"renamed or its security settings changed."
        )
        session.add(DeviceHistory(
            device_id=candidate.id,
            mac_address=new_mac,
            hostname=candidate.hostname,
            ip_address=candidate.ip_address,
            event_type="mac_rotated",
            details=detail,
        ))
        # Recorded as an alert as well as history: this rewrites an existing
        # device's identity, so it must never happen silently.
        session.add(AlertLog(
            router_id=self.router_id,
            alert_type="mac_rotated",
            message=(
                f"Device '{candidate.custom_name or candidate.hostname or new_mac}' returned with a "
                f"new private MAC ({old_mac} -> {new_mac}) and was matched to its existing record."
            ),
        ))
        logger.info(detail)

        db_devices.pop(old_mac, None)
        db_devices[new_mac] = candidate
        return True

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

        # Every address the router can see right now, across all three tables.
        # Computed up front because rotation detection needs to ask whether a
        # device's *old* address is still anywhere on the router, and that
        # question cannot be answered from a set being filled in as we go.
        present_macs = collect_present_macs(leases, arps, wifis)

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

            # An unrecognised private address may be a device we already know
            # that has rotated. Checked before the "new device" path so the
            # existing row is re-keyed instead of being duplicated.
            if mac not in db_devices:
                await self._adopt_rotation(
                    session, mac, lease.host_name, db_devices, present_macs, now_utc
                )

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
                    apply_wifi_registration(device, wifi_info)
                elif arp_info and arp_info.interface:
                    device.last_interface = arp_info.interface
                    kind = classify_connection(arp_info.interface, None)
                    # None means the interface is inconclusive - typically the
                    # bridge, which every wireless client is also seen through.
                    # Keeping the previous answer beats replacing a correct
                    # "wireless" with a guess.
                    if kind is not None:
                        device.connection_kind = kind
                    if kind == "wired":
                        # The machine has moved onto cable. Keeping the last
                        # wireless reading would report a signal for a link that
                        # no longer exists.
                        device.wifi_links = None
                        device.last_wifi_signal = None

                device.is_active = True
                device.last_seen = now_utc

                # Re-resolve placeholder identities, not just "Unknown Vendor".
                # A randomized-MAC device discovered before its hostname was
                # known kept the generic label forever.
                if not device.vendor or device.vendor in GENERIC_VENDOR_LABELS:
                    device.vendor = await vendor_service.lookup_async(mac, hostname=device.hostname or lease.host_name)
            else:
                # The quarantine limit is reported in the discovery record, but
                # deliberately NOT written to device.speed_limit - see the
                # Device() construction below.
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
                    # "default" means "no explicit override for this device".
                    # The quarantine limit is a property of *being unassigned*
                    # and is resolved at queue-build time by
                    # TrafficController.sync_device_queue, so it must not be
                    # frozen into the row here.
                    #
                    # Stamping the literal was the bug behind the nonsensical
                    # queue trees: a device discovered at "5M/5M" kept that value
                    # after it was assigned to a user, so it got a 5M/5M child
                    # queue underneath an unlimited parent and was throttled to
                    # 5 Mbps forever. Nothing ever cleared it, because from the
                    # queue builder's point of view the operator had asked for
                    # exactly that.
                    speed_limit="default",
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

            # Same rotation check as the lease path. A rotated device that has
            # not renewed a lease yet still shows up here, and it must be
            # recognised in both places or the ARP pass would recreate the
            # duplicate the lease pass just avoided.
            arp_hostname = next(
                (lz.host_name for lz in leases if lz.mac_address == mac and lz.host_name), None
            )
            if mac not in db_devices:
                await self._adopt_rotation(
                    session, mac, arp_hostname, db_devices, present_macs, now_utc
                )

            if mac in db_devices:
                device = db_devices[mac]
                if arp.address and (not device.ip_address or device.ip_address == "0.0.0.0"):
                    device.ip_address = arp.address
                if wifi_info:
                    apply_wifi_registration(device, wifi_info)
                elif arp.interface and not device.last_interface:
                    device.last_interface = arp.interface
            else:
                # Pass whatever hostname the lease table knows for this MAC: a
                # randomized MAC has no OUI, so the hostname is the only thing
                # that can identify it as e.g. a Pixel rather than "Private MAC".
                lease_hostname = arp_hostname
                vendor = await vendor_service.lookup_async(mac, hostname=lease_hostname)
                device = Device(
                    mac_address=mac,
                    router_id=self.router_id,
                    ip_address=arp.address,
                    hostname=lease_hostname,
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

        # An authorized wireless association is direct proof of presence, and is
        # more reliable than ARP: a client can hold a stable radio link while its
        # ARP entry expires, and a client that roams onto Wi-Fi may have no DHCP
        # lease of its own. Without this, such devices went dark in the UI.
        for wifi in wifis:
            mac = wifi.mac_address
            if not mac:
                continue
            active_macs.add(mac)
            device = db_devices.get(mac)
            if device:
                apply_wifi_registration(device, wifi)

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
            # is_randomized_mac is a property of the DTO, not the ORM row - the
            # model carries only the address. Reading it off the Device object
            # raised AttributeError and 500'd this whole endpoint the moment an
            # inactive assigned device shared a vendor with an unassigned one,
            # which is exactly the state Rule 3 below exists to act on.
            u_is_random = vendor_service.is_randomized_mac(u_dev.mac_address)

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
                elif not a_dev.is_active and u_is_random and u_vendor and a_vendor and u_vendor == a_vendor:
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

    async def _absorb_device(self, session: AsyncSession, survivor: Device, victim: Device) -> None:
        """Fold ``victim``'s history and traffic into ``survivor`` and delete it.

        Unlike ``merge_devices`` this does NOT copy the victim's network
        coordinates onto the survivor: the survivor is the record that is
        currently present, so its MAC, IP and interface are the live ones and
        the victim's are the stale ones being retired.

        Reassignment goes through the relationships, not raw UPDATEs. ``history``
        and ``traffic_rollups`` are ``cascade="all, delete-orphan"``; a Core
        UPDATE would move the rows in the database but leave the session's
        in-memory collections pointing at them, and the cascade fired by
        ``session.delete(victim)`` would then delete the rows we just moved.
        """
        from sqlalchemy import update

        # Fresh collections: a device already in the session keeps whatever its
        # selectin relationships loaded when it first appeared, and a rollup
        # written since would be missed and then duplicated onto the survivor.
        await session.refresh(victim, ["history", "traffic_rollups"])
        await session.refresh(survivor, ["traffic_rollups"])

        for record in list(victim.history):
            record.device = survivor

        # Daily traffic rollups: add onto the survivor's row for the same date,
        # move the rest. Losing these silently is how a merge used to erase a
        # device's accumulated volume.
        survivor_by_date = {r.record_date: r for r in survivor.traffic_rollups}
        for vr in list(victim.traffic_rollups):
            existing = survivor_by_date.get(vr.record_date)
            if existing:
                existing.bytes_in += vr.bytes_in
                existing.bytes_out += vr.bytes_out
                vr.device = None  # orphaned -> deleted on flush
            else:
                vr.device = survivor
                survivor_by_date[vr.record_date] = vr

        await session.flush()

        # Anything that treated the victim as its primary adapter now points at
        # the survivor; a resulting self-reference is cleared.
        await session.execute(
            update(Device)
            .where(Device.linked_to_device_id == victim.id)
            .values(linked_to_device_id=survivor.id)
        )
        if survivor.linked_to_device_id == survivor.id:
            survivor.linked_to_device_id = None
        victim.linked_to_device_id = None

        seen = victim.last_seen.strftime("%Y-%m-%d %H:%M") if victim.last_seen else "unknown"
        session.add(DeviceHistory(
            device_id=survivor.id,
            mac_address=victim.mac_address,
            hostname=survivor.hostname,
            ip_address=survivor.ip_address,
            event_type="mac_rotated",
            details=(
                f"Consolidated a rotated record ({victim.mac_address}, last seen "
                f"{seen} UTC) into this device"
            ),
        ))
        await session.delete(victim)
        await session.flush()

    async def consolidate_rotated_devices(self, session: AsyncSession) -> int:
        """Collapse the rows left behind by repeated private-MAC rotation.

        Discovery-time adoption (:func:`mac_rotation.find_rotation_candidate`)
        only fires when it can identify a *single* prior record for a returning
        device. The moment two or more duplicates for one phone exist - because
        rotations happened before adoption shipped, or an access-point change
        produced several in quick succession - it can no longer tell which to
        adopt onto and declines, so every further rotation adds another row.
        The dashboard fills with "Pixel-9-Pro-XL x5" and the queue tree grows a
        branch per ghost.

        This pass cleans that up after the fact. Devices are grouped by their
        normalised hostname, considering only rows that carry a randomized MAC.
        A group is consolidated when every row in it that has an owner has the
        *same* owner - the user has already asserted one identity by assigning
        them together. The survivor is the row that is currently active, or
        failing that the most recently seen; every other row's history and
        traffic move onto it and the row is deleted. Any unassigned duplicates
        in the group are adopted onto the same owner in the process, which is
        the automatic merge that manual suggestions used to require a click for.

        A **generic** hostname ("iPhone", "android" - names a house can hold two
        of) is held to a stricter bar: the rows must also share one vendor and
        no more than one may be currently active. Two "iPhone" rows both online
        under one user really could be two phones; three where two are stale is
        one phone that rotated.

        A group whose rows are split across two different users is left alone -
        that is two people who genuinely own a device of the same model, and
        guessing wrong would hand one person's device to the other.

        Returns:
            Number of duplicate rows removed.
        """
        from datetime import datetime

        devices = (await session.execute(select(Device))).scalars().all()

        groups: dict = {}
        for device in devices:
            if not vendor_service.is_randomized_mac(device.mac_address):
                continue
            name = normalise_hostname(device.custom_name) or normalise_hostname(device.hostname)
            if not name:
                continue
            groups.setdefault(name, []).append(device)

        removed = 0
        for name, members in groups.items():
            if len(members) < 2:
                continue

            owners = {d.user_id for d in members if d.user_id is not None}
            if len(owners) > 1:
                logger.info(
                    f"Not consolidating {len(members)} '{name}' records: they are split "
                    f"across users {sorted(owners)}, which looks like two real devices"
                )
                continue
            if not owners:
                # All unassigned - could be two guests with the same phone
                # model. Left for the manual merge-suggestions flow.
                continue

            if is_generic_hostname(name):
                vendors = {(d.vendor or "").strip().lower() for d in members if d.vendor}
                active_count = sum(1 for d in members if d.is_active)
                if len(vendors) > 1 or active_count > 1:
                    logger.info(
                        f"Not consolidating {len(members)} '{name}' records: generic "
                        f"hostname with {'mixed vendors' if len(vendors) > 1 else 'more than one online'}, "
                        f"could be two real devices"
                    )
                    continue

            target_user = owners.pop()
            # Prefer a currently-active row; break ties on last_seen.
            survivor = max(
                members,
                key=lambda d: (d.is_active, d.last_seen or datetime.min),
            )
            survivor.user_id = target_user

            victims = [d for d in members if d.id != survivor.id]
            for victim in victims:
                await self._absorb_device(session, survivor, victim)
                removed += 1

            session.add(AlertLog(
                router_id=self.router_id,
                alert_type="mac_rotated",
                message=(
                    f"Consolidated {len(victims)} rotated '{name}' record(s) into one device "
                    f"for user {target_user}. If two people genuinely own a '{name}', reassign "
                    f"the one that reappears."
                ),
            ))
            logger.info(
                f"Consolidated {len(victims)} rotated '{name}' record(s) into device "
                f"{survivor.id} (user {target_user})"
            )

        if removed:
            await session.commit()
        return removed

    async def merge_devices(self, session: AsyncSession, source_device_id: int, target_device_id: int, note: Optional[str] = None) -> Device:
        """Merges a newly discovered (unassigned) device into a target user device."""
        source = await session.get(
            Device, source_device_id,
            options=[selectinload(Device.history), selectinload(Device.traffic_rollups)],
        )
        target = await session.get(
            Device, target_device_id,
            options=[selectinload(Device.history), selectinload(Device.traffic_rollups)],
        )

        if not source or not target:
            raise ValueError("Source or target device not found")

        # Force-refresh the collections: if either device was already in the
        # session (a prior refresh in the same request), its selectin
        # relationships were loaded then and the options above do not reload
        # them - so a rollup added since would be invisible and get duplicated.
        await session.refresh(source, ["history", "traffic_rollups"])
        await session.refresh(target, ["traffic_rollups"])

        old_target_mac = target.mac_address
        new_source_mac = source.mac_address
        source_ip = source.ip_address
        source_hostname = source.hostname
        source_interface = source.last_interface
        source_signal = source.last_wifi_signal
        source_active = source.is_active
        source_seen = source.last_seen
        source_vendor = source.vendor

        # Re-link history and daily traffic to the target. Assignment goes
        # through the `.device` relationship, not a Core UPDATE: `history` and
        # `traffic_rollups` are delete-orphan cascades, and a Core UPDATE would
        # leave the in-memory collections pointing at the moved rows so the
        # cascade on `session.delete(source)` would delete them again. Carrying
        # the rollups is new - leaving them behind orphaned every byte the
        # rotated-away MAC had moved, quietly shrinking the household totals.
        for h in list(source.history):
            h.device = target

        target_rolls_by_date = {r.record_date: r for r in target.traffic_rollups}
        for sr in list(source.traffic_rollups):
            existing = target_rolls_by_date.get(sr.record_date)
            if existing:
                existing.bytes_in += sr.bytes_in
                existing.bytes_out += sr.bytes_out
                sr.device = None  # orphaned -> deleted on flush
            else:
                sr.device = target
                target_rolls_by_date[sr.record_date] = sr

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
