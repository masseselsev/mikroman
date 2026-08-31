"""ISP data quota for the billing cycle, with alerting at chosen thresholds.

A quota that only reports being exceeded is useless - the point is to warn
before it is reached - so several thresholds can be armed at once, typically a
early warning, a late one and the limit itself.

Two rules keep the alerting sane:

* each threshold fires **once per billing cycle**, because the check runs on
  every poll and would otherwise repeat the same warning indefinitely;
* a poll gap must not swallow a threshold. If usage jumps from 40% to 95%
  between two checks, every threshold in between is reported, not just the
  highest.
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import AppSetting

logger = logging.getLogger("mikroman.quota")

LIMIT_SETTING_KEY = "quota_limit_bytes"
THRESHOLDS_SETTING_KEY = "quota_alert_thresholds"
NOTIFY_SETTING_KEY = "quota_notify_telegram"
FIRED_SETTING_KEY = "quota_fired_thresholds"
# Link to the ISP account / usage page, or the modem's own stats page. Shown as
# a button on the quota strip so the operator can jump to the authoritative
# figure in one click.
PORTAL_URL_SETTING_KEY = "isp_portal_url"
PORTAL_LABEL_SETTING_KEY = "isp_portal_label"


@dataclass
class QuotaConfig:
    """Quota for one billing cycle. A limit of 0 disables the feature."""

    limit_bytes: int = 0
    thresholds: List[int] = field(default_factory=list)
    notify_telegram: bool = True
    portal_url: Optional[str] = None
    portal_label: Optional[str] = None


def parse_thresholds(raw: Optional[str]) -> List[int]:
    """Parse a comma-separated percentage list into sorted, unique values.

    Unusable entries are dropped rather than discarding the whole list, and
    values outside 1-100 are removed because they can never fire meaningfully.
    """
    if not raw:
        return []
    values = set()
    for part in str(raw).split(","):
        token = part.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError:
            logger.debug(f"Ignoring unparseable quota threshold {token!r}")
            continue
        if 1 <= value <= 100:
            values.add(value)
    return sorted(values)


def crossed_thresholds(
    used_bytes: int,
    limit_bytes: Optional[int],
    thresholds: List[int],
    already_fired: List[int],
) -> List[int]:
    """Thresholds newly reached by ``used_bytes`` and not yet alerted on.

    Returns every threshold passed since the last check, so a large jump between
    polls still reports each one rather than only the highest.
    """
    if not limit_bytes or limit_bytes <= 0 or not thresholds:
        return []
    pct = (used_bytes / limit_bytes) * 100
    fired = set(already_fired)
    return [t for t in sorted(thresholds) if pct >= t and t not in fired]


async def _get(session: AsyncSession, key: str) -> Optional[str]:
    setting = await session.get(AppSetting, key)
    return setting.value if setting else None


async def _set(session: AsyncSession, key: str, value: str, description: str = "") -> None:
    setting = await session.get(AppSetting, key)
    if setting:
        setting.value = value
    else:
        session.add(AppSetting(key=key, value=value, description=description))


async def get_quota_config(session: AsyncSession) -> QuotaConfig:
    """Current quota configuration; inert defaults when nothing is set."""
    raw_limit = await _get(session, LIMIT_SETTING_KEY)
    try:
        limit = int(raw_limit) if raw_limit else 0
    except ValueError:
        logger.warning("Stored quota limit is not a number; treating quota as disabled")
        limit = 0

    notify_raw = await _get(session, NOTIFY_SETTING_KEY)
    portal_url = (await _get(session, PORTAL_URL_SETTING_KEY) or "").strip() or None
    portal_label = (await _get(session, PORTAL_LABEL_SETTING_KEY) or "").strip() or None
    return QuotaConfig(
        limit_bytes=max(0, limit),
        thresholds=parse_thresholds(await _get(session, THRESHOLDS_SETTING_KEY)),
        notify_telegram=(notify_raw is None or notify_raw.lower() != "false"),
        portal_url=portal_url,
        portal_label=portal_label,
    )


def clean_portal_url(raw: Optional[str]) -> Optional[str]:
    """Validate the ISP/modem link before it is stored and later rendered.

    It ends up as the ``href`` of a button the operator clicks, so only
    ``http``/``https`` is allowed and credentials embedded in the URL are
    rejected - the same rule the IP-lookup templates are held to. Returns the
    trimmed URL, or None for an empty value; raises ``ValueError`` on a bad one.
    """
    if not raw or not raw.strip():
        return None
    url = raw.strip()
    lowered = url.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        raise ValueError("Portal URL must start with http:// or https://")
    host_part = url.split("://", 1)[1].split("/", 1)[0]
    if "@" in host_part:
        raise ValueError("Portal URL must not contain embedded credentials")
    return url


async def save_quota_config(session: AsyncSession, config: QuotaConfig) -> QuotaConfig:
    """Persist the quota configuration."""
    await _set(session, LIMIT_SETTING_KEY, str(max(0, config.limit_bytes)),
               "ISP data limit for one billing cycle, in bytes; 0 disables")
    await _set(session, THRESHOLDS_SETTING_KEY, ",".join(str(t) for t in parse_thresholds(
        ",".join(str(t) for t in config.thresholds))),
        "Percentages of the quota at which to alert")
    await _set(session, NOTIFY_SETTING_KEY, "true" if config.notify_telegram else "false",
               "Send quota threshold alerts to Telegram")
    await _set(session, PORTAL_URL_SETTING_KEY, clean_portal_url(config.portal_url) or "",
               "Link to the ISP usage/billing page or the modem's stats page")
    await _set(session, PORTAL_LABEL_SETTING_KEY, (config.portal_label or "").strip()[:40],
               "Short label for the ISP portal link button")
    await session.commit()
    return await get_quota_config(session)


async def _load_fired(session: AsyncSession) -> dict:
    raw = await _get(session, FIRED_SETTING_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


async def unfired_for_cycle(session: AsyncSession, cycle_start: date) -> List[int]:
    """Thresholds already alerted on during this billing cycle.

    Keyed by cycle start, so a new cycle re-arms every threshold automatically
    without needing a separate reset step.
    """
    data = await _load_fired(session)
    return sorted(data.get(cycle_start.isoformat(), []))


async def mark_fired(session: AsyncSession, cycle_start: date, threshold: int) -> None:
    """Record that a threshold has been alerted on for this cycle."""
    data = await _load_fired(session)
    key = cycle_start.isoformat()
    fired = set(data.get(key, []))
    fired.add(threshold)
    # Only the current cycle is retained; older entries are of no further use.
    await _set(session, FIRED_SETTING_KEY, json.dumps({key: sorted(fired)}),
               "Quota thresholds already alerted on, per billing cycle")
    await session.commit()
