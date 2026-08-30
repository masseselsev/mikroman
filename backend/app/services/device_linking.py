"""Grouping several network adapters of one physical machine into one device.

A machine with more than one network adapter presents a different MAC address on
each. A laptop docked over Ethernet and roaming over Wi-Fi was therefore
discovered as two unrelated devices, its traffic split between them and neither
row telling the whole story.

Linking marks a secondary adapter as belonging to a primary device. The group is
"the primary plus everything pointing at it", so it is always exactly one level
deep: linking onto a secondary resolves to that secondary's primary.

This is deliberately distinct from *merging*, which exists for MAC rotation and
collapses two records into one because only one of the addresses is real. Here
both addresses are genuine and may be in use at different times, so both rows
are kept and simply presented together.
"""
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import Device

logger = logging.getLogger("mikroman.device_linking")

# Interface name fragments that identify wireless media. 'mld' covers the
# WiFi 7 multi-link interfaces RouterOS creates on newer hardware.
_WIRELESS_HINTS = ("wifi", "wlan", "wl", "mld", "wireless")


class LinkSuggestion(BaseModel):
    """A proposal to treat two discovered devices as one physical machine."""

    device_id: int
    primary_device_id: int
    device_name: str
    primary_device_name: str
    device_connection: Optional[str] = None
    primary_connection: Optional[str] = None
    confidence: float
    reason: str


def classify_connection(interface: Optional[str], signal: Optional[int]) -> Optional[str]:
    """Classify an adapter as wired or wireless.

    A signal reading is conclusive - only a wireless association produces one,
    and some drivers report wireless clients against the bridge rather than the
    radio. Otherwise the interface name is used.
    """
    if signal is not None:
        return "wireless"
    if not interface:
        return None
    name = interface.lower()
    if any(hint in name for hint in _WIRELESS_HINTS):
        return "wireless"
    return "wired"


async def _resolve_primary(session: AsyncSession, device_id: int) -> int:
    """Follow a device to the head of its group, so groups never nest."""
    device = await session.get(Device, device_id)
    if device and device.linked_to_device_id:
        return device.linked_to_device_id
    return device_id


async def link_device(session: AsyncSession, device_id: int, primary_device_id: int) -> Device:
    """Attach ``device_id`` to ``primary_device_id`` as an extra adapter."""
    primary_id = await _resolve_primary(session, primary_device_id)

    if device_id == primary_id:
        raise ValueError("A device cannot be linked to itself")

    device = await session.get(Device, device_id)
    primary = await session.get(Device, primary_id)
    if not device or not primary:
        raise ValueError("Device not found")

    # Anything already attached to the device being linked moves up with it,
    # keeping the group one level deep.
    children = (await session.execute(
        select(Device).where(Device.linked_to_device_id == device_id)
    )).scalars().all()
    for child in children:
        child.linked_to_device_id = primary_id

    device.linked_to_device_id = primary_id
    device.connection_kind = classify_connection(device.last_interface, device.last_wifi_signal)
    primary.connection_kind = classify_connection(primary.last_interface, primary.last_wifi_signal)
    # A secondary adapter inherits ownership: it is the same machine.
    if primary.user_id and device.user_id != primary.user_id:
        device.user_id = primary.user_id

    await session.commit()
    await session.refresh(device)
    logger.info(f"Linked device {device_id} as an adapter of device {primary_id}")
    return device


async def unlink_device(session: AsyncSession, device_id: int) -> Device:
    """Detach an adapter so it stands as its own device again."""
    device = await session.get(Device, device_id)
    if not device:
        raise ValueError("Device not found")
    device.linked_to_device_id = None
    await session.commit()
    await session.refresh(device)
    return device


async def build_device_groups(
    session: AsyncSession,
    devices: Optional[List[Device]] = None
) -> List[Dict[str, Any]]:
    """Collapse devices into logical machines.

    Each group reports the primary device, every adapter belonging to it, and
    state aggregated across them: the machine is online while any adapter is,
    and its active interfaces are those currently carrying it.
    """
    if devices is None:
        devices = list((await session.execute(select(Device))).scalars().all())

    by_id = {d.id: d for d in devices}
    groups: Dict[int, List[Device]] = {}

    for device in devices:
        # An adapter whose primary is not in this result set is treated as its
        # own device rather than silently disappearing.
        head = device.linked_to_device_id if device.linked_to_device_id in by_id else device.id
        groups.setdefault(head, []).append(device)

    result: List[Dict[str, Any]] = []
    for head_id, adapters in groups.items():
        # Primary first, then the rest in discovery order.
        adapters.sort(key=lambda d: (d.id != head_id, d.id))
        primary = by_id.get(head_id, adapters[0])
        active = [a for a in adapters if a.is_active]
        result.append({
            "primary": primary,
            "adapters": adapters,
            "is_active": len(active) > 0,
            "active_interfaces": [a.last_interface for a in active if a.last_interface],
            "connection_kinds": sorted({
                classify_connection(a.last_interface, a.last_wifi_signal)
                for a in adapters
                if classify_connection(a.last_interface, a.last_wifi_signal)
            }),
        })

    result.sort(key=lambda g: g["primary"].id)
    return result


async def find_link_suggestions(session: AsyncSession) -> List[LinkSuggestion]:
    """Propose adapters that look like they belong to the same machine.

    The signal is a shared DHCP hostname across two different MAC addresses on
    different media - which is exactly what a dual-homed laptop reports.
    """
    devices = list((await session.execute(select(Device))).scalars().all())
    unlinked = [d for d in devices if d.linked_to_device_id is None]

    # Group candidates by normalised hostname.
    by_hostname: Dict[str, List[Device]] = {}
    for device in unlinked:
        name = (device.hostname or device.custom_name or "").strip().lower()
        if len(name) < 3:
            continue
        by_hostname.setdefault(name, []).append(device)

    linked_ids = {d.linked_to_device_id for d in devices if d.linked_to_device_id}

    suggestions: List[LinkSuggestion] = []
    for name, candidates in by_hostname.items():
        if len(candidates) < 2:
            continue
        # Oldest record is the most established, so it becomes the primary.
        candidates.sort(key=lambda d: d.id)
        primary = candidates[0]
        if primary.id in linked_ids:
            continue

        primary_kind = classify_connection(primary.last_interface, primary.last_wifi_signal)
        for other in candidates[1:]:
            other_kind = classify_connection(other.last_interface, other.last_wifi_signal)
            # Different media is the strongest case; the same medium twice is
            # more likely a rotated MAC, which merging already handles.
            confidence = 0.92 if (primary_kind and other_kind and primary_kind != other_kind) else 0.75
            if confidence < 0.8:
                continue
            suggestions.append(LinkSuggestion(
                device_id=other.id,
                primary_device_id=primary.id,
                device_name=other.custom_name or other.hostname or other.mac_address,
                primary_device_name=primary.custom_name or primary.hostname or primary.mac_address,
                device_connection=other_kind,
                primary_connection=primary_kind,
                confidence=confidence,
                reason=(
                    f"'{primary.hostname or name}' appears on both a {primary_kind} "
                    f"and a {other_kind} adapter - likely one machine"
                ),
            ))

    return suggestions
