"""External IP-lookup services reachable from the WAN tile.

Knowing the public address is only half an answer; the rest - geolocation, the
owning AS, abuse reports, what is exposed on it - lives on third-party lookup
sites. This module holds the catalogue of those sites, the user's choice among
them, and the validation that keeps a user-supplied URL template from becoming
an injection vector.

A service is just a name and a URL template containing the ``{ip}`` placeholder.
Templates rather than fixed providers, because these sites change their URL
shapes and a user should be able to point at their own tooling without waiting
for a release.

Security note: a template ends up as the ``href`` of a link the user clicks.
``javascript:`` and ``data:`` URLs in an href execute in the page's origin, so
the scheme is checked against an allow-list here, and again in the frontend
before the anchor is rendered. Validation is deliberately duplicated: this
setting is stored and later replayed into the DOM, so neither side may assume
the other sanitised it.
"""

import json
import logging
import re
from typing import List, Optional
from urllib.parse import quote, urlparse

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import AppSetting

logger = logging.getLogger("mikroman.ip_lookup")

SETTING_KEY = "ip_lookup_config"

# The one token substituted into a template.
IP_PLACEHOLDER = "{ip}"

# Only schemes a browser can safely navigate to. Everything else - javascript:,
# data:, vbscript:, file:, and any scheme we have not considered - is refused.
ALLOWED_SCHEMES = frozenset({"http", "https"})

MAX_TEMPLATE_LENGTH = 500
MAX_NAME_LENGTH = 40
MAX_CUSTOM_SERVICES = 10

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class IpLookupService(BaseModel):
    """One lookup destination."""

    id: str
    name: str
    url_template: str
    # Built-in entries ship with the app and cannot be edited or deleted; only
    # enabled or disabled.
    builtin: bool = False


# Curated defaults, ordered by how generally useful they are. 2ip.io is first
# because it answers the common question - where is this address and who owns
# it - in one page, in both English and Russian.
BUILTIN_SERVICES: List[IpLookupService] = [
    IpLookupService(id="2ip", name="2ip.io", url_template="https://2ip.io/{ip}/", builtin=True),
    IpLookupService(id="ipinfo", name="IPinfo", url_template="https://ipinfo.io/{ip}", builtin=True),
    IpLookupService(
        id="whatismyip",
        name="WhatIsMyIPAddress",
        url_template="https://whatismyipaddress.com/ip/{ip}",
        builtin=True,
    ),
    IpLookupService(
        id="abuseipdb",
        name="AbuseIPDB",
        url_template="https://www.abuseipdb.com/check/{ip}",
        builtin=True,
    ),
    IpLookupService(
        id="shodan",
        name="Shodan",
        url_template="https://www.shodan.io/host/{ip}",
        builtin=True,
    ),
    IpLookupService(
        id="bgp_he",
        name="BGP Toolkit",
        url_template="https://bgp.he.net/ip/{ip}",
        builtin=True,
    ),
]

DEFAULT_SERVICE_ID = BUILTIN_SERVICES[0].id


class IpLookupConfig(BaseModel):
    """Which services are offered, which one a plain click uses."""

    enabled_ids: List[str] = Field(default_factory=lambda: [DEFAULT_SERVICE_ID])
    default_id: str = DEFAULT_SERVICE_ID
    custom: List[IpLookupService] = Field(default_factory=list)


class TemplateError(ValueError):
    """A URL template that must not be stored or followed."""


def validate_template(raw: Optional[str]) -> str:
    """Check a URL template and return it normalised.

    Raises TemplateError with a message meant for the user. The checks, in the
    order a bad value is most likely to fail them:

    * non-empty and bounded, so a stored setting cannot become unbounded input
    * contains ``{ip}``, or the link would ignore the address entirely
    * parses, with an ``http``/``https`` scheme - this is the check that stops
      ``javascript:`` and ``data:`` hrefs
    * has a host
    * carries no embedded credentials, which would be leaked by the click
    """
    if not raw or not raw.strip():
        raise TemplateError("URL template is empty.")

    template = raw.strip()
    if len(template) > MAX_TEMPLATE_LENGTH:
        raise TemplateError(f"URL template is longer than {MAX_TEMPLATE_LENGTH} characters.")

    if IP_PLACEHOLDER not in template:
        raise TemplateError("URL template must contain the {ip} placeholder.")

    # Parse with a sample address substituted: a scheme check on the raw
    # template can be fooled by a placeholder sitting in front of the colon.
    probe = template.replace(IP_PLACEHOLDER, "192.0.2.1")
    try:
        parsed = urlparse(probe)
    except ValueError as e:
        raise TemplateError(f"URL template is not a valid URL: {e}") from e

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise TemplateError(
            f"URL template must start with http:// or https:// "
            f"(got {parsed.scheme or 'no'} scheme)."
        )

    if not parsed.netloc:
        raise TemplateError("URL template has no host.")

    if "@" in parsed.netloc:
        raise TemplateError("URL template must not contain embedded credentials.")

    return template


def build_lookup_url(template: str, ip: str) -> str:
    """Substitute an address into a validated template.

    The address is percent-encoded before substitution. Ordinary IPv4 and IPv6
    literals need no encoding, but the address arrives from an external echo
    service, and a value that reached the DOM unencoded would be the whole
    attack. Validation is re-run afterwards so a hostile address cannot turn a
    safe template into an unsafe URL.
    """
    safe_ip = quote(str(ip).strip(), safe="")
    url = template.replace(IP_PLACEHOLDER, safe_ip)

    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES or not parsed.netloc:
        raise TemplateError("Substituted URL is not a valid http(s) URL.")
    return url


def _sanitise_custom(entries: List[dict]) -> List[IpLookupService]:
    """Keep the custom entries that survive validation, drop the rest.

    A stored setting can predate a tightening of these rules, so a bad entry is
    logged and skipped rather than made fatal: one malformed row must not stop
    the settings page from loading.
    """
    services: List[IpLookupService] = []
    for entry in entries[:MAX_CUSTOM_SERVICES]:
        if not isinstance(entry, dict):
            continue
        service_id = str(entry.get("id") or "").strip().lower()
        name = str(entry.get("name") or "").strip()[:MAX_NAME_LENGTH]
        try:
            template = validate_template(entry.get("url_template"))
        except TemplateError as e:
            logger.warning(f"Dropping stored IP lookup service {service_id!r}: {e}")
            continue
        if not _ID_RE.match(service_id) or not name:
            logger.warning(f"Dropping stored IP lookup service with bad id/name: {service_id!r}")
            continue
        services.append(IpLookupService(id=service_id, name=name, url_template=template, builtin=False))
    return services


def all_services(config: IpLookupConfig) -> List[IpLookupService]:
    """Built-in catalogue followed by the user's own entries."""
    return [*BUILTIN_SERVICES, *config.custom]


def resolve_config(config: IpLookupConfig) -> IpLookupConfig:
    """Drop references to services that no longer exist and keep a default.

    Deleting a custom service that happened to be the default must not leave the
    WAN tile with nothing to click, so the default falls back to the first
    enabled service, then to the built-in default.
    """
    known = {s.id for s in all_services(config)}
    enabled = [sid for sid in config.enabled_ids if sid in known]
    if not enabled:
        enabled = [DEFAULT_SERVICE_ID]

    default_id = config.default_id if config.default_id in enabled else enabled[0]
    return IpLookupConfig(enabled_ids=enabled, default_id=default_id, custom=config.custom)


async def get_config(session: AsyncSession) -> IpLookupConfig:
    """Stored configuration, or the defaults when nothing has been saved."""
    setting = await session.get(AppSetting, SETTING_KEY)
    if not setting or not setting.value:
        return IpLookupConfig()

    try:
        raw = json.loads(setting.value)
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"Stored IP lookup config is not valid JSON, using defaults: {e}")
        return IpLookupConfig()

    if not isinstance(raw, dict):
        return IpLookupConfig()

    enabled = [str(v) for v in raw.get("enabled_ids", []) if isinstance(v, (str, int))]
    custom = _sanitise_custom(raw.get("custom", []) or [])
    config = IpLookupConfig(
        enabled_ids=enabled or [DEFAULT_SERVICE_ID],
        default_id=str(raw.get("default_id") or DEFAULT_SERVICE_ID),
        custom=custom,
    )
    return resolve_config(config)


async def save_config(session: AsyncSession, config: IpLookupConfig) -> IpLookupConfig:
    """Validate and persist. Returns the configuration as actually stored."""
    validated_custom: List[IpLookupService] = []
    seen_ids = {s.id for s in BUILTIN_SERVICES}

    for service in config.custom[:MAX_CUSTOM_SERVICES]:
        service_id = service.id.strip().lower()
        if not _ID_RE.match(service_id):
            raise TemplateError(f"Invalid service id {service.id!r}.")
        if service_id in seen_ids:
            raise TemplateError(f"Duplicate service id {service_id!r}.")
        name = service.name.strip()[:MAX_NAME_LENGTH]
        if not name:
            raise TemplateError("Custom services need a name.")
        seen_ids.add(service_id)
        validated_custom.append(IpLookupService(
            id=service_id,
            name=name,
            url_template=validate_template(service.url_template),
            builtin=False,
        ))

    resolved = resolve_config(IpLookupConfig(
        enabled_ids=config.enabled_ids,
        default_id=config.default_id,
        custom=validated_custom,
    ))

    payload = json.dumps({
        "enabled_ids": resolved.enabled_ids,
        "default_id": resolved.default_id,
        "custom": [s.model_dump(exclude={"builtin"}) for s in resolved.custom],
    })

    setting = await session.get(AppSetting, SETTING_KEY)
    if setting:
        setting.value = payload
    else:
        session.add(AppSetting(key=SETTING_KEY, value=payload))
    await session.commit()
    return resolved
