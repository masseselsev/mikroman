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
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    AppSetting,
    Device,
    DeviceTrafficRollup,
    TrafficRollup,
)
from backend.app.services.router_time import router_local_date

logger = logging.getLogger("mikroman.traffic_accounting")

# Comment tag identifying a MikroMan accounting rule. Device id keeps the tag
# stable across renames and IP changes.
ACCT_COMMENT = "mikroman:acct:dev_{device_id}:{direction}"
ACCT_PREFIX = "mikroman:acct:"
BASELINE_SETTING_KEY = "acct_counter_baselines"
# Date on which per-device accounting first became active. Gateway counters
# predate it, so coverage must not be judged over earlier periods.
STARTED_SETTING_KEY = "accounting_started_at"

# Reserved key inside the baselines blob holding the router uptime (seconds) at
# the last successful collection. If uptime has gone backwards since, the router
# rebooted and every byte counter reset to zero at that moment.
UPTIME_BASELINE_KEY = "__router_uptime_s__"
# Uptime may appear to dip by a few seconds between polls from rounding and tick
# jitter; only a drop larger than this is treated as a reboot.
REBOOT_SLACK_SECONDS = 90

# direction -> RouterOS match field. A device is the *source* of its uploads and
# the *destination* of its downloads.
DIRECTION_FIELDS = {"up": "src-address", "down": "dst-address"}


def parse_acct_comment(comment: Optional[str]) -> Optional[Tuple[int, str]]:
    """Parse an accounting rule comment into ``(device_id, direction)``.

    Returns None for any comment that is not a MikroMan accounting tag, so
    unrelated user-defined mangle rules are never touched.
    """
    if not comment or not comment.startswith(ACCT_PREFIX):
        return None
    parts = comment.split(":")
    # mikroman : acct : dev_<id> : <direction>
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
        for rule in rules:
            parsed = parse_acct_comment(rule.get("comment"))
            if parsed:
                existing[parsed] = rule

        created = updated = removed = 0

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

        # Prune rules for devices that are gone or no longer accountable
        for key, rule in existing.items():
            if key not in desired:
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

    # --- baselines --------------------------------------------------------

    @staticmethod
    async def _load_baselines(session: AsyncSession) -> Dict[str, int]:
        setting = await session.get(AppSetting, BASELINE_SETTING_KEY)
        if setting and setting.value:
            try:
                return json.loads(setting.value)
            except json.JSONDecodeError:
                logger.warning("Accounting baselines corrupted; reinitialising")
        return {}

    @staticmethod
    async def _save_baselines(session: AsyncSession, baselines: Dict[str, int]) -> None:
        raw = json.dumps(baselines)
        setting = await session.get(AppSetting, BASELINE_SETTING_KEY)
        if setting:
            setting.value = raw
        else:
            session.add(AppSetting(
                key=BASELINE_SETTING_KEY,
                value=raw,
                description="Per-device mangle counter baselines for delta accounting",
            ))

    @staticmethod
    def compute_delta(current: int, previous: Optional[int], *, reset: bool = False) -> int:
        """Delta between successive readings of a monotonic counter.

        A first reading establishes a baseline and contributes nothing. When the
        counter reset - the rule was recreated, or the router rebooted - only
        the bytes seen since that reset are credited, never a whole lifetime
        total.

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
        today = await router_local_date(session)

        try:
            rules = await self.router_client.get_mangle_rules()
        except Exception as e:
            logger.warning(f"Could not read mangle counters: {e}")
            return {"devices": 0, "bytes_in": 0, "bytes_out": 0}

        readings: Dict[int, Dict[str, int]] = {}
        for rule in rules:
            parsed = parse_acct_comment(rule.get("comment"))
            if not parsed:
                continue
            device_id, direction = parsed
            try:
                value = int(rule.get("bytes", 0) or 0)
            except (TypeError, ValueError):
                continue
            readings.setdefault(device_id, {})[direction] = value

        if not readings:
            return {"devices": 0, "bytes_in": 0, "bytes_out": 0}

        baselines = await self._load_baselines(session)

        # Did the router reboot since the last collection? Uptime running
        # backwards is proof; a first run (no stored uptime) is not.
        prev_uptime = baselines.get(UPTIME_BASELINE_KEY)
        rebooted = (
            router_uptime_seconds is not None
            and isinstance(prev_uptime, int)
            and router_uptime_seconds + REBOOT_SLACK_SECONDS < prev_uptime
        )
        if rebooted:
            logger.info(
                f"Router uptime dropped {prev_uptime}s -> {router_uptime_seconds}s: "
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

        if router_uptime_seconds is not None:
            baselines[UPTIME_BASELINE_KEY] = router_uptime_seconds

        total_in = total_out = 0
        user_totals: Dict[int, Tuple[int, int]] = {}

        for device_id, (down, up) in per_device.items():
            device = await session.get(Device, device_id)
            if device is None:
                continue

            await self._add_rollup(
                session, DeviceTrafficRollup, "device_id", device_id, today, down, up
            )
            total_in += down
            total_out += up

            if device.user_id:
                prev = user_totals.get(device.user_id, (0, 0))
                user_totals[device.user_id] = (prev[0] + down, prev[1] + up)

        for user_id, (down, up) in user_totals.items():
            await self._add_rollup(
                session, TrafficRollup, "user_id", user_id, today, down, up
            )

        await self._save_baselines(session, baselines)
        await session.commit()

        return {"devices": len(per_device), "bytes_in": total_in, "bytes_out": total_out}

    @staticmethod
    async def _add_rollup(
        session: AsyncSession,
        model: Any,
        fk_name: str,
        fk_value: int,
        record_date: date,
        bytes_in: int,
        bytes_out: int,
    ) -> None:
        """Add today's delta onto the existing daily rollup row, or create it."""
        stmt = select(model).where(
            getattr(model, fk_name) == fk_value,
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
