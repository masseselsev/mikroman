"""The RouterOS REST client, composed from its domain mixins.

``routeros.py`` was a single 1000-line class holding every RouterOS menu the app
touches - certificates, system, clients, queues, firewall, containers - behind
one connection. The connection is genuinely shared; the menus are not related to
each other at all, and every feature in the app had to open that file to add
three lines to it.

Splitting the menus into mixins and composing them here keeps the one thing that
must be shared (:class:`RouterOSTransport`: config, pool, circuit breaker) in one
place, gives each menu a file small enough to read in full, and leaves the public
surface untouched - ``RouterOSClient`` still exposes every method it always did,
and every existing import keeps working.

Adding a menu: write a mixin in its own module, add it to the bases below.
"""
import logging
import socket

from backend.app.services.routeros.backup import BackupMixin
from backend.app.services.routeros.certificates import CertificatesMixin
from backend.app.services.routeros.clients import ClientsMixin
from backend.app.services.routeros.connections import ConnectionsMixin
from backend.app.services.routeros.containers import ContainersMixin
from backend.app.services.routeros.firewall import FirewallMixin
from backend.app.services.routeros.firmware import FirmwareMixin
from backend.app.services.routeros.queues import QueuesMixin
from backend.app.services.routeros.system import SystemMixin
from backend.app.services.routeros.transport import RouterOSTransport

logger = logging.getLogger("mikroman.routeros")


class RouterOSClient(
    CertificatesMixin,
    SystemMixin,
    ClientsMixin,
    QueuesMixin,
    FirewallMixin,
    ContainersMixin,
    ConnectionsMixin,
    BackupMixin,
    FirmwareMixin,
    RouterOSTransport,
):
    """Async HTTP client for the MikroTik RouterOS REST API (7.1+).

    See ``backend/app/services/routeros_compat.py`` for the menus this depends
    on and the release each was introduced in.

    The mixins carry no state of their own and never override one another, so
    the resolution order above is presentational rather than load-bearing;
    :class:`RouterOSTransport` is last because it is what they all rest on.
    """

    def get_immune_ips(self) -> set[str]:
        """IPs this client must never block, throttle or disconnect.

        Always contains the address MikroMan reaches the router on, plus the
        local address the OS picks for that route - i.e. the container's own IP.
        Cutting either one severs the dashboard from the router it manages.

        ``_immune_ips`` holds the operator's extra exemptions, seeded by
        :class:`~backend.app.services.router_manager.RouterManager` from the
        ``immune_ips`` app setting. They are added to the computed pair, never
        substituted for it - a guard must only ever widen with configuration,
        never narrow. The computed half is cached because it is consulted on
        every queue and address-list write.
        """
        immune = set(getattr(self, "_immune_ips", None) or ())

        cached = getattr(self, "_immune_ips_cache", None)
        if cached is not None:
            return immune | cached

        host = getattr(self, "host", None)
        computed = {host} if host else set()
        if host:
            try:
                # A connect() on a UDP socket sends nothing - it only asks the
                # kernel which local address would be used to reach the router.
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    sock.connect((host, 80))
                    computed.add(sock.getsockname()[0])
                finally:
                    sock.close()
            except Exception as e:
                logger.debug(f"Could not determine local address towards {host}: {e}")

        self._immune_ips_cache = computed
        return immune | computed
