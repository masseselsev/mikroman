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
from backend.app.services.routeros.certificates import CertificatesMixin
from backend.app.services.routeros.clients import ClientsMixin
from backend.app.services.routeros.containers import ContainersMixin
from backend.app.services.routeros.firewall import FirewallMixin
from backend.app.services.routeros.queues import QueuesMixin
from backend.app.services.routeros.system import SystemMixin
from backend.app.services.routeros.transport import RouterOSTransport


class RouterOSClient(
    CertificatesMixin,
    SystemMixin,
    ClientsMixin,
    QueuesMixin,
    FirewallMixin,
    ContainersMixin,
    RouterOSTransport,
):
    """Async HTTP client for the MikroTik RouterOS REST API (7.1+).

    See ``backend/app/services/routeros_compat.py`` for the menus this depends
    on and the release each was introduced in.

    The mixins carry no state of their own and never override one another, so
    the resolution order above is presentational rather than load-bearing;
    :class:`RouterOSTransport` is last because it is what they all rest on.
    """
