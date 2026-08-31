"""Turning RouterOS field values into Python ones.

RouterOS answers REST calls with strings, and several of its fields carry more
than one value in one string - a WiFi 7 multi-link association reports one
signal per link, comma separated, and ``gmt-offset`` appears in at least three
different shapes across releases. Parsing lives here, apart from the transport,
so each rule can be tested against real observed values without a router.
"""
from typing import Any, List, Optional

from backend.app.schemas.routeros import WiFiLinkDTO


def parse_signal_list(raw: Optional[Any]) -> List[int]:
    """Parse a RouterOS signal field into dBm values.

    A single-link association reports one value ("-62"). A multi-link (WiFi 7
    MLO) association may report one value per link, comma separated.
    """
    if raw is None:
        return []
    values = []
    for part in str(raw).split(","):
        token = part.strip()
        if token and token.lstrip("-").isdigit():
            values.append(int(token))
    return values


def parse_gmt_offset_minutes(raw: Optional[str]) -> Optional[int]:
    """Parse a RouterOS ``gmt-offset`` into minutes east of UTC.

    Accepts the ``+05:00`` form, a bare ``+05``, and the raw-seconds form some
    RouterOS versions report. Returns None when the value cannot be understood,
    so the dashboard shows no router clock rather than a wrong one.
    """
    if raw is None:
        return None
    token = str(raw).strip()
    if not token:
        return None

    # Raw seconds, e.g. "18000" or "-10800".
    if token.lstrip("+-").isdigit() and ":" not in token:
        value = int(token)
        # Values small enough to be hours are treated as hours, not seconds.
        return value // 60 if abs(value) > 60 else value * 60

    sign = -1 if token.startswith("-") else 1
    body = token.lstrip("+-")
    parts = body.split(":")
    try:
        hours = int(parts[0])
        minutes = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return None
    return sign * (hours * 60 + minutes)


def parse_uptime_seconds(raw: Optional[str]) -> Optional[int]:
    """Parse a RouterOS uptime string into seconds.

    RouterOS reports uptime as a compact run of unit-suffixed parts, e.g.
    ``"38m35s"``, ``"1d3h58m3s"``, ``"6w2d5h"``. A bare integer (seconds) is
    also accepted. Returns None when the value cannot be understood.

    Used to detect a reboot: if uptime has gone *backwards* between two polls
    the router restarted, and every byte counter on it reset to zero at that
    moment - which the traffic accounting has to know so it credits the bytes
    since the reboot rather than a nonsensical delta against a stale baseline.
    """
    if raw is None:
        return None
    token = str(raw).strip().lower()
    if not token:
        return None
    if token.isdigit():
        return int(token)

    units = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}
    total = 0
    number = ""
    seen = False
    for ch in token:
        if ch.isdigit():
            number += ch
        elif ch in units and number:
            total += int(number) * units[ch]
            number = ""
            seen = True
        else:
            return None
    if number:  # trailing digits with no unit
        return None
    return total if seen else None


def build_wifi_links(
    interface: str,
    band: Optional[str],
    signals: List[int],
    mld_interfaces: Optional[str],
    mld_link_addresses: Optional[str],
) -> List[WiFiLinkDTO]:
    """Expand a registration entry into its individual radio links.

    RouterOS reports a WiFi 7 multi-link client as one entry on the ``mld*``
    interface, carrying parallel comma-separated lists of the member radios
    (``mld-interfaces``) and the per-link MAC addresses (``mld-link-addresses``).
    A conventional single-link client has neither, and yields one link.

    When the router reports fewer signal readings than links, the readings are
    assigned in order and the remaining links report no signal rather than
    repeating a value that was not measured for them.
    """
    members = [p.strip() for p in (mld_interfaces or "").split(",") if p.strip()]
    addresses = [p.strip().upper() for p in (mld_link_addresses or "").split(",") if p.strip()]

    if not members:
        return [WiFiLinkDTO(
            interface=interface,
            mac_address=addresses[0] if addresses else None,
            signal_strength=signals[0] if signals else None,
            band=band,
        )]

    links = []
    for index, member in enumerate(members):
        links.append(WiFiLinkDTO(
            interface=member,
            mac_address=addresses[index] if index < len(addresses) else None,
            signal_strength=signals[index] if index < len(signals) else None,
            band=band,
        ))
    return links
