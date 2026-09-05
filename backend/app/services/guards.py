"""Write safety guards for RouterOS mutations.

Prevents self-lockout, accidental throttling of management endpoints,
pruning of foreign WinBox queues/rules, and invalid rate configurations.
"""
import re
from typing import Optional, Set

RATE_PATTERN = re.compile(r"^(\d+)([kKMGT]?)/(\d+)([kKMGT]?)$")
MULTIPLIERS = {
    "": 1,
    "k": 1_000,
    "K": 1_000,
    "M": 1_000_000,
    "m": 1_000_000,
    "G": 1_000_000_000,
    "g": 1_000_000_000,
    "T": 1_000_000_000_000,
    "t": 1_000_000_000_000,
}

IMMUNE_WILDCARDS = {"0.0.0.0", "0.0.0.0/0", "::/0", "255.255.255.255"}
IMMUNE_LOOPBACKS = {"127.0.0.1", "::1", "localhost"}


class WriteGuardViolation(ValueError):
    """Raised when an operation is refused by a RouterOS write safety guard."""

    def __init__(self, guard_name: str, reason: str, target: str):
        super().__init__(f"[WriteGuard] [{guard_name}] Refused write for {target}: {reason}")
        self.guard_name = guard_name
        self.reason = reason
        self.target = target


def parse_bps(val: str) -> int:
    """Parse bandwidth rate like '5M', '100k', or '0' into bits per second."""
    raw = str(val).strip()
    if raw.isdigit():
        return int(raw)
    match = re.match(r"^(\d+)([kKMGT]?)$", raw)
    if not match:
        raise ValueError(f"Invalid rate string: {val}")
    num, unit = match.groups()
    return int(num) * MULTIPLIERS.get(unit, 1)


def parse_pair(pair_str: str) -> tuple[int, int]:
    """Parse a pair rate like '5M/10M' or '0/0' into (upload_bps, download_bps)."""
    clean = str(pair_str).strip()
    if clean in ("0", "0/0", "unlimited", "none"):
        return 0, 0
    match = RATE_PATTERN.match(clean)
    if not match:
        raise ValueError(f"Invalid rate pair format: {pair_str}")
    up_num, up_unit, down_num, down_unit = match.groups()
    return (
        int(up_num) * MULTIPLIERS.get(up_unit, 1),
        int(down_num) * MULTIPLIERS.get(down_unit, 1),
    )


def guard_immune_targets(target: str, immune_ips: Set[str], action: str = "block") -> None:
    """Refuse blocking or throttling of immune infrastructure and host targets."""
    clean_target = str(target).strip()
    ip_only = clean_target.split("/")[0]

    if ip_only in IMMUNE_LOOPBACKS or clean_target in IMMUNE_LOOPBACKS:
        raise WriteGuardViolation(
            "ImmuneTargetGuard",
            "Target is a loopback address and cannot be modified",
            clean_target,
        )

    if clean_target in IMMUNE_WILDCARDS:
        raise WriteGuardViolation(
            "ImmuneTargetGuard",
            "Target is a wildcard/broadcast and cannot be throttled or blocked",
            clean_target,
        )

    if ip_only in immune_ips or clean_target in immune_ips:
        raise WriteGuardViolation(
            "ImmuneTargetGuard",
            f"Target {clean_target} is a protected management/host IP",
            clean_target,
        )


def guard_foreign_resources(comment: Optional[str], action: str, resource_type: str) -> None:
    """Refuse destructive operations on resources not managed by MikroMan."""
    c = (comment or "").strip()
    if not c.startswith("mikroman:"):
        raise WriteGuardViolation(
            "ForeignResourceGuard",
            f"Cannot {action} foreign {resource_type} without 'mikroman:' comment prefix",
            c or "<empty>",
        )


def guard_queue_invariants(
    target: str,
    max_limit: str,
    limit_at: Optional[str] = None,
    parent: Optional[str] = None,
    name: Optional[str] = None,
) -> None:
    """Verify queue parameters comply with RouterOS relational requirements."""
    try:
        max_up, max_down = parse_pair(max_limit)
    except ValueError as e:
        raise WriteGuardViolation("QueueInvariantGuard", str(e), target)

    if limit_at:
        # Only the parse is wrapped. WriteGuardViolation subclasses ValueError,
        # so a violation raised inside a `try` whose `except` catches ValueError
        # would be caught and re-wrapped, nesting the message inside itself.
        try:
            at_up, at_down = parse_pair(limit_at)
        except ValueError as e:
            raise WriteGuardViolation("QueueInvariantGuard", str(e), target)

        if max_up > 0 and at_up > max_up:
            raise WriteGuardViolation(
                "QueueInvariantGuard",
                f"Upload limit-at ({at_up} bps) cannot exceed max-limit ({max_up} bps)",
                target,
            )
        if max_down > 0 and at_down > max_down:
            raise WriteGuardViolation(
                "QueueInvariantGuard",
                f"Download limit-at ({at_down} bps) cannot exceed max-limit ({max_down} bps)",
                target,
            )

    if parent and name and parent.strip() == name.strip():
        raise WriteGuardViolation(
            "QueueInvariantGuard",
            f"Queue cannot be its own parent: circular parentage on {name}",
            target,
        )
