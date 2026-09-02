"""RouterOS version compatibility for the API surface this app depends on.

Every RouterOS menu MikroMan touches is declared here together with the release
that introduced it, so the minimum supported version is derived from the code's
actual requirements rather than asserted in a README and left to rot.

Two classes of requirement:

* **Required** - the app cannot function without them. Their highest
  introduction version is the hard floor.
* **Optional** - features that light up when present and degrade quietly when
  not, either through an explicit fallback or by omitting a display. These never
  raise the floor; they are listed so the reason a feature is missing on an
  older router is answerable from the code.

Sources are MikroTik's own documentation and release announcements; each entry
carries the reasoning so a future change can be re-checked rather than trusted.
"""

import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger("mikroman.routeros_compat")


@dataclass(frozen=True)
class ApiRequirement:
    """One RouterOS menu or capability the app uses."""

    path: str
    since: Tuple[int, int, int]
    required: bool
    note: str


# Menus that predate RouterOS v7 entirely are recorded as 7.1: the REST
# transport is what actually gates them, not the menu.
_V7_REST = (7, 1, 0)

REQUIREMENTS: List[ApiRequirement] = [
    ApiRequirement(
        path="/rest",
        since=_V7_REST,
        required=True,
        note=(
            "The REST API itself. Added in 7.1beta4 and first shipped in a "
            "stable release with 7.1; there is no REST endpoint at all before "
            "that, so this is the hard floor for every deployment."
        ),
    ),
    ApiRequirement(
        path="/system/resource",
        since=_V7_REST,
        required=True,
        note="Board name, version, CPU and memory. Predates v7; gated only by REST.",
    ),
    ApiRequirement(
        path="/interface",
        since=_V7_REST,
        required=True,
        note="Interface inventory and counters. Predates v7; gated only by REST.",
    ),
    ApiRequirement(
        path="/interface/monitor-traffic",
        since=_V7_REST,
        required=True,
        note=(
            "Live interface rates, called as POST with 'once'. Predates v7; the "
            "REST wrapper is what makes it reachable."
        ),
    ),
    ApiRequirement(
        path="/ip/address",
        since=_V7_REST,
        required=True,
        note="WAN address resolution. Predates v7.",
    ),
    ApiRequirement(
        path="/ip/arp",
        since=_V7_REST,
        required=True,
        note="Device discovery and presence. Predates v7.",
    ),
    ApiRequirement(
        path="/ip/dhcp-server/lease",
        since=_V7_REST,
        required=True,
        note="Hostnames and lease state for discovered devices. Predates v7.",
    ),
    ApiRequirement(
        path="/ip/firewall/mangle",
        since=_V7_REST,
        required=True,
        note=(
            "Per-device byte accounting via passthrough rules - the primitive "
            "all traffic reporting is built on, because Simple Queue counters "
            "cannot be trusted on 7.x. Predates v7."
        ),
    ),
    ApiRequirement(
        path="/ip/firewall/filter",
        since=_V7_REST,
        required=True,
        note="Pause/block enforcement. Predates v7.",
    ),
    ApiRequirement(
        path="/ip/firewall/address-list",
        since=_V7_REST,
        required=True,
        note="Grouping targets for filter rules. Predates v7.",
    ),
    ApiRequirement(
        path="/queue/simple",
        since=_V7_REST,
        required=True,
        note="Bandwidth shaping (shaping only - never accounting). Predates v7.",
    ),
    ApiRequirement(
        path="/system/clock",
        since=_V7_REST,
        required=True,
        note=(
            "Router timezone offset, which anchors every daily boundary. The "
            "gmt-offset parser accepts both the '+05:00' and raw-seconds forms."
        ),
    ),
    # ---- Optional: absent means a smaller feature set, never a broken app ----
    ApiRequirement(
        path="/system/health",
        since=_V7_REST,
        required=False,
        note=(
            "Temperature and voltage. Not all boards report it, and the payload "
            "is a name/value table on v7 but a single record on older builds - "
            "both shapes are handled, and absence simply hides the tiles."
        ),
    ),
    ApiRequirement(
        path="/interface/wifi/registration-table",
        since=(7, 13, 0),
        required=False,
        note=(
            "The 'wifi' menu, renamed from 'wifiwave2' in 7.13 when the package "
            "was split into wifi-qcom / wifi-qcom-ac. Below 7.13 the client "
            "falls back to /interface/wireless/registration-table, so wireless "
            "presence and signal still work on the legacy package."
        ),
    ),
    ApiRequirement(
        path="/interface/wireless/registration-table",
        since=_V7_REST,
        required=False,
        note="Legacy wireless package. The fallback for routers below 7.13.",
    ),
    ApiRequirement(
        path="/interface/wifi/registration-table[mld-interfaces]",
        since=(7, 13, 0),
        required=False,
        note=(
            "Wi-Fi 7 multi-link fields (mld-interfaces, mld-link-addresses). "
            "MikroTik does not document an introduction version and they only "
            "appear on 802.11be hardware; verified present on 7.25. Read with "
            ".get(), so a router without them shows one link instead of "
            "several. Treated as 7.13 because they live in the wifi menu."
        ),
    ),
    ApiRequirement(
        path="/certificate",
        since=_V7_REST,
        required=False,
        note="Optional HTTPS auto-provisioning. Predates v7.",
    ),
    ApiRequirement(
        path="/ip/service",
        since=_V7_REST,
        required=False,
        note="Read to discover the www-ssl port and to enable the service during auto-provisioning; the port itself is never modified.",
    ),
    ApiRequirement(
        path="/file",
        since=_V7_REST,
        required=False,
        note="Certificate upload during auto-provisioning.",
    ),
    ApiRequirement(
        path="/system/reboot",
        since=_V7_REST,
        required=False,
        note="Operator-triggered reboot from the dashboard.",
    ),
]

# The hard floor is whatever the strictest required menu demands.
MINIMUM_VERSION: Tuple[int, int, int] = max(r.since for r in REQUIREMENTS if r.required)

# Running MikroMan inside a RouterOS container rather than on a Docker host
# needs the container package, which landed in 7.4beta.
CONTAINER_MINIMUM_VERSION: Tuple[int, int, int] = (7, 4, 0)

# Highest version this app has actually been exercised against. Anything above
# it is expected to work but is unverified, which is worth saying out loud
# rather than implying a guarantee.
VERIFIED_VERSION: Tuple[int, int, int] = (7, 25, 0)

_VERSION_RE = re.compile(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def format_version(version: Tuple[int, int, int]) -> str:
    """Render a version tuple the way MikroTik writes it (7.1, 7.13.2)."""
    major, minor, patch = version
    return f"{major}.{minor}" if patch == 0 else f"{major}.{minor}.{patch}"


def parse_version(raw: Optional[str]) -> Optional[Tuple[int, int, int]]:
    """Parse a RouterOS version string into a comparable tuple.

    RouterOS reports versions like ``7.25``, ``7.25.1``, ``7.16rc2`` and
    ``7.1beta4``, sometimes with a build suffix such as ``7.25_ab508``. Only the
    numeric head is significant for capability checks: a beta of 7.13 has the
    7.13 menus. Returns None for anything unparseable, so callers can tell
    "old router" apart from "could not tell".
    """
    if not raw:
        return None
    match = _VERSION_RE.match(str(raw))
    if not match:
        return None
    major = int(match.group(1))
    minor = int(match.group(2) or 0)
    patch = int(match.group(3) or 0)
    return (major, minor, patch)


@dataclass
class CompatibilityReport:
    """Verdict on one router's suitability, plus what it costs the user."""

    version: Optional[Tuple[int, int, int]]
    supported: bool
    # Features that will not work on this router, in user-facing terms.
    degraded: List[str]
    # Non-fatal notes: unparseable version, or newer than anything tested.
    warnings: List[str]

    @property
    def version_text(self) -> str:
        return format_version(self.version) if self.version else "unknown"


def check_version(raw_version: Optional[str]) -> CompatibilityReport:
    """Compare a router's reported version against what the app needs.

    Never raises and never refuses: an unrecognised or old router still gets a
    connection attempt, because MikroTik ships builds faster than this table is
    updated and a wrong guess must not lock someone out of their own router.
    The report is advisory.
    """
    version = parse_version(raw_version)
    if version is None:
        return CompatibilityReport(
            version=None,
            supported=True,
            degraded=[],
            warnings=[
                f"Could not read the RouterOS version from {raw_version!r}. "
                f"MikroMan needs {format_version(MINIMUM_VERSION)} or newer."
            ],
        )

    supported = version >= MINIMUM_VERSION
    degraded: List[str] = []
    warnings: List[str] = []

    if not supported:
        warnings.append(
            f"RouterOS {format_version(version)} is older than the minimum "
            f"{format_version(MINIMUM_VERSION)}: the REST API this app speaks "
            f"does not exist on it."
        )

    for requirement in REQUIREMENTS:
        if requirement.required or version >= requirement.since:
            continue
        degraded.append(
            f"{requirement.path} needs RouterOS "
            f"{format_version(requirement.since)}"
        )

    if version > VERIFIED_VERSION:
        warnings.append(
            f"RouterOS {format_version(version)} is newer than the highest "
            f"version MikroMan has been verified against "
            f"({format_version(VERIFIED_VERSION)}). If something reads wrong, "
            f"re-check the API surface in routeros_compat.py."
        )

    return CompatibilityReport(
        version=version,
        supported=supported,
        degraded=degraded,
        warnings=warnings,
    )


def log_compatibility(raw_version: Optional[str]) -> CompatibilityReport:
    """Run the check and record the outcome once, at connection time."""
    report = check_version(raw_version)
    for warning in report.warnings:
        logger.warning(warning)
    for note in report.degraded:
        logger.info(f"Feature unavailable on this RouterOS: {note}")
    return report
