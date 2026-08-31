"""Recognising a device that came back wearing a new MAC address.

Phones and laptops no longer present a stable hardware address. iOS, Android and
Windows all generate a *private* (locally-administered) MAC per network, and they
generate a **new one** whenever that network changes identity - a renamed SSID, a
changed passphrase, a switch between security modes, or simply the user choosing
"forget this network" and joining again.

To the router that is not the same device reconnecting. It is a first-time
arrival: a MAC never seen before, requesting a fresh DHCP lease. Discovery
faithfully created a second row for it, and the household ended up with three
"iPhone" records - one real phone and two ghosts of the addresses it used before
the Wi-Fi was reconfigured. Everything attached to identity went with the ghost:
the owner, the custom name, the speed limit, the traffic history.

What makes the real case recognisable is that the old address does not merely go
quiet - it *vanishes from the router entirely* at the moment the new one appears,
because there was only ever one radio. So the signature is:

    a never-before-seen private MAC arrives
    carrying a hostname that exactly one known device already answers to
    and that known device's address is nowhere on the router any more

When all three hold, the honest reading is not "a new device" but "the same
device, new address", and the existing row is updated in place. Keeping the row
is the entire point: identity, ownership and history stay attached to the device
they describe.

The one thing this must not do is guess. Apple ships phones whose default
hostname is the bare word "iPhone", so two different handsets in one house can
share it. A generic name is therefore never enough on its own: those cases are
reported and left for the operator to confirm through the existing merge
suggestions, which is a slower answer but a true one.
"""

import logging
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from backend.app.db.models import Device
from backend.app.services.vendor_lookup import vendor_service

logger = logging.getLogger("mikroman.mac_rotation")

# Hostnames that identify a product rather than a particular unit. A house can
# hold two of any of these, so a match on one of them is not evidence that two
# records describe the same physical device.
#
# Kept deliberately small: only names a vendor actually ships as the factory
# default. Anything the owner has personalised - "Kristina-iPhone", "Pixel-9-Pro-XL" -
# is distinctive and does not belong here.
GENERIC_HOSTNAMES = frozenset({
    "iphone",
    "ipad",
    "ipod",
    "android",
    "android-phone",
    "localhost",
    "macbook",
    "macbook-air",
    "macbook-pro",
    "windows-phone",
    "unknown",
    "device",
})

MIN_HOSTNAME_LENGTH = 3


def normalise_hostname(raw: Optional[str]) -> Optional[str]:
    """Fold a hostname to the form two records are compared in.

    DHCP hostnames arrive with inconsistent case and the occasional trailing
    dot, and macOS appends ".local". None of that distinguishes two devices.
    """
    if not raw:
        return None
    name = raw.strip().lower().rstrip(".")
    if name.endswith(".local"):
        name = name[: -len(".local")]
    name = name.strip()
    return name or None


def is_generic_hostname(name: Optional[str]) -> bool:
    """True when the hostname names a product line rather than one unit."""
    normalised = normalise_hostname(name)
    if not normalised or len(normalised) < MIN_HOSTNAME_LENGTH:
        return True
    return normalised in GENERIC_HOSTNAMES


def find_rotation_candidate(
    new_mac: str,
    new_hostname: Optional[str],
    known_devices: Iterable[Device],
    present_macs: Set[str],
) -> Optional[Device]:
    """The known device this new address almost certainly belongs to, or None.

    Args:
        new_mac: The address that has just appeared on the router.
        new_hostname: The DHCP hostname it presented, if any.
        known_devices: Every device already in the inventory.
        present_macs: Every address the router can see *right now*, across DHCP
            leases, the ARP table and wireless registrations. Membership here is
            what separates "this device rotated its address" from "this device
            has a second adapter and both are in use".

    Returns None whenever the answer is not certain - an unrecognisable
    hostname, a generic one, no candidate, or more than one. A wrong adoption
    silently hands one person's device to another, so ambiguity is always
    resolved by declining to act.
    """
    # Only private addresses rotate. A burned-in OUI address is stable, so a new
    # one genuinely is a new adapter.
    if not vendor_service.is_randomized_mac(new_mac):
        return None

    hostname = normalise_hostname(new_hostname)
    if not hostname or is_generic_hostname(hostname):
        return None

    candidates: List[Device] = []
    for device in known_devices:
        if device.mac_address == new_mac:
            continue
        if normalise_hostname(device.hostname) != hostname and \
                normalise_hostname(device.custom_name) != hostname:
            continue
        # The device that rotated must have rotated *away* from a private
        # address; a device sitting on its hardware address did not move.
        if not vendor_service.is_randomized_mac(device.mac_address):
            continue
        # Still on the router under its old address, so both exist at once:
        # that is a second adapter, not a rotation. Linking covers it.
        if device.mac_address in present_macs:
            continue
        candidates.append(device)

    if len(candidates) != 1:
        if len(candidates) > 1:
            logger.info(
                f"MAC {new_mac} matches hostname '{hostname}' on "
                f"{len(candidates)} known devices; too ambiguous to adopt "
                f"automatically, leaving it for merge suggestions"
            )
        return None

    return candidates[0]


def canonical_pair(mac_a: str, mac_b: str) -> Tuple[str, str]:
    """The ``(low, high)`` ordering a co-presence pair is stored and looked up in.

    ``device_coexistence`` holds one row per unordered pair; forcing a stable
    order here means both the writer and every reader agree on which address is
    ``mac_a``, so the unique constraint does its job and lookups never miss a
    pair just because the arguments arrived the other way round.
    """
    return (mac_a, mac_b) if mac_a <= mac_b else (mac_b, mac_a)


def collect_present_macs(*sources: Sequence) -> Set[str]:
    """Every MAC the router currently reports, across all discovery tables.

    Takes the raw DHCP lease, ARP and wireless registration lists. A device is
    "present" if any of them mentions it, because each table has blind spots:
    a lease outlives the client, an ARP entry expires while the radio link
    holds, and a wireless client may have no lease of its own.
    """
    macs: Set[str] = set()
    for source in sources:
        for entry in source or []:
            mac = getattr(entry, "mac_address", None)
            if mac:
                macs.add(mac)
    return macs
