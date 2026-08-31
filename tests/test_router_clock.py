"""Router clock and timezone.

The dashboard reports times in the router's own timezone, because that is the
frame of reference for everything it shows - lease times, billing cycles and
uptime all come from the router. The container commonly runs UTC while the
router sits in a local zone, which made "today" mean two different things.

The offset is sent once and the browser advances the clock itself, so showing a
live time costs no extra polling.
"""
from backend.app.services.routeros import parse_gmt_offset_minutes, parse_uptime_seconds

# Verbatim from a live hAP be^3 on RouterOS 7.25.
LIVE_CLOCK = {
    "date": "2026-08-31",
    "dst-active": "false",
    "gmt-offset": "+05:00",
    "time": "03:58:22",
    "time-zone-autodetect": "true",
    "time-zone-name": "Asia/Tashkent",
}


def test_parse_positive_offset():
    assert parse_gmt_offset_minutes(LIVE_CLOCK["gmt-offset"]) == 300


def test_parse_negative_and_half_hour_offsets():
    assert parse_gmt_offset_minutes("-03:30") == -210
    assert parse_gmt_offset_minutes("+05:45") == 345
    assert parse_gmt_offset_minutes("+00:00") == 0
    assert parse_gmt_offset_minutes("-08:00") == -480


def test_parse_accepts_seconds_form_and_bare_hours():
    """RouterOS has reported the offset in more than one shape across versions."""
    assert parse_gmt_offset_minutes("18000") == 300
    assert parse_gmt_offset_minutes("+05") == 300


def test_unparseable_offset_yields_none_rather_than_a_wrong_time():
    assert parse_gmt_offset_minutes(None) is None
    assert parse_gmt_offset_minutes("") is None
    assert parse_gmt_offset_minutes("not-an-offset") is None


class TestParseUptimeSeconds:
    """Uptime is parsed only to detect a reboot - if it drops between polls the
    router restarted and every byte counter on it reset to zero."""

    def test_the_compact_unit_run_routeros_actually_emits(self):
        assert parse_uptime_seconds("38m35s") == 38 * 60 + 35
        assert parse_uptime_seconds("1d3h58m3s") == 86400 + 3 * 3600 + 58 * 60 + 3
        assert parse_uptime_seconds("6w2d5h") == 6 * 604800 + 2 * 86400 + 5 * 3600

    def test_a_bare_second_count(self):
        assert parse_uptime_seconds("4500") == 4500
        assert parse_uptime_seconds("0") == 0

    def test_seconds_only(self):
        assert parse_uptime_seconds("45s") == 45

    def test_a_reboot_is_a_drop(self):
        # This is the whole point of parsing it.
        before = parse_uptime_seconds("2h0m0s")
        after = parse_uptime_seconds("45s")
        assert after < before

    def test_garbage_yields_none_rather_than_a_wrong_number(self):
        assert parse_uptime_seconds(None) is None
        assert parse_uptime_seconds("") is None
        assert parse_uptime_seconds("up 3 days") is None
        assert parse_uptime_seconds("3h5") is None  # trailing digits, no unit
