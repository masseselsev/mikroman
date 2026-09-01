"""Per-device traffic accounting via RouterOS firewall mangle counters.

Why not Simple Queues
---------------------
MikroMan originally derived per-user and per-device volume from the ``bytes``
counter of each managed Simple Queue. On RouterOS 7.25 (hAP be^3) those counters
were measured to stay frozen at zero while traffic flowed: a freshly created
queue placed first in the queue order, targeting the busiest client, counted
0 bytes through a 4.9 MB burst. FastTrack, hardware offload, ``use-ip-firewall``
and stale queue objects were each ruled out individually.

The firewall forward chain, by contrast, accounted 243.8 MB against 246 MB of
actual WAN download in the same window (99.1%). So volume is measured with a
pair of ``action=passthrough`` mangle rules per device - one matching the device
as source (upload), one as destination (download). ``passthrough`` increments a
counter and passes the packet on unchanged; it cannot drop, alter or reroute
traffic.

Simple Queues are still used, but only for what they still do correctly:
bandwidth *shaping*.

Counter semantics
-----------------
Mangle counters are monotonic until the rule is recreated or the router reboots.
Volume is therefore accumulated as deltas against a persisted baseline, exactly
as the interface counters are handled.
"""
import json
import logging
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    AppSetting,
    Device,
    DeviceTrafficRollup,
    RouterSelfTrafficRollup,
    TrafficRollup,
)
from backend.app.services.rollups import split_bytes_by_day
from backend.app.services.router_time import router_local_date, router_local_now

logger = logging.getLogger("mikroman.traffic_accounting")

# Comment tag identifying a MikroMan accounting rule. Device id keeps the tag
# stable across renames and IP changes.
ACCT_COMMENT = "mikroman:acct:dev_{device_id}:{direction}"
ACCT_PREFIX = "mikroman:acct:"
BASELINE_SETTING_KEY = "acct_counter_baselines"
# ``{"<dead device id>": <successor device id>}``. A device row can disappear
# (a rotated MAC consolidated into another record) while its mangle rules are
# still on the router counting bytes, because rules are only pruned on the next
# sync tick. Without this map those final bytes are read, matched to a device id
# that no longer resolves, and silently dropped.
SUCCESSOR_SETTING_KEY = "acct_device_successors"
# Date on which per-device accounting first became active. Gateway counters
# predate it, so coverage must not be judged over earlier periods.
STARTED_SETTING_KEY = "accounting_started_at"

# Reserved key inside the baselines blob holding the router uptime (seconds) at
# the last successful collection. If uptime has gone backwards since, the router
# rebooted and every byte counter reset to zero at that moment.
UPTIME_BASELINE_KEY = "__router_uptime_s__"
# Reserved key inside the baselines blob holding the router-local wall-clock
# time (ISO string) of the last successful collection. When the current tick
# lands on a later date, the counter delta accumulated since then is spread
# across the days it spans instead of being credited whole to the current date
# - otherwise a poll that resumes after an outage running past midnight files
# a full evening of traffic under the wrong day.
LAST_COLLECT_KEY = "__last_collect_local__"
# Uptime may appear to dip by a few seconds between polls from rounding and tick
# jitter; only a drop larger than this is treated as a reboot.
REBOOT_SLACK_SECONDS = 90

# direction -> RouterOS match field. A device is the *source* of its uploads and
# the *destination* of its downloads.
DIRECTION_FIELDS = {"up": "src-address", "down": "dst-address"}

# --- the router's own traffic ------------------------------------------------
#
# Per-device rules match the `forward` chain, which by construction only sees
# traffic passing *through* the router. Everything the router does on its own
# behalf - DNS, NTP, package and cloud checks, DDNS, whatever its containers
# pull, and MikroMan's own REST polling - travels `input` and `output` instead
# and was therefore invisible to per-device accounting, surfacing only as part
# of the unexplained gap between the WAN interface total and the sum of the
# devices. One passthrough pair per monitored WAN interface names that volume.
SELF_COMMENT = "mikroman:acct:self:{direction}:{interface}"
# direction -> (chain, interface match field). The router is the *destination*
# of what it downloads (arriving on the WAN port) and the *source* of what it
# sends (leaving by it).
SELF_DIRECTION_RULES = {
    "down": ("input", "in-interface"),
    "up": ("output", "out-interface"),
}


def parse_self_comment(comment: Optional[str]) -> Optional[Tuple[str, str]]:
    """Parse a router self-traffic tag into ``(direction, interface)``.

    Separate from :func:`parse_acct_comment` rather than folded into it: these
    rules have no device, live in different chains, and match on an interface
    instead of an address, so a single parser returning a union type would push
    that difference onto every caller.
    """
    if not comment or not comment.startswith(ACCT_PREFIX):
        return None
    parts = comment.split(":")
    # mikroman : acct : self : <direction> : <interface>
    if len(parts) != 5 or parts[2] != "self":
        return None
    direction, interface = parts[3], parts[4]
    if direction not in SELF_DIRECTION_RULES or not interface:
        return None
    return direction, interface


def parse_acct_comment(comment: Optional[str]) -> Optional[Tuple[int, str]]:
    """Parse an accounting rule comment into ``(device_id, direction)``.

    Returns None for any comment that is not a MikroMan accounting tag, so
    unrelated user-defined mangle rules are never touched.
    """
    if not comment or not comment.startswith(ACCT_PREFIX):
        return None
    parts = comment.split(":")
    # mikroman : acct : dev_<id> : <direction>   (self-traffic tags have 5 parts)
    if len(parts) != 4 or not parts[2].startswith("dev_"):
        return None
    try:
        device_id = int(parts[2][4:])
    except ValueError:
        return None
    direction = parts[3]
    if direction not in DIRECTION_FIELDS:
        return None
    return device_id, direction


class LiveRateTracker:
    """Derives live per-device throughput by differentiating mangle byte counters.

    The Simple Queue ``rate`` field is unusable on RouterOS 7.x: it was observed
    frozen at a constant non-zero value for hours while the WAN was almost idle,
    and stuck at zero for clients that were actively downloading. Differentiating
    the accounting counters gives a rate that is correct by construction, because
    it is computed from the same bytes the historical totals are built from.

    State is in-memory and per-process: rates are a live view, not history.
    """

    def __init__(self) -> None:
        # device_id -> {"up": bytes, "down": bytes, "t": timestamp}
        self._previous: Dict[int, Dict[str, float]] = {}

    def sample(self, rules: List[Dict[str, Any]], now: Optional[float] = None) -> Dict[int, Dict[str, float]]:
        """Read counters and return ``{device_id: {"rx_bps", "tx_bps"}}``.

        The first sample for a device establishes a reference and yields nothing.
        """
        if now is None:
            now = time.monotonic()

        current: Dict[int, Dict[str, int]] = {}
        for rule in rules:
            parsed = parse_acct_comment(rule.get("comment"))
            if not parsed:
                continue
            device_id, direction = parsed
            try:
                current.setdefault(device_id, {})[direction] = int(rule.get("bytes", 0) or 0)
            except (TypeError, ValueError):
                continue

        rates: Dict[int, Dict[str, float]] = {}
        for device_id, values in current.items():
            up_now = values.get("up", 0)
            down_now = values.get("down", 0)
            previous = self._previous.get(device_id)
            self._previous[device_id] = {"up": up_now, "down": down_now, "t": now}

            if previous is None:
                continue
            elapsed = now - previous["t"]
            if elapsed <= 0:
                continue

            # A counter that went backwards means the rule was recreated; credit
            # only the post-reset bytes rather than a nonsensical negative rate.
            up_delta = up_now - previous["up"] if up_now >= previous["up"] else up_now
            down_delta = down_now - previous["down"] if down_now >= previous["down"] else down_now

            rates[device_id] = {
                "rx_bps": max(0.0, down_delta * 8 / elapsed),
                "tx_bps": max(0.0, up_delta * 8 / elapsed),
            }
        return rates


async def aggregate_user_rates(
    session: AsyncSession,
    per_device: Dict[int, Dict[str, float]]
) -> Dict[int, Dict[str, float]]:
    """Sum measured per-device rates onto their owning user profiles."""
    totals: Dict[int, Dict[str, float]] = {}
    if not per_device:
        return totals

    result = await session.execute(
        select(Device.id, Device.user_id).where(Device.id.in_(per_device.keys()))
    )
    for device_id, user_id in result.all():
        if not user_id:
            continue
        rate = per_device.get(device_id) or {}
        bucket = totals.setdefault(user_id, {"rx_bps": 0.0, "tx_bps": 0.0})
        bucket["rx_bps"] += rate.get("rx_bps", 0.0)
        bucket["tx_bps"] += rate.get("tx_bps", 0.0)
    return totals


# Process-wide tracker shared by the WebSocket telemetry loop and the REST
# endpoints, so successive polls differentiate against each other.
live_rate_tracker = LiveRateTracker()


class TrafficAccountingService:
    """Maintains and reads per-device mangle byte counters on RouterOS."""

    def __init__(self, router_client: Any, router_id: Optional[int] = None):
        self.router_client = router_client
        self.router_id = router_id

    # --- rule lifecycle ---------------------------------------------------

    async def _accountable_devices(self, session: AsyncSession) -> List[Device]:
        """Active devices with an IP address that belong to this router.

        Devices with a NULL ``router_id`` are included: they are legitimate
        clients discovered before multi-router support attributed them, and
        excluding them silently dropped them from every analytics view.
        """
        stmt = select(Device).where(Device.is_active, Device.ip_address.is_not(None))
        if self.router_id is not None:
            stmt = stmt.where(
                (Device.router_id == self.router_id) | (Device.router_id.is_(None))
            )
        result = await session.execute(stmt)
        return [d for d in result.scalars().all() if (d.ip_address or "").strip()]

    async def _monitored_interfaces(self, session: AsyncSession) -> List[str]:
        """WAN interfaces to measure the router's own traffic on.

        The same setting the gateway rollups are measured from, so the two
        figures describe the same link and can be compared meaningfully.
        """
        key = (
            f"monitored_interfaces_{self.router_id}"
            if self.router_id else "monitored_interfaces_default"
        )
        setting = await session.get(AppSetting, key)
        if setting and setting.value:
            try:
                names = json.loads(setting.value)
                if isinstance(names, list) and names:
                    return [str(n) for n in names]
            except (json.JSONDecodeError, TypeError):
                logger.debug(f"Could not parse {key} for self-traffic accounting")
        return ["ether1"]

    async def sync_counter_rules(self, session: AsyncSession) -> Dict[str, int]:
        """Create, correct and prune the per-device accounting rules.

        Returns a small summary dict for logging/telemetry.
        """
        devices = await self._accountable_devices(session)
        desired: Dict[Tuple[int, str], str] = {}
        for device in devices:
            ip = (device.ip_address or "").strip()
            for direction in DIRECTION_FIELDS:
                desired[(device.id, direction)] = ip

        try:
            rules = await self.router_client.get_mangle_rules()
        except Exception as e:
            logger.warning(f"Could not read mangle rules for accounting sync: {e}")
            return {"created": 0, "updated": 0, "removed": 0}

        existing: Dict[Tuple[int, str], Dict[str, Any]] = {}
        existing_self: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for rule in rules:
            parsed = parse_acct_comment(rule.get("comment"))
            if parsed:
                existing[parsed] = rule
                continue
            parsed_self = parse_self_comment(rule.get("comment"))
            if parsed_self:
                existing_self[parsed_self] = rule

        created = updated = removed = 0

        # The router's own input/output traffic, one passthrough pair per WAN
        # interface. Created before the device rules are pruned so a freshly
        # monitored interface starts counting on the same tick it is added.
        for interface in await self._monitored_interfaces(session):
            for direction, (chain, field) in SELF_DIRECTION_RULES.items():
                if (direction, interface) in existing_self:
                    continue
                try:
                    await self.router_client.create_mangle_rule({
                        "chain": chain,
                        "action": "passthrough",
                        field: interface,
                        "comment": SELF_COMMENT.format(direction=direction, interface=interface),
                    })
                    created += 1
                except Exception as e:
                    logger.warning(
                        f"Failed to create self-traffic rule for {interface} ({direction}): {e}"
                    )

        # Create or correct
        for key, ip in desired.items():
            device_id, direction = key
            field = DIRECTION_FIELDS[direction]
            rule = existing.get(key)
            if rule is None:
                try:
                    await self.router_client.create_mangle_rule({
                        "chain": "forward",
                        "action": "passthrough",
                        field: ip,
                        "comment": ACCT_COMMENT.format(device_id=device_id, direction=direction),
                    })
                    created += 1
                except Exception as e:
                    logger.warning(f"Failed to create accounting rule for device {device_id}: {e}")
            elif (rule.get(field) or "").split("/")[0] != ip:
                # The device's IP changed - repoint the rule. Its counter keeps
                # running, and the baseline logic absorbs the discontinuity.
                try:
                    await self.router_client.update_mangle_rule(rule[".id"], {field: ip})
                    updated += 1
                except Exception as e:
                    logger.warning(f"Failed to repoint accounting rule for device {device_id}: {e}")

        # Prune rules for devices that are gone or no longer accountable.
        #
        # Read each counter one last time and flush the bytes accrued since the
        # previous baseline BEFORE deleting the rule, otherwise every active ->
        # inactive transition drops up to one interval of that device's traffic.
        # That is normally negligible (a device about to go idle is already
        # idle), but across a router outage that also spanned the device
        # dropping off, it is minutes of real volume. main.py additionally runs
        # collect() before this method so the common path never reaches here
        # with unflushed bytes; this is the backstop.
        to_prune = {key: rule for key, rule in existing.items() if key not in desired}
        if to_prune:
            final_deltas: Dict[int, Tuple[int, int]] = {}
            baselines = await self._load_baselines(session, self.router_id)
            for (device_id, direction), rule in to_prune.items():
                try:
                    current = int(rule.get("bytes", 0) or 0)
                except (TypeError, ValueError):
                    current = 0
                bkey = f"{device_id}:{direction}"
                delta = self.compute_delta(current, baselines.get(bkey))
                baselines.pop(bkey, None)
                if delta <= 0:
                    continue
                down, up = final_deltas.get(device_id, (0, 0))
                if direction == "down":
                    final_deltas[device_id] = (down + delta, up)
                else:
                    final_deltas[device_id] = (down, up + delta)

            if final_deltas:
                await self._flush_deltas(session, await router_local_date(session), final_deltas)
            await self._save_baselines(session, baselines, self.router_id)

            # bytes for these device ids, so their redirects have done their job.
            successors = await self._load_successors(session)
            pruned_ids = {str(device_id) for device_id, _ in to_prune}
            if successors.keys() & pruned_ids:
                await self._save_successors(
                    session, {k: v for k, v in successors.items() if k not in pruned_ids}
                )
            await session.commit()

        for key, rule in to_prune.items():
            try:
                await self.router_client.delete_mangle_rule(rule[".id"])
                removed += 1
            except Exception as e:
                logger.warning(f"Failed to remove stale accounting rule {rule.get('.id')}: {e}")

        if created or updated or removed:
            logger.info(
                f"Accounting rules synced: {created} created, {updated} repointed, {removed} removed"
            )
        # Mark whenever accounting rules are in place - not only when they were
        # created on this tick - so the marker is also recorded for an install
        # whose rules already existed before this bookkeeping was added.
        if desired:
            await self._mark_accounting_started(session)
        return {"created": created, "updated": updated, "removed": removed}

    @staticmethod
    async def _mark_accounting_started(session: AsyncSession) -> None:
        """Record the first day per-device accounting was active.

        Gateway (WAN interface) counters have been running since long before the
        accounting rules existed. Without this marker the coverage cross-check
        compares a full day of gateway volume against a partial day of device
        volume and reports a false 'degraded' alarm.
        """
        existing = await session.get(AppSetting, STARTED_SETTING_KEY)
        if existing:
            return
        session.add(AppSetting(
            key=STARTED_SETTING_KEY,
            value=(await router_local_date(session)).isoformat(),
            description="First date per-device mangle accounting was active",
        ))
        await session.commit()

    @staticmethod
    async def get_accounting_started(session: AsyncSession) -> Optional[date]:
        """Day per-device accounting became active, if it ever has."""
        setting = await session.get(AppSetting, STARTED_SETTING_KEY)
        if not setting or not setting.value:
            return None
        try:
            return date.fromisoformat(setting.value)
        except ValueError:
            return None

    # --- successors -------------------------------------------------------

    @staticmethod
    async def _load_successors(session: AsyncSession) -> Dict[str, int]:
        setting = await session.get(AppSetting, SUCCESSOR_SETTING_KEY)
        if setting and setting.value:
            try:
                return {str(k): int(v) for k, v in json.loads(setting.value).items()}
            except (json.JSONDecodeError, TypeError, ValueError):
                logger.warning("Accounting successor map corrupted; reinitialising")
        return {}

    @staticmethod
    async def _save_successors(session: AsyncSession, successors: Dict[str, int]) -> None:
        raw = json.dumps(successors)
        setting = await session.get(AppSetting, SUCCESSOR_SETTING_KEY)
        if setting:
            setting.value = raw
        else:
            session.add(AppSetting(
                key=SUCCESSOR_SETTING_KEY,
                value=raw,
                description="Where to credit counters of device rows that were merged away",
            ))

    @classmethod
    async def record_device_successor(
        cls, session: AsyncSession, dead_device_id: int, successor_device_id: int
    ) -> None:
        """Note that ``dead_device_id`` was absorbed into ``successor_device_id``.

        Call this whenever a device row is removed but its traffic legitimately
        continues under another record - a consolidated MAC rotation, or a
        manual merge. Bytes that its still-live mangle rules accrue before the
        next prune are then credited to the successor instead of vanishing.

        Existing entries pointing at the now-dead id are repointed, so a chain of
        merges never needs to be walked at read time.
        """
        successors = await cls._load_successors(session)
        successors[str(dead_device_id)] = successor_device_id
        for victim, target in list(successors.items()):
            if target == dead_device_id:
                successors[victim] = successor_device_id
        await cls._save_successors(session, successors)

    # --- baselines --------------------------------------------------------

    @classmethod
    def _baseline_key(cls, router_id: Optional[int] = None) -> str:
        return f"accounting_baselines_{router_id}" if router_id is not None else BASELINE_SETTING_KEY

    @classmethod
    async def _load_baselines_static(cls, session: AsyncSession, router_id: Optional[int] = None) -> Dict[str, Any]:
        key = cls._baseline_key(router_id)
        setting = await session.get(AppSetting, key)
        if not setting or not setting.value:
            if router_id == 1:
                setting = await session.get(AppSetting, BASELINE_SETTING_KEY)
            elif router_id is None:
                setting = await session.get(AppSetting, "accounting_baselines_1")
                if not setting or not setting.value:
                    setting = await session.get(AppSetting, BASELINE_SETTING_KEY)
            if not setting or not setting.value:
                return {}
        try:
            return json.loads(setting.value)
        except (json.JSONDecodeError, TypeError):
            return {}

    @classmethod
    async def _save_baselines_static(cls, session: AsyncSession, baselines: Dict[str, Any], router_id: Optional[int] = None) -> None:
        raw = json.dumps(baselines)
        key = cls._baseline_key(router_id)
        setting = await session.get(AppSetting, key)
        if setting:
            setting.value = raw
        else:
            session.add(
                AppSetting(
                    key=key,
                    value=raw,
                    description=f"Raw mangle counter baselines for router {router_id or 'default'}",
                )
            )
        # If router_id is None or 1, keep both default and scoped keys synchronized
        if router_id in (None, 1):
            other_key = "accounting_baselines_1" if key == BASELINE_SETTING_KEY else BASELINE_SETTING_KEY
            other_setting = await session.get(AppSetting, other_key)
            if other_setting:
                other_setting.value = raw
            else:
                session.add(AppSetting(key=other_key, value=raw))
        await session.commit()

    @classmethod
    async def _load_baselines(cls, session: AsyncSession, router_id: Optional[int] = None) -> Dict[str, Any]:
        return await cls._load_baselines_static(session, router_id)

    @classmethod
    async def _save_baselines(cls, session: AsyncSession, baselines: Dict[str, Any], router_id: Optional[int] = None) -> None:
        await cls._save_baselines_static(session, baselines, router_id)

    @staticmethod
    def compute_delta(current: int, previous: Optional[int], *, reset: bool = False) -> int:
        """Delta between successive readings of a monotonic counter.

        counter reset - the rule was recreated, or the router rebooted - only
        the bytes seen since that reset are credited, never a whole lifetime

        ``reset=True`` forces that path even when ``current >= previous``. A
        counter climbing fast enough can pass its stale pre-reboot value within
        a single poll interval; without an out-of-band reset signal that reads
        as a small ordinary delta and the bytes since the reboot are lost.
        """
        if previous is None:
            return 0
        if reset or current < previous:
            return current
        return current - previous

    # --- collection -------------------------------------------------------

    async def collect(
        self,
        session: AsyncSession,
        router_uptime_seconds: Optional[int] = None,
    ) -> Dict[str, int]:
        """Read counters, accumulate daily rollups, and persist new baselines.

        Device volume is authoritative; user volume is the sum of that user's
        devices, so the two levels can never disagree.

        ``router_uptime_seconds`` lets a reboot be recognised for certain: if it
        is lower than the value stored at the previous collection, the router
        restarted and every counter reset, so this tick credits the bytes since
        the reboot rather than differencing against a baseline that no longer
        exists. A network outage on its own is *not* a reboot - the router keeps
        counting throughout, and the first successful poll after it reconnects
        picks up the whole gap by ordinary differencing.
        """
        # Rollups are keyed by the router's date, not the container's: a UTC
        # container files the router's evening under the previous day.
        now_local = await router_local_now(session)
        today = now_local.date()

        try:
            rules = await self.router_client.get_mangle_rules()
        except Exception as e:
            logger.warning(f"Could not read mangle counters: {e}")
            return {"devices": 0, "bytes_in": 0, "bytes_out": 0}

        readings: Dict[int, Dict[str, int]] = {}
        # (direction, interface) -> counter, for the router's own input/output.
        self_readings: Dict[Tuple[str, str], int] = {}
        for rule in rules:
            try:
                value = int(rule.get("bytes", 0) or 0)
            except (TypeError, ValueError):
                continue
            parsed = parse_acct_comment(rule.get("comment"))
            if parsed:
                device_id, direction = parsed
                readings.setdefault(device_id, {})[direction] = value
                continue
            parsed_self = parse_self_comment(rule.get("comment"))
            if parsed_self:
                self_readings[parsed_self] = value

        if not readings and not self_readings:
            return {"devices": 0, "bytes_in": 0, "bytes_out": 0}

        baselines = await self._load_baselines(session, self.router_id)

        # When the last successful collection was on an earlier router-local
        # date, this tick's counter delta covers more than one day and is
        # apportioned across them by clock time. ``None`` means same-day (the
        # overwhelmingly common case) and the delta is credited whole to today.
        span_start: Optional[datetime] = None
        prev_raw = baselines.get(LAST_COLLECT_KEY)
        if isinstance(prev_raw, str):
            try:
                parsed_prev = datetime.fromisoformat(prev_raw)
                if parsed_prev < now_local and parsed_prev.date() < today:
                    span_start = parsed_prev
            except ValueError:
                pass
        baselines[LAST_COLLECT_KEY] = now_local.isoformat()

        # Did the router reboot since the last collection? Uptime running
        # backwards is proof; a first run (no stored uptime) is not.
        prev_uptime = baselines.get(UPTIME_BASELINE_KEY)
        is_first_run = prev_uptime is None and not any(":" in k for k in baselines)
        rebooted = (
            not is_first_run
            and router_uptime_seconds is not None
            and isinstance(prev_uptime, int)
            and router_uptime_seconds < prev_uptime
        )
        if rebooted:
            logger.info(
                f"Router {self.router_id} uptime dropped {prev_uptime}s -> {router_uptime_seconds}s: "
                f"treating all accounting counters as reset for this tick"
            )

        # device_id -> (downloaded, uploaded) accumulated this tick
        per_device: Dict[int, Tuple[int, int]] = {}
        for device_id, values in readings.items():
            deltas = {}
            for direction, current in values.items():
                key = f"{device_id}:{direction}"
                deltas[direction] = self.compute_delta(
                    current, baselines.get(key), reset=rebooted
                )
                baselines[key] = current
            down = deltas.get("down", 0)
            up = deltas.get("up", 0)
            if down or up:
                per_device[device_id] = (down, up)

        # The router's own traffic, summed across every monitored WAN interface.
        self_down = self_up = 0
        for (direction, interface), current in self_readings.items():
            key = f"self:{direction}:{interface}"
            delta = self.compute_delta(current, baselines.get(key), reset=rebooted)
            baselines[key] = current
            if direction == "down":
                self_down += delta
            else:
                self_up += delta

        if router_uptime_seconds is not None:
            baselines[UPTIME_BASELINE_KEY] = router_uptime_seconds

        total_in, total_out = await self._flush_deltas(
            session, today, per_device, span_start=span_start, span_end=now_local
        )
        if self_down or self_up:
            for day, d_in, d_out in self._spread(span_start, now_local, today, self_down, self_up):
                await self._add_rollup(
                    session, RouterSelfTrafficRollup, "router_id", self.router_id,
                    day, d_in, d_out,
                )

        await self._save_baselines(session, baselines, self.router_id)
        await session.commit()

        return {"devices": len(per_device), "bytes_in": total_in, "bytes_out": total_out}

    @staticmethod
    def _spread(
        span_start: Optional[datetime],
        span_end: datetime,
        today: date,
        down: int,
        up: int,
    ) -> List[Tuple[date, int, int]]:
        """``[(date, down, up)]`` for one delta.
        A single ``(today, down, up)`` entry when the tick did not cross a
        local midnight; otherwise the amount apportioned across the spanned
        days by clock time. The per-day parts always sum back to the input.
        """
        if span_start is None:
            return [(today, down, up)]
        return split_bytes_by_day(span_start, span_end, down, up)

    async def _flush_deltas(
        self,
        session: AsyncSession,
        today: date,
        per_device: Dict[int, Tuple[int, int]],
        *,
        span_start: Optional[datetime] = None,
        span_end: Optional[datetime] = None,
    ) -> Tuple[int, int]:
        """Credit accumulated ``{device_id: (down, up)}`` deltas to the rollups.

        Device volume is authoritative; each user's rollup is the sum of that
        user's devices, computed here, so the two levels can never disagree.
        Returns the ``(total_in, total_out)`` actually written.

        When ``span_start`` is given and sits on an earlier date than
        ``span_end``, each device's delta is spread across the days it spans
        (see :meth:`_spread`) rather than all landing on ``today``. The user
        rollups are aggregated per day from the same split.

        A reading for a device row that no longer exists is redirected to its
        successor when one was recorded (a consolidated rotation or a manual
        merge). Only a device that was genuinely deleted has nowhere to go, and
        its last few seconds of counter are then correctly discarded.
        """
        span_end = span_end or datetime.combine(today, datetime.min.time())
        total_in = total_out = 0
        # (user_id, date) -> (down, up)
        user_totals: Dict[Tuple[int, date], Tuple[int, int]] = {}
        successors: Optional[Dict[str, int]] = None

        for device_id, (down, up) in per_device.items():
            if not (down or up):
                continue
            device = await session.get(Device, device_id)
            if device is None:
                if successors is None:
                    successors = await self._load_successors(session)
                heir_id = successors.get(str(device_id))
                device = await session.get(Device, heir_id) if heir_id else None
                if device is None:
                    logger.debug(
                        f"Dropping {down + up} accounted bytes for removed device {device_id}"
                    )
                    continue
                device_id = device.id

            for day, d_in, d_out in self._spread(span_start, span_end, today, down, up):
                await self._add_rollup(
                    session, DeviceTrafficRollup, "device_id", device_id, day, d_in, d_out
                )
                if device.user_id:
                    prev = user_totals.get((device.user_id, day), (0, 0))
                    user_totals[(device.user_id, day)] = (prev[0] + d_in, prev[1] + d_out)
            total_in += down
            total_out += up

        for (user_id, day), (down, up) in user_totals.items():
            await self._add_rollup(
                session, TrafficRollup, "user_id", user_id, day, down, up
            )

        return total_in, total_out

    @staticmethod
    async def _add_rollup(
        session: AsyncSession,
        model: Any,
        fk_name: str,
        fk_value: Optional[int],
        record_date: date,
        bytes_in: int,
        bytes_out: int,
    ) -> None:
        """Add today's delta onto the existing daily rollup row, or create it.

        ``fk_value`` may be None for router self-traffic on a single-router
        install that predates multi-router support, so the lookup has to use
        ``IS NULL`` - ``= NULL`` matches nothing and would create a fresh row on
        every tick, turning one day's rollup into thousands.
        """
        column = getattr(model, fk_name)
        stmt = select(model).where(
            column.is_(None) if fk_value is None else column == fk_value,
            model.record_date == record_date,
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing:
            existing.bytes_in += bytes_in
            existing.bytes_out += bytes_out
        else:
            session.add(model(**{
                fk_name: fk_value,
                "record_date": record_date,
                "bytes_in": bytes_in,
                "bytes_out": bytes_out,
            }))
