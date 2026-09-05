"""Deterministic classifier for RouterOS log entries.

Categorizes and evaluates the severity of log entries emitted by MikroTik
RouterOS devices based on topics and message content.
"""

from __future__ import annotations

import re
from typing import Tuple

# Pre-compiled regex patterns for performance
RE_AUTH = re.compile(
    r"(login failure|logged in|logged out|authentication failed|user .* logged|invalid user)",
    re.IGNORECASE,
)
RE_INTERFACE = re.compile(
    r"(link down|link up|excessive collisions|fcs error|loop detected)",
    re.IGNORECASE,
)
RE_DHCP = re.compile(
    r"(assigned|deassigned|offered|lease|conflict detected|address pool .* empty)",
    re.IGNORECASE,
)
RE_WIRELESS = re.compile(
    r"(connected|disconnected|associated|disassociated|roamed|capsman|signal strength|deauthenticated)",
    re.IGNORECASE,
)
RE_FIREWALL = re.compile(
    r"(drop|reject|forward:|input:|output:|raw:)",
    re.IGNORECASE,
)
RE_CRITICAL = re.compile(
    r"(critical|fatal|kernel failure|kernel panic|router rebooted without proper shutdown)",
    re.IGNORECASE,
)
RE_ERROR = re.compile(
    r"(error|failed|failure|rejected)",
    re.IGNORECASE,
)
RE_WARNING = re.compile(
    r"(warning|conflict|unreachable|timeout|dropped)",
    re.IGNORECASE,
)

# RouterOS logs a REST session's login/logout as a pair of "account" lines a
# few seconds apart, keyed by the same account name and source address every
# time - MikroMan's own polling produces one such pair roughly every ten
# minutes even with keep-alive working (RouterOS ages the REST session out on
# its own timer, unrelated to client-side connection reuse). Only the variant
# carrying a source address is matched: the sibling line without one
# ("... via api", no "from <ip>") has nothing to compare an IP against, so it
# is left alone rather than assumed to belong to the same event.
RE_API_LOGIN = re.compile(
    r"^user (\S+) logged (in|out) from (\S+) via (?:api|rest-api)$",
    re.IGNORECASE,
)


def is_self_api_login(message: str, username: str, own_ip: str) -> bool:
    """True when ``message`` is a login/logout line for exactly this account
    from exactly this address.

    Deliberately narrow: both the account name *and* the source address must
    match before a line is treated as MikroMan's own routine polling. Matching
    on the account name alone would also hide a genuine anomaly - someone else
    reaching the router with the same (shared) API credential from a different
    machine - which is exactly the case an operator most wants to still see.
    """
    if not username or not own_ip:
        return False
    m = RE_API_LOGIN.match((message or "").strip())
    if not m:
        return False
    return m.group(1) == username and m.group(3) == own_ip


def classify_log_entry(topics: str, message: str) -> Tuple[str, str]:
    """Classify a log entry returning (severity, category).

    Severity: "critical" | "error" | "warning" | "info"
    Category: "auth" | "interface" | "dhcp" | "wireless" | "firewall" | "system"
    """
    topics_lower = (topics or "").lower()
    msg = message or ""

    # 1. Determine Category
    category = "system"
    if "account" in topics_lower or RE_AUTH.search(msg):
        category = "auth"
    elif "interface" in topics_lower or RE_INTERFACE.search(msg):
        category = "interface"
    elif "dhcp" in topics_lower or RE_DHCP.search(msg):
        category = "dhcp"
    elif "wireless" in topics_lower or "caps" in topics_lower or RE_WIRELESS.search(msg):
        category = "wireless"
    elif "firewall" in topics_lower or "raw" in topics_lower or RE_FIREWALL.search(msg):
        category = "firewall"

    # 2. Determine Severity
    severity = "info"
    if "critical" in topics_lower or RE_CRITICAL.search(msg):
        severity = "critical"
    elif "error" in topics_lower or (category == "auth" and "failure" in msg.lower()) or RE_ERROR.search(msg):
        severity = "error"
    elif "warning" in topics_lower or RE_WARNING.search(msg):
        severity = "warning"

    return severity, category
