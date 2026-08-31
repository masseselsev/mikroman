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
    DeviceCoexistence,
    DeviceHistory,
    TrafficRollup,
)
from backend.app.schemas.routeros import ARPTableEntry, DHCPLeaseDTO, WiFiRegistrationDTO
from backend.app.services.device_consolidation import (
    DEFAULT_ROTATION_SETTLE_HOURS,  # noqa: F401  (re-exported: existing import path)
    DeviceConsolidationMixin,
)
from backend.app.services.device_linking import classify_connection
from backend.app.services.mac_rotation import (
    collect_present_macs,
    find_rotation_candidate,
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


async def detach_device_traffic_from_user(
    session: AsyncSession, device: Device, user_id: int
) -> int:
    """Subtract a device's recorded daily volume back out of a user's totals.

    ``TrafficAccountingService.collect`` writes the per-device and per-user daily
    rollups from the *same* deltas, so a user's ``TrafficRollup`` for a date is
    the sum of that user's devices' ``DeviceTrafficRollup`` for the date. When a
    device leaves the profile, its share can be taken back out by subtracting it
    date-for-date.

    Clamped at zero. A device that was unassigned for part of its life
    contributed nothing to the user then, and the rollups carry no record of
    *which* user owned the device on each date, so a blind subtraction could
    otherwise push a historical total negative. Over-keeping a little is the
    safer error than showing a negative month.

    Returns the number of user rollup rows adjusted.
    """
    await session.refresh(device, ["traffic_rollups"])
    by_date = {r.record_date: r for r in device.traffic_rollups}
    if not by_date:
        return 0

    rows = (await session.execute(
        select(TrafficRollup).where(
            TrafficRollup.user_id == user_id,
            TrafficRollup.record_date.in_(list(by_date.keys())),
        )
    )).scalars().all()

    adjusted = 0
    for user_roll in rows:
        dev_roll = by_date[user_roll.record_date]
        user_roll.bytes_in = max(0, user_roll.bytes_in - dev_roll.bytes_in)
        user_roll.bytes_out = max(0, user_roll.bytes_out - dev_roll.bytes_out)
        adjusted += 1
    return adjusted


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


class DeviceManager(DeviceConsolidationMixin):
    """Discovery, inventory, MAC history and user assignment of network devices.

    Merge and rotation-cleanup behaviour lives in
    :class:`~backend.app.services.device_consolidation.DeviceConsolidationMixin`
    and is composed in here: discovery asks what is on the network now,
    consolidation asks which of yesterday's rows were the same device. Splitting
    them keeps each file readable; composing them keeps every existing call site
    (``DeviceManager(client).merge_devices(...)``) working unchanged.
    """

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

    async def _get_container_interfaces(self) -> set:
        """Names of ``veth`` interfaces - the router-side end of a container.

        RouterOS attaches each container to a ``veth`` pair and gives the router
        an address on it, so the container shows up in the ARP table with a MAC
        and an IP and is indistinguishable from a network client by any other
        signal. The interface *type* is the one thing that tells them apart.

        Returns an empty set on any failure, which degrades to the previous
        behaviour (containers treated as ordinary devices) rather than losing
        real clients because one call did not answer.
        """
        try:
            interfaces = await self.router_client.get_interfaces()
        except Exception as e:
            logger.debug(f"Could not read interfaces to identify containers: {e}")
            return set()
        return {
            iface.name for iface in interfaces
            if (getattr(iface, "type", "") or "").lower() == "veth"
        }

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

        # If the candidate has ever been seen online next to another device of
        # the same name, that name denotes more than one radio in this house.
        # "Exactly one prior record" is then no longer a safe basis for re-keying
        # - the address that just appeared could belong to any of them, or be a
        # genuinely new arrival. Leave it for the operator to resolve.
        coexists = (
            await session.execute(
                select(DeviceCoexistence)
                .where(
                    (DeviceCoexistence.mac_a == candidate.mac_address)
                    | (DeviceCoexistence.mac_b == candidate.mac_address)
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if coexists is not None:
            logger.info(
                f"Not adopting {new_mac} onto {candidate.mac_address}: that record has "
                f"been seen online beside another same-named device, so "
                f"'{hostname or candidate.hostname}' is more than one device here"
            )
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

        # Interfaces that carry a container rather than a client. A container's
        # veth end answers ARP exactly like a laptop does, so without this every
        # workload the operator runs on the router queues up in the unassigned
        # inbox asking to be given to somebody.
        container_interfaces = await self._get_container_interfaces()

        wifi_map = {w.mac_address: w for w in wifis}
        arp_map = {a.mac_address: a for a in arps}
        container_macs = {
            a.mac_address for a in arps if (a.interface or "") in container_interfaces
        }

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

        # Flag whatever answered on a veth interface as a container. Done in one
        # pass at the end rather than at each creation site: a device can be
        # discovered through the lease table, the ARP table or both, and the
        # interface evidence only lives in the ARP entry.
        for mac in container_macs:
            device = db_devices.get(mac)
            if device is not None and not device.is_container:
                device.is_container = True
                logger.info(
                    f"{device.hostname or mac} answered on a container interface; "
                    f"classifying it as a router workload, not a network client"
                )
        # A container that was rehomed onto an ordinary interface stops being one.
        for mac in active_macs - container_macs:
            device = db_devices.get(mac)
            if device is not None and device.is_container:
                device.is_container = False

        # Remember any pair of same-named private-MAC devices that were both
        # online in this sweep. That is the evidence consolidation needs to keep
        # two real phones apart later, when only one of them happens to be on.
        await self._record_coexistence(session, db_devices, active_macs, now_utc)

        await session.commit()
        return list(db_devices.values()), newly_discovered

    async def _record_coexistence(
        self,
        session: AsyncSession,
        db_devices: dict,
        active_macs: set,
        now_utc: datetime,
    ) -> None:
        """Log every pair of same-named private-MAC devices seen online together.

        One radio cannot answer on two addresses at once, so two private MACs
        active in the same sweep under one hostname are two physical devices, not
        one phone mid-rotation. Recording the pair in ``device_coexistence`` is
        what stops :meth:`consolidate_rotated_devices` from later folding them
        into each other once only one of the two is online.

        Only randomized addresses are considered - a burned-in MAC never rotates,
        so it is never a consolidation candidate and a co-presence record for it
        would carry no weight.
        """
        by_name: dict = {}
        for mac in active_macs:
            device = db_devices.get(mac)
            if device is None or not vendor_service.is_randomized_mac(mac):
                continue
            name = normalise_hostname(device.custom_name) or normalise_hostname(device.hostname)
            if not name:
                continue
            by_name.setdefault(name, []).append(mac)

        for name, macs in by_name.items():
            if len(macs) < 2:
                continue
            ordered = sorted(macs)
            for i in range(len(ordered)):
                for j in range(i + 1, len(ordered)):
                    mac_a, mac_b = ordered[i], ordered[j]
                    existing = (
                        await session.execute(
                            select(DeviceCoexistence).where(
                                DeviceCoexistence.mac_a == mac_a,
                                DeviceCoexistence.mac_b == mac_b,
                            )
                        )
                    ).scalar_one_or_none()
                    if existing is not None:
                        existing.last_seen_together = now_utc
                        existing.observations += 1
                        continue
                    session.add(DeviceCoexistence(
                        mac_a=mac_a,
                        mac_b=mac_b,
                        hostname=name,
                        first_seen_together=now_utc,
                        last_seen_together=now_utc,
                        observations=1,
                    ))
                    logger.info(
                        f"Recorded co-presence of {mac_a} and {mac_b} (both '{name}'): "
                        f"two devices sharing one name, they will not be auto-merged"
                    )

