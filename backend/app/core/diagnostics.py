"""Self-observation: what the app is doing and what it costs.

The question this exists to answer is one that could not be answered from the
code alone: *is this much CPU and memory actually needed?* On a router-hosted
container the usual tools are missing - there is no ``docker stats``, no shell
inside the container, and until the log file landed in the data directory there
was no history either. Everything had to be inferred from the router's own
numbers, which mix the app's cost with the router's.

Two counters, both cheap:

* ``note_request`` - how many RouterOS REST calls went to which device. This is
  the load the app puts on the router, which is the half the operator sees in the
  router's own CPU meter.
* ``record`` - how long each background pass took, so a cadence change can be
  justified by the measured duration instead of by an argument.

Plus the process's own memory, read from ``/proc`` where available: the
container's ``memory-current`` as RouterOS reports it includes the page cache
that the app's database writes push through the mount, which is why that figure
looked alarming (500 MB) while the router's own free memory never moved.
"""
from __future__ import annotations

import logging
import platform
import resource
import threading
import time
from typing import Dict, Optional

logger = logging.getLogger("mikroman.diagnostics")

_started_at = time.monotonic()
_lock = threading.Lock()
#: pass name -> {count, total_s, max_s, last_s}
_ticks: Dict[str, Dict[str, float]] = {}
#: router host -> request count
_requests: Dict[str, int] = {}


def record(name: str, seconds: float) -> None:
    """Log one completed pass of a named background task."""
    with _lock:
        entry = _ticks.setdefault(name, {"count": 0.0, "total_s": 0.0, "max_s": 0.0, "last_s": 0.0})
        entry["count"] += 1
        entry["total_s"] += seconds
        entry["max_s"] = max(entry["max_s"], seconds)
        entry["last_s"] = seconds


def note_request(host: str) -> None:
    """Count one RouterOS REST call against ``host``."""
    with _lock:
        _requests[host] = _requests.get(host, 0) + 1


def request_counts() -> Dict[str, int]:
    with _lock:
        return dict(_requests)


def _current_rss_bytes() -> Optional[int]:
    """Resident set size from ``/proc/self/statm`` (Linux only).

    The second field is resident *pages*. Returns None where /proc has no
    self/statm, so the caller can fall back rather than report a wrong zero.
    """
    try:
        with open("/proc/self/statm", "r") as fh:
            fields = fh.read().split()
        return int(fields[1]) * resource.getpagesize()
    except (OSError, IndexError, ValueError):
        return None


def _is_linux() -> bool:
    return platform.system() == "Linux"


def _peak_rss_bytes() -> int:
    """High-water mark for this process.

    ``ru_maxrss`` is reported in kilobytes on Linux and bytes on Darwin, so the
    unit is chosen from the platform rather than assumed - the supported
    deployment (the container, RouterOS) is Linux, but this is also read on a
    development mac.
    """
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak * 1024 if _is_linux() else peak


def snapshot() -> dict:
    """The whole picture, in the shape the API returns it."""
    with _lock:
        ticks = {
            name: {
                "count": int(entry["count"]),
                "total_seconds": round(entry["total_s"], 3),
                "max_seconds": round(entry["max_s"], 3),
                "last_seconds": round(entry["last_s"], 3),
                "avg_seconds": round(entry["total_s"] / entry["count"], 3) if entry["count"] else 0.0,
            }
            for name, entry in _ticks.items()
        }
        requests = dict(_requests)
    rss = _current_rss_bytes()
    return {
        "uptime_seconds": round(time.monotonic() - _started_at, 1),
        "memory_bytes": rss,
        "memory_peak_bytes": _peak_rss_bytes(),
        "memory_note": (
            "Resident set of the app process only. RouterOS reports the "
            "container's cgroup usage, which also counts the page cache this "
            "process pushes through its data mount - that figure is larger and "
            "is reclaimable memory, not the app's."
        ),
        "routeros_requests_total": sum(requests.values()),
        "routeros_requests_by_host": requests,
        "background_passes": ticks,
    }
