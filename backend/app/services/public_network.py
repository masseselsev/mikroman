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

import ipaddress
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

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

# Registry data names the legal entity - "COSCOM Liability Limited Company" -
# while the operator everyone actually knows is "Ucell". The WAN tile should
# carry the recognisable brand, so two sources are tried for it, in order:
#
#  1. ipwho.is (HTTPS). Its `connection` block carries the operator's own
#     domain ("ucell.uz") and a consumer-facing org string ("Ucell Net 1").
#     The domain's registrable label - "ucell" -> "Ucell" - is the brand the
#     public lookup sites (2ip.io and friends) display, and the legal entity
#     name never is.
#  2. ip-api.com (plaintext HTTP). Fallback only. Its free tier is HTTP, which
#     is why it is used for the NAME ONLY.
#
# Everything acted upon - the address and the AS number - comes from the HTTPS
# sources above. That split bounds what an interfering network can do to a
# plaintext response: it can never change the address the WAN tile links to,
# and the name is rendered as a text node, escaped and length capped, so it
# cannot become markup.
_IPWHOIS_URL = "https://ipwho.is/"
# Always the address-specific form: the operator is looked up for the router's
# own public address, never for the container's egress.
_IP_API_FOR = "http://ip-api.com/json/{ip}?fields=status,isp,as"

# Two-label public suffixes common enough to matter; anything not listed is
# treated as a single-label TLD. Used to pull the brand out of an operator's
# own domain ("bt.co.uk" -> "bt", "ucell.uz" -> "ucell").
_TWO_LABEL_TLDS = frozenset({
    "co.uk", "org.uk", "gov.uk", "ac.uk", "co.jp", "or.jp", "ne.jp",
    "com.au", "net.au", "org.au", "com.br", "com.tr", "com.ua", "net.ua",
    "co.nz", "co.za", "com.cn", "com.hk", "com.sg", "com.mx", "co.in",
    "co.kr", "com.pl", "co.il", "com.ar", "com.co",
})

# Legal-entity noise that marks a string as a registration, not a brand.
_LEGAL_ENTITY = re.compile(
    r"\b(?:LLC|L\.L\.C\.?|LLP|Ltd\.?|Limited|Liability|Company|Co\.?|Inc\.?|"
    r"Corp\.?|GmbH|S\.?A\.?|S\.?R\.?L\.?|B\.?V\.?|PLC|JSC|OOO|PJSC|OJSC)\b",
    re.IGNORECASE,
)

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


def brand_from_domain(domain: Optional[str]) -> Optional[str]:
    """The recognisable brand carried by an operator's own domain.

    ``"ucell.uz"`` -> ``"Ucell"``, ``"bt.co.uk"`` -> ``"Bt"``,
    ``"t-mobile.com"`` -> ``"T-Mobile"``. The registrable label (the one in
    front of the public suffix) is, for an ISP's own domain, almost always its
    trading name - which the registry's legal entity string never is.

    Returns ``None`` for anything that is not a plain hostname so a garbled
    field can never reach the UI.
    """
    if not domain:
        return None
    host = domain.strip().lower().strip(".")
    # A hostname, nothing else: letters, digits, dots and hyphens.
    if not host or not re.fullmatch(r"[a-z0-9.-]+", host):
        return None
    parts = host.split(".")
    if len(parts) < 2:
        return None
    suffix_len = 2 if ".".join(parts[-2:]) in _TWO_LABEL_TLDS else 1
    label_index = len(parts) - suffix_len - 1
    if label_index < 0:
        return None
    label = parts[label_index]
    if len(label) < 2 or not re.fullmatch(r"[a-z0-9-]+", label):
        return None
    # "t-mobile" -> "T-Mobile"; a lone word is simply capitalised.
    return "-".join(w.capitalize() for w in label.split("-"))[:_MAX_NAME_LENGTH]


def clean_trading_name(raw: Optional[str]) -> Optional[str]:
    """A consumer-facing org string with trailing clutter removed, or ``None``.

    ``"Ucell Net 1"`` -> ``"Ucell"``. A string that still reads as a legal
    registration (``"COSCOM Liability Limited Company"``) is rejected outright -
    the caller has a domain-derived brand or the registry name to fall back on.
    """
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    # Drop a trailing "Net 1" / "AS" / numbering suffix that network registries
    # bolt onto the consumer name.
    text = re.sub(r"\s+(?:Net(?:work)?\s*\d*|AS\d+|\d+)$", "", text, flags=re.IGNORECASE).strip()
    if not text or _LEGAL_ENTITY.search(text):
        return None
    return text[:_MAX_NAME_LENGTH]


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


def public_ip_or_none(value: Optional[str]) -> Optional[str]:
    """The address if it is a real routable public one, else ``None``.

    ``/ip/cloud`` on a router that has never reached the DDNS service reports
    ``0.0.0.0``; a router behind carrier-grade NAT reports a ``100.64/10`` or
    RFC1918 address. None of those identify the operator, so they are rejected
    and the caller falls back to the container-egress lookup.
    """
    cleaned = _clean_ip(value)
    if not cleaned:
        return None
    try:
        return cleaned if ipaddress.ip_address(cleaned).is_global else None
    except ValueError:
        return None


@dataclass
class _CacheEntry:
    value: PublicNetwork = field(default_factory=PublicNetwork)
    checked_at: float = 0.0
    last_ok: bool = False


class PublicNetworkResolver:
    """Caching resolver for each router's public address and operator.

    Cached **per router**: on a multi-router install the routers sit at
    different sites with different uplinks, and the container's own egress
    (what an unqualified "what is my IP" call returns) is only one of them.

    When the caller passes ``hint_ip`` - the router's own ``/ip/cloud``
    public-address, which RouterOS keeps current over DDNS - that address is
    trusted directly and the operator is looked up for *it*. Only when the
    router cannot say (``0.0.0.0``, CGNAT, DDNS never reached) does it fall
    back to the container-egress echo, which is the right answer for a local
    single-router setup where the container is behind that very router.

    Kept as an object rather than module functions so tests can drive a fresh
    instance with stubbed HTTP.
    """

    def __init__(self) -> None:
        self._by_router: Dict[Optional[int], _CacheEntry] = {}

    def _is_fresh(self, entry: _CacheEntry, now: float) -> bool:
        ttl = SUCCESS_TTL_SECONDS if entry.last_ok else FAILURE_TTL_SECONDS
        return entry.checked_at > 0 and (now - entry.checked_at) < ttl

    async def _echo_ip(self, http: httpx.AsyncClient) -> Optional[str]:
        """The container's own public address, from a public echo service."""
        try:
            resp = await http.get(_IPINFO_URL, headers={"Accept": "application/json"})
            if resp.status_code == 200:
                ip = _clean_ip(resp.json().get("ip"))
                if ip:
                    return ip
        except Exception as e:
            logger.debug(f"Public IP echo via ipinfo failed: {e}")
        try:
            resp = await http.get(_IPIFY_URL)
            if resp.status_code == 200:
                return _clean_ip(resp.text)
        except Exception as e:
            logger.debug(f"Public IP echo via ipify failed: {e}")
        return None

    async def _identity_for_ip(
        self, http: httpx.AsyncClient, ip: str
    ) -> tuple[Optional[str], Optional[str]]:
        """``(operator brand, AS number)`` for one specific address.

        ipwho.is (HTTPS) is authoritative for both; the operator's own domain
        gives the recognisable brand. ip-api.com's address form is the
        plaintext fallback for the name only.
        """
        try:
            resp = await http.get(f"{_IPWHOIS_URL}{ip}")
            if resp.status_code == 200:
                body = resp.json()
                if body.get("success", True):
                    conn = body.get("connection") or {}
                    raw_asn = conn.get("asn")
                    asn = None
                    if raw_asn not in (None, "", 0):
                        asn = f"AS{raw_asn}" if str(raw_asn).isdigit() else str(raw_asn)[:_MAX_NAME_LENGTH]
                    brand = (
                        brand_from_domain(conn.get("domain"))
                        or clean_trading_name(conn.get("org"))
                        or clean_trading_name(conn.get("isp"))
                    )
                    if brand or asn:
                        return brand, asn
        except Exception as e:
            logger.debug(f"Identity lookup via ipwho.is failed for {ip}: {e}")

        try:
            resp = await http.get(_IP_API_FOR.format(ip=ip))
            if resp.status_code == 200:
                body = resp.json()
                if body.get("status") == "success":
                    asn, _ = split_org_field(str(body.get("as") or ""))
                    name = str(body.get("isp") or "").strip()[:_MAX_NAME_LENGTH] or None
                    return name, asn
        except Exception as e:
            logger.debug(f"Identity lookup via ip-api failed for {ip}: {e}")

        return None, None

    async def resolve(
        self, router_id: Optional[int] = None, hint_ip: Optional[str] = None
    ) -> PublicNetwork:
        """This router's public identity, served from cache when still fresh.

        A failed refresh keeps the last known good answer rather than blanking
        the tile - a momentarily unreachable lookup service says nothing about
        whether the uplink is up. The success cache lasts
        ``SUCCESS_TTL_SECONDS`` (15 min), so an upstream or routing change on
        the provider's side is picked up on its own within that window.
        """
        now = time.time()
        entry = self._by_router.get(router_id)
        if entry and self._is_fresh(entry, now):
            return entry.value

        prev = entry.value if entry else PublicNetwork()
        router_public = public_ip_or_none(hint_ip)

        async with httpx.AsyncClient(timeout=LOOKUP_TIMEOUT_SECONDS) as http:
            ip = router_public or await self._echo_ip(http)
            fetched = PublicNetwork(ip=ip)
            if ip:
                brand, asn = await self._identity_for_ip(http, ip)
                fetched = fetched.model_copy(update={"isp": brand, "asn": asn})

        if not fetched.is_empty():
            self._by_router[router_id] = _CacheEntry(fetched, now, True)
            return fetched
        # Keep the previous good answer but mark the entry stale so the next
        # tick retries on the shorter failure timer.
        self._by_router[router_id] = _CacheEntry(prev, now, False)
        return prev

    def reset(self, router_id: Optional[int] = None) -> None:
        """Drop cached state - a whole router, or everything. Used by tests."""
        if router_id is None:
            self._by_router.clear()
        else:
            self._by_router.pop(router_id, None)


# Process-wide singleton, now keyed by router id internally.
public_network_resolver = PublicNetworkResolver()
