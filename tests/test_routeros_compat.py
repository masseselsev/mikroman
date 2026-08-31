"""RouterOS version floor: parsing, the derived minimum, and the advisories.

The table in routeros_compat.py is the single place that says which RouterOS
releases this app supports. These tests pin the behaviour that depends on it, so
adding a menu that needs a newer release cannot silently raise the floor without
someone noticing.
"""

from backend.app.services.routeros_compat import (
    CONTAINER_MINIMUM_VERSION,
    MINIMUM_VERSION,
    REQUIREMENTS,
    VERIFIED_VERSION,
    check_version,
    format_version,
    parse_version,
)


class TestParseVersion:
    def test_plain_two_part_version(self):
        assert parse_version("7.25") == (7, 25, 0)

    def test_three_part_version(self):
        assert parse_version("7.13.2") == (7, 13, 2)

    def test_prerelease_suffix_is_ignored(self):
        # A beta of 7.13 has the 7.13 menus, so the suffix carries no
        # capability information.
        assert parse_version("7.1beta4") == (7, 1, 0)
        assert parse_version("7.16rc2") == (7, 16, 0)

    def test_build_suffix_is_ignored(self):
        assert parse_version("7.25_ab508") == (7, 25, 0)

    def test_leading_v_is_tolerated(self):
        assert parse_version("v7.4") == (7, 4, 0)

    def test_unparseable_returns_none(self):
        assert parse_version(None) is None
        assert parse_version("") is None
        assert parse_version("unknown") is None

    def test_ordering_is_numeric_not_lexicographic(self):
        # "7.9" > "7.13" as strings; as versions it is the other way round.
        assert parse_version("7.13") > parse_version("7.9")


class TestMinimumVersion:
    def test_floor_is_the_rest_api_release(self):
        # REST is what every required menu depends on; nothing required needs
        # anything newer. If this fails, a new required menu raised the floor
        # and the README plus the policy doc must be updated to match.
        assert MINIMUM_VERSION == (7, 1, 0)

    def test_container_deployment_needs_a_newer_release(self):
        assert CONTAINER_MINIMUM_VERSION > MINIMUM_VERSION

    def test_no_required_entry_exceeds_the_declared_minimum(self):
        for requirement in REQUIREMENTS:
            if requirement.required:
                assert requirement.since <= MINIMUM_VERSION, requirement.path

    def test_every_requirement_documents_its_reasoning(self):
        for requirement in REQUIREMENTS:
            assert requirement.note.strip(), requirement.path

    def test_format_version_matches_mikrotik_notation(self):
        assert format_version((7, 1, 0)) == "7.1"
        assert format_version((7, 13, 2)) == "7.13.2"


class TestCheckVersion:
    def test_current_router_is_supported_with_nothing_degraded(self):
        report = check_version("7.25")
        assert report.supported
        assert report.degraded == []
        assert report.warnings == []

    def test_version_below_the_floor_is_flagged(self):
        report = check_version("6.49.7")
        assert not report.supported
        assert any("older than the minimum" in w for w in report.warnings)

    def test_router_below_the_wifi_menu_rename_reports_degraded_features(self):
        # 7.12 predates the wifiwave2 -> wifi rename, so the new menu is absent.
        # The app still works: it falls back to the legacy wireless menu.
        report = check_version("7.12")
        assert report.supported
        assert any("/interface/wifi/registration-table" in d for d in report.degraded)

    def test_router_at_the_rename_has_the_wifi_menu(self):
        report = check_version("7.13")
        assert not any("/interface/wifi/registration-table" in d for d in report.degraded)

    def test_unknown_version_warns_but_does_not_block(self):
        # A wrong guess must never lock someone out of their own router.
        report = check_version("something-unexpected")
        assert report.supported
        assert report.version is None
        assert report.version_text == "unknown"
        assert report.warnings

    def test_version_newer_than_verified_says_so(self):
        newer = (VERIFIED_VERSION[0], VERIFIED_VERSION[1] + 5, 0)
        report = check_version(format_version(newer))
        assert report.supported
        assert any("newer than the highest" in w for w in report.warnings)
