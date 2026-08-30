"""WiFi 7 multi-link (MLO) association parsing.

RouterOS reports a multi-link client as a single entry on an ``mld*`` interface
carrying parallel comma-separated lists of the member radios and their per-link
MAC addresses. Displaying only the ``mld1`` name loses which radios the client
is actually using and how good each link is.

The payloads below are taken verbatim from a live hAP be^3 on RouterOS 7.25.
"""
from backend.app.services.routeros import build_wifi_links, parse_signal_list

# Real MLO client: Pixel 9 Pro XL associated over WiFi 7.
MLO_ENTRY = {
    "auth-type": "wpa3-psk-gd",
    "band": "5ghz-be",
    "dhcp-hostname": "Pixel-9-Pro-XL",
    "interface": "mld1",
    "mac-address": "C6:DA:93:39:1E:C5",
    "mld-interfaces": "wifi2",
    "mld-link-addresses": "C6:DA:93:39:1E:C6",
    "signal": "-62",
}

# Conventional single-link client on the same router.
SINGLE_ENTRY = {
    "band": "5ghz-ac",
    "interface": "wifi2",
    "mac-address": "74:4D:28:54:1C:5C",
    "signal": "-69",
}


def test_parse_signal_list_handles_single_and_multi_link():
    assert parse_signal_list("-62") == [-62]
    assert parse_signal_list("-55,-62,-71") == [-55, -62, -71]
    assert parse_signal_list(None) == []
    assert parse_signal_list("") == []
    # Junk must not crash the telemetry loop.
    assert parse_signal_list("n/a") == []
    assert parse_signal_list("-55, ,-62") == [-55, -62]


def test_single_link_client_yields_one_link():
    links = build_wifi_links(
        interface=SINGLE_ENTRY["interface"],
        band=SINGLE_ENTRY["band"],
        signals=parse_signal_list(SINGLE_ENTRY["signal"]),
        mld_interfaces=None,
        mld_link_addresses=None,
    )
    assert len(links) == 1
    assert links[0].interface == "wifi2"
    assert links[0].signal_strength == -69
    assert links[0].band == "5ghz-ac"


def test_mlo_client_reports_member_radios_not_the_mld_alias():
    """The useful answer is 'wifi2 at -62', not 'mld1'."""
    links = build_wifi_links(
        interface=MLO_ENTRY["interface"],
        band=MLO_ENTRY["band"],
        signals=parse_signal_list(MLO_ENTRY["signal"]),
        mld_interfaces=MLO_ENTRY["mld-interfaces"],
        mld_link_addresses=MLO_ENTRY["mld-link-addresses"],
    )
    assert len(links) == 1
    assert links[0].interface == "wifi2"
    assert links[0].mac_address == "C6:DA:93:39:1E:C6"
    assert links[0].signal_strength == -62
    assert links[0].band == "5ghz-be"


def test_mlo_client_across_several_radios():
    """A client bonded over three radios reports one link each, in order."""
    links = build_wifi_links(
        interface="mld1",
        band="5ghz-be",
        signals=parse_signal_list("-55,-62,-71"),
        mld_interfaces="wifi1,wifi2,wifi3",
        mld_link_addresses="AA:BB:CC:00:00:01,AA:BB:CC:00:00:02,AA:BB:CC:00:00:03",
    )
    assert [link.interface for link in links] == ["wifi1", "wifi2", "wifi3"]
    assert [link.signal_strength for link in links] == [-55, -62, -71]
    assert [link.mac_address for link in links] == [
        "AA:BB:CC:00:00:01", "AA:BB:CC:00:00:02", "AA:BB:CC:00:00:03",
    ]


def test_missing_per_link_signals_are_not_invented():
    """Fewer readings than links must leave the rest unknown, not duplicated."""
    links = build_wifi_links(
        interface="mld1",
        band="5ghz-be",
        signals=[-58],
        mld_interfaces="wifi1,wifi2",
        mld_link_addresses="AA:BB:CC:00:00:01",
    )
    assert links[0].signal_strength == -58
    assert links[1].signal_strength is None
    assert links[1].mac_address is None
