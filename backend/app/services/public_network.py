"""Internet-facing identity of the router's uplink.

The address configured on the WAN interface is frequently a carrier-grade NAT
address (10/8, 100.64/10) that nothing outside the operator can reach, so it
answers neither "what is my address on the internet" nor "who is my provider".
Both facts are resolved together through an external echo service.

Design notes:

* One lookup returns both the address and the operator, so the dashboard does
  not pay two round trips for one row of the WAN tile.
* Results are cached for 15 minutes. Telemetry ticks roughly once a second, and
  neither fact changes on that timescale; without the cache this would be a
  request per frame against a public service that rate-limits.
* Every failure is non-fatal and keeps the previous answer. The router may
  legitimately have no internet, the service may rate-limit us, and a missing
  provider name must never degrade the rest of the telemetry frame.
* Failures back off on a much shorter timer than successes, so an outage that
  clears after a minute is picked up in a minute rather than a quarter hour.
"""

import logging
import re
import time
from typing import Optional

import httpx
from pydantic import BaseModel

logger = logging.getLogger("mikroman.public_network")

# A good answer is worth keeping for a long time; a bad one only briefly, so a
# transient outage does not blank the tile for the whole success window.
SUCCESS_TTL_SECONDS = 900
FAILURE_TTL_SECONDS = 60

# Deliberately short: this runs inside the telemetry loop, and a slow external
# service must never stall a frame.
LOOKUP_TIMEOUT_SECONDS = 4.0

# Guards against a compromised or misbehaving service pushing unbounded text
# into the UI.
_MAX_IP_LENGTH = 45
_MAX_NAME_LENGTH = 64

# ipinfo.io answers with the address and the owning organisation in one
# unauthenticated HTTPS call. ipify is the fallback: it only knows the address,
# but it is a different operator, so it covers the case where the first is
# blocked or rate-limiting us.
_IPINFO_URL = "https://ipinfo.io/json"
_IPIFY_URL = "https://api.ipify.org"

# Registry org fields are conventionally "AS<number> <organisation name>".
_ASN_PREFIX = re.compile(r"^\s*(AS\d+)\s+(.*)$", re.IGNORECASE)


class PublicNetwork(BaseModel):
    """What the internet sees of this router's uplink."""

    ip: Optional[str] = None
    isp: Optional[str] = None
    asn: Optional[str] = None

    def is_empty(self) -> bool:
        return not self.ip and not self.isp


def split_org_field(raw: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Split a registry org string into its AS number and organisation name.

    ``"AS49273 COSCOM Liability Limited Company"`` becomes
    ``("AS49273", "COSCOM Liability Limited Company")``. A value without the
    conventional prefix is returned as a name with no AS number, because some
    registries publish a bare organisation string.
    """
    if not raw:
        return None, None
    text = raw.strip()
    if not text:
        return None, None

    match = _ASN_PREFIX.match(text)
    if match:
        asn = match.group(1).upper()
        name = match.group(2).strip()
        return asn, (name[:_MAX_NAME_LENGTH] or None)

    # A bare AS number carries no name worth showing on its own.
    if re.fullmatch(r"AS\d+", text, re.IGNORECASE):
        return text.upper(), None

    return None, text[:_MAX_NAME_LENGTH]


def _clean_ip(value: Optional[str]) -> Optional[str]:
    """Accept only something that could plausibly be an address literal."""
    if not value:
        return None
    text = value.strip()
    if not text or len(text) > _MAX_IP_LENGTH:
        return None
    # IPv4 and IPv6 literals; anything else is an error page, not an address.
    if not re.fullmatch(r"[0-9a-fA-F.:]+", text):
        return None
    return text


class PublicNetworkResolver:
    """Caching resolver for the uplink's public address and operator.

    Kept as an object rather than module-level functions so tests can drive a
    fresh instance with a stubbed fetch instead of reaching the internet.
    """

    def __init__(self) -> None:
        self._value = PublicNetwork()
        self._checked_at = 0.0
        self._last_ok = False

    def _is_fresh(self, now: float) -> bool:
        ttl = SUCCESS_TTL_SECONDS if self._last_ok else FAILURE_TTL_SECONDS
        return self._checked_at > 0 and (now - self._checked_at) < ttl

    async def _fetch(self) -> PublicNetwork:
        """Query the external services. Returns an empty result on failure."""
        async with httpx.AsyncClient(timeout=LOOKUP_TIMEOUT_SECONDS) as http:
            try:
                resp = await http.get(_IPINFO_URL, headers={"Accept": "application/json"})
                if resp.status_code == 200:
                    body = resp.json()
                    ip = _clean_ip(body.get("ip"))
                    asn, isp = split_org_field(body.get("org"))
                    if ip or isp:
                        return PublicNetwork(ip=ip, isp=isp, asn=asn)
            except Exception as e:
                logger.debug(f"Public network lookup via ipinfo failed: {e}")

            # Address only; better than nothing when the richer service is down.
            try:
                resp = await http.get(_IPIFY_URL)
                if resp.status_code == 200:
                    ip = _clean_ip(resp.text)
                    if ip:
                        return PublicNetwork(ip=ip)
            except Exception as e:
                logger.debug(f"Public IP lookup via ipify failed: {e}")

        return PublicNetwork()

    async def resolve(self) -> PublicNetwork:
        """Current public identity, served from cache when still fresh.

        A failed refresh keeps the last known good answer rather than blanking
        the tile: a momentarily unreachable echo service says nothing about
        whether the router's own uplink is up.
        """
        now = time.time()
        if self._is_fresh(now):
            return self._value

        fetched = await self._fetch()
        self._checked_at = now
        if not fetched.is_empty():
            self._value = fetched
            self._last_ok = True
        else:
            self._last_ok = False
        return self._value

    def reset(self) -> None:
        """Drop cached state. Used by tests and after a router switch."""
        self._value = PublicNetwork()
        self._checked_at = 0.0
        self._last_ok = False


# Process-wide singleton: the answer describes the host's internet egress, which
# is the same regardless of which router the dashboard is currently showing.
public_network_resolver = PublicNetworkResolver()
