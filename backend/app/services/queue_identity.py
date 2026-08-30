"""Identity and comparison helpers for RouterOS-managed objects.

RouterOS normalises values it stores: host targets gain an explicit ``/32``
suffix and rate limits are expanded to bits per second (``5M`` -> ``5000000``).
Comparing the application's own representation verbatim against the value read
back therefore never matches, which previously caused the background sync worker
to rewrite every managed queue on every poll tick.

Centralising the comparison rules here keeps the traffic controller and the
analytics engine in agreement about when two RouterOS objects are "the same".
"""
import re
from typing import Any, FrozenSet, Optional

# Managed-object comment tags. Identifiers are used rather than user-supplied
# names so a rename never orphans a queue, and so that no name can be a prefix
# of another (":managed:M" used to match ":managed:Mark").
USER_QUEUE_COMMENT = "mikroman:managed:user_{user_id}"
DEVICE_QUEUE_COMMENT = "mikroman:managed:dev_{device_id}"
USER_QUEUED_COMMENT = "mikroman:queued:user_{user_id}"
DEVICE_QUEUED_COMMENT = "mikroman:queued:dev_{device_id}"

_RATE_UNITS = {"": 1, "k": 1_000, "m": 1_000_000, "g": 1_000_000_000}
_RATE_TOKEN = re.compile(r"^(\d+(?:\.\d+)?)\s*([kKmMgG]?)$")


def normalize_target(target: Optional[str]) -> FrozenSet[str]:
    """Normalise a Simple Queue ``target`` into an order-independent CIDR set.

    A bare host address is equivalent to the same address with an explicit
    ``/32``; RouterOS always reports the latter. Real subnets keep their own
    prefix length so ``192.168.88.0/24`` never collapses onto a host route.
    """
    entries = set()
    for part in (target or "").split(","):
        candidate = part.strip()
        if not candidate:
            continue
        if "/" not in candidate:
            candidate = f"{candidate}/32"
        entries.add(candidate)
    return frozenset(entries)


def _rate_token_to_bps(token: str) -> Optional[int]:
    """Convert a single RouterOS rate token (``5M``, ``512k``, ``0``) to bps."""
    match = _RATE_TOKEN.match(token.strip())
    if not match:
        return None
    value, unit = match.groups()
    return int(float(value) * _RATE_UNITS[unit.lower()])


def normalize_rate_limit(limit: Optional[str]) -> str:
    """Normalise a ``max-limit`` pair into canonical ``bps/bps`` form.

    Unparseable input is returned unchanged so an unexpected RouterOS value
    compares unequal and triggers a corrective write rather than being ignored.
    """
    if not limit:
        return "0/0"
    parts = limit.split("/")
    if len(parts) != 2:
        return limit.strip()
    upload, download = (_rate_token_to_bps(p) for p in parts)
    if upload is None or download is None:
        return limit.strip()
    return f"{upload}/{download}"


def normalize_parent(parent: Optional[str]) -> Optional[str]:
    """Normalise a queue ``parent``; RouterOS reports ``none`` for top-level."""
    if parent is None:
        return None
    cleaned = parent.strip()
    if not cleaned or cleaned.lower() == "none":
        return None
    return cleaned


def _comment_of(queue: Any) -> str:
    return (getattr(queue, "comment", None) or "").strip()


def queue_matches_user(queue: Any, user_id: int, user_name: str) -> bool:
    """Whether ``queue`` is the managed Simple Queue for the given user.

    Matching is exact. The legacy name-based tag is still recognised so queues
    created by earlier versions are adopted and migrated rather than duplicated.
    """
    comment = _comment_of(queue)
    if comment == USER_QUEUE_COMMENT.format(user_id=user_id):
        return True
    if comment == f"mikroman:managed:{user_name}":  # legacy tag, exact match only
        return True
    if comment:
        # A tagged queue that belongs to something else must never be adopted.
        return False
    return getattr(queue, "name", None) in (f"mikroman-{user_name}", f"mikroman-user-{user_id}")


def queue_matches_device(queue: Any, device_id: int) -> bool:
    """Whether ``queue`` is the managed child Simple Queue for the given device."""
    comment = _comment_of(queue)
    if comment == DEVICE_QUEUE_COMMENT.format(device_id=device_id):
        return True
    if comment:
        return False
    return getattr(queue, "name", None) == f"mikroman-dev-{device_id}"
