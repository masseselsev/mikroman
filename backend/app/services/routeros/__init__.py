"""RouterOS REST client.

Was a single module; became a package when the class outgrew a thousand lines.
Everything the rest of the app ever imported from ``services.routeros`` is
re-exported here, so ``from backend.app.services.routeros import RouterOSClient``
means exactly what it did before the split.
"""
from backend.app.services.routeros.client import RouterOSClient
from backend.app.services.routeros.parsing import (
    build_wifi_links,
    parse_gmt_offset_minutes,
    parse_signal_list,
    parse_uptime_seconds,
)
from backend.app.services.routeros.transport import (
    UNREACHABLE_COOLDOWN_SECONDS,
    RouterOSTransport,
    RouterUnreachableError,
)

__all__ = [
    "RouterOSClient",
    "RouterOSTransport",
    "RouterUnreachableError",
    "UNREACHABLE_COOLDOWN_SECONDS",
    "build_wifi_links",
    "parse_gmt_offset_minutes",
    "parse_signal_list",
    "parse_uptime_seconds",
]
