"""Collapsing duplicate device records that are really one physical device.

Split out of ``device_manager`` because discovery and consolidation are separate
concerns that happen to share a client. Discovery asks "what is on the network
right now"; consolidation asks "which of these rows are the same phone wearing
different randomised MAC addresses", and answers it with evidence gathered over
days - a settling period, a co-presence register, an operator's judgement.

The three entry points, in ascending order of confidence:

* :meth:`find_merge_suggestions` - proposes, never acts.
* :meth:`consolidate_rotated_devices` - acts automatically, but only on rows
  that have been silent for ``mac_rotation_settle_hours`` and were never seen
  online beside their twin.
* :meth:`merge_devices` - does it now, because a person said so. Also the
  target of an accepted suggestion.

Consolidation is destructive: daily rollups are summed date by date, so the
individual shares are gone afterwards and no split can divide them back out.
Everything here is written on that assumption.

Composed into :class:`~backend.app.services.device_manager.DeviceManager` as a
mixin, so ``DeviceManager(client).merge_devices(...)`` keeps working unchanged
and callers never have to know about the split.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.db.models import (
    AlertLog,
    AppSetting,
    Device,
    DeviceCoexistence,
    DeviceHistory,
)
from backend.app.schemas.device import DeviceSuggestionDTO
from backend.app.services.mac_rotation import (
    canonical_pair,
    is_generic_hostname,
    normalise_hostname,
)
from backend.app.services.vendor_lookup import vendor_service

logger = logging.getLogger("mikroman.device_consolidation")

# How long a duplicate record must have been silent before the consolidation
# pass is willing to absorb it. A phone asleep for the evening is not yet
# evidence of a rotation; overridable via the `mac_rotation_settle_hours`
# app setting.
DEFAULT_ROTATION_SETTLE_HOURS = 48.0


class DeviceConsolidationMixin:
    """Merge and rotation-cleanup behaviour for :class:`DeviceManager`.

    Expects the host class to provide ``self.router_client`` and, where the
    queries below filter by router, ``self.router_id``.
    """

    async def find_merge_suggestions(self, session: AsyncSession) -> List[DeviceSuggestionDTO]:
        """Identifies unassigned devices that likely belong to an existing user device (e.g. MAC rotation)."""
        # Get unassigned devices
        unassigned_res = await session.execute(
            select(Device).where(Device.user_id == None).options(selectinload(Device.history))  # noqa: E711
        )
        unassigned_devs = unassigned_res.scalars().all()

        # Get assigned devices with their parent users
        assigned_res = await session.execute(
            select(Device).where(Device.user_id != None).options(selectinload(Device.user), selectinload(Device.history))  # noqa: E711
        )
        assigned_devs = assigned_res.scalars().all()

        # Pairs proven distinct by having been online together. A merge the
        # automatic pass would refuse must not be offered here either.
        coex_rows = (await session.execute(select(DeviceCoexistence))).scalars().all()
        coex_pairs = {canonical_pair(r.mac_a, r.mac_b) for r in coex_rows}

        suggestions: List[DeviceSuggestionDTO] = []

        for u_dev in unassigned_devs:
            u_host = (u_dev.hostname or "").strip().lower()
            u_vendor = (u_dev.vendor or "").strip().lower()
            # is_randomized_mac is a property of the DTO, not the ORM row - the
            # model carries only the address. Reading it off the Device object
            # raised AttributeError and 500'd this whole endpoint the moment an
            # inactive assigned device shared a vendor with an unassigned one,
            # which is exactly the state Rule 3 below exists to act on.
            u_is_random = vendor_service.is_randomized_mac(u_dev.mac_address)

            for a_dev in assigned_devs:
                if not a_dev.user:
                    continue

                # Ever seen online at the same instant as the candidate -> two
                # radios, not one rotated address. Never suggest merging them.
                if canonical_pair(u_dev.mac_address, a_dev.mac_address) in coex_pairs:
                    continue

                a_host = (a_dev.hostname or a_dev.custom_name or "").strip().lower()
                a_vendor = (a_dev.vendor or "").strip().lower()

                confidence = 0.0
                reason = ""

                # Rule 1: Exact hostname match on private MAC
                if u_host and a_host and u_host == a_host:
                    confidence = 0.95
                    reason = f"Identical hostname '{u_dev.hostname}' on user '{a_dev.user.name}'"
                # Rule 2: Substring or normalized hostname match
                elif u_host and a_host and (u_host in a_host or a_host in u_host) and len(u_host) > 3:
                    confidence = 0.80
                    reason = f"Matching name pattern '{u_dev.hostname}' ~ '{a_dev.hostname or a_dev.custom_name}'"
                # Rule 3: Target device is inactive, same vendor & private MAC
                elif not a_dev.is_active and u_is_random and u_vendor and a_vendor and u_vendor == a_vendor:
                    if "apple" in u_vendor or "pixel" in u_vendor or "samsung" in u_vendor:
                        confidence = 0.70
                        reason = f"Rotated private MAC for offline device '{a_dev.custom_name or a_dev.hostname}'"

                if confidence >= 0.70:
                    suggestions.append(DeviceSuggestionDTO(
                        unassigned_device_id=u_dev.id,
                        suggested_target_device_id=a_dev.id,
                        suggested_user_id=a_dev.user.id,
                        suggested_user_name=a_dev.user.name,
                        target_device_name=a_dev.custom_name or a_dev.hostname or a_dev.mac_address,
                        confidence=confidence,
                        reason=reason
                    ))

        return suggestions

    async def _absorb_device(self, session: AsyncSession, survivor: Device, victim: Device) -> None:
        """Fold ``victim``'s history and traffic into ``survivor`` and delete it.

        Unlike ``merge_devices`` this does NOT copy the victim's network
        coordinates onto the survivor: the survivor is the record that is
        currently present, so its MAC, IP and interface are the live ones and
        the victim's are the stale ones being retired.

        Reassignment goes through the relationships, not raw UPDATEs. ``history``
        and ``traffic_rollups`` are ``cascade="all, delete-orphan"``; a Core
        UPDATE would move the rows in the database but leave the session's
        in-memory collections pointing at them, and the cascade fired by
        ``session.delete(victim)`` would then delete the rows we just moved.
        """
        from sqlalchemy import update

        # Fresh collections: a device already in the session keeps whatever its
        # selectin relationships loaded when it first appeared, and a rollup
        # written since would be missed and then duplicated onto the survivor.
        await session.refresh(victim, ["history", "traffic_rollups"])
        await session.refresh(survivor, ["traffic_rollups"])

        for record in list(victim.history):
            record.device = survivor

        # Daily traffic rollups: add onto the survivor's row for the same date,
        # move the rest. Losing these silently is how a merge used to erase a
        # device's accumulated volume.
        survivor_by_date = {r.record_date: r for r in survivor.traffic_rollups}
        for vr in list(victim.traffic_rollups):
            existing = survivor_by_date.get(vr.record_date)
            if existing:
                existing.bytes_in += vr.bytes_in
                existing.bytes_out += vr.bytes_out
                vr.device = None  # orphaned -> deleted on flush
            else:
                vr.device = survivor
                survivor_by_date[vr.record_date] = vr

        await session.flush()

        # Anything that treated the victim as its primary adapter now points at
        # the survivor; a resulting self-reference is cleared.
        await session.execute(
            update(Device)
            .where(Device.linked_to_device_id == victim.id)
            .values(linked_to_device_id=survivor.id)
        )
        if survivor.linked_to_device_id == survivor.id:
            survivor.linked_to_device_id = None
        victim.linked_to_device_id = None

        seen = victim.last_seen.strftime("%Y-%m-%d %H:%M") if victim.last_seen else "unknown"
        session.add(DeviceHistory(
            device_id=survivor.id,
            mac_address=victim.mac_address,
            hostname=survivor.hostname,
            ip_address=survivor.ip_address,
            event_type="mac_rotated",
            details=(
                f"Consolidated a rotated record ({victim.mac_address}, last seen "
                f"{seen} UTC) into this device"
            ),
        ))
        # The victim's mangle counters keep running until the next accounting
        # sync prunes them; redirect their final bytes onto the survivor.
        from backend.app.services.traffic_accounting import TrafficAccountingService
        await TrafficAccountingService.record_device_successor(session, victim.id, survivor.id)

        await session.delete(victim)
        await session.flush()

    async def consolidate_rotated_devices(
        self, session: AsyncSession, *, settle_hours: Optional[float] = None
    ) -> int:
        """Collapse the rows left behind by repeated private-MAC rotation.

        Discovery-time adoption (:func:`mac_rotation.find_rotation_candidate`)
        only fires when it can identify a *single* prior record for a returning
        device. The moment two or more duplicates for one phone exist - because
        rotations happened before adoption shipped, or an access-point change
        produced several in quick succession - it can no longer tell which to
        adopt onto and declines, so every further rotation adds another row.
        The dashboard fills with "Pixel-9-Pro-XL x5" and the queue tree grows a
        branch per ghost.

        This pass cleans that up after the fact. Devices are grouped by their
        normalised hostname, considering only rows that carry a randomized MAC.
        A group is consolidated when every row in it that has an owner has the
        *same* owner - the user has already asserted one identity by assigning
        them together. The survivor is the row that is currently active, or
        failing that the most recently seen; every other row's history and
        traffic move onto it and the row is deleted. Any unassigned duplicates
        in the group are adopted onto the same owner in the process, which is
        the automatic merge that manual suggestions used to require a click for.

        Two safeguards keep this from folding together devices that only *look*
        alike - three people who each own a bare "iPhone", or one person with
        two of the same model:

        * **Co-presence is decisive.** If any two rows in the group were ever
          seen online in the same discovery sweep (recorded in
          ``device_coexistence``), the group holds more than one physical device
          and is left completely alone. One radio cannot answer on two addresses
          at once, so co-presence is proof, not a guess.
        * **A quiet period must pass.** A duplicate row is only absorbed once it
          has been continuously silent for ``settle_hours`` (default
          :data:`DEFAULT_ROTATION_SETTLE_HOURS`, overridable via the
          ``mac_rotation_settle_hours`` app setting). A phone that is merely
          asleep for the evening is not yet evidence of a rotation; one unseen
          for two days is. Rows still inside the window are left for a later
          pass.

        A **generic** hostname ("iPhone", "android") keeps its extra bar on top
        of the above: the rows must also share one vendor and no more than one
        may be currently active.

        A group whose rows are split across two different users is left alone -
        that is two people who genuinely own a device of the same model, and
        guessing wrong would hand one person's device to the other.

        Returns:
            Number of duplicate rows removed.
        """
        if settle_hours is None:
            setting = (
                await session.execute(
                    select(AppSetting).where(AppSetting.key == "mac_rotation_settle_hours")
                )
            ).scalar_one_or_none()
            try:
                settle_hours = float(setting.value) if setting and setting.value else DEFAULT_ROTATION_SETTLE_HOURS
            except (TypeError, ValueError):
                settle_hours = DEFAULT_ROTATION_SETTLE_HOURS

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        settle_cutoff = now - timedelta(hours=settle_hours)

        # Every pair of addresses ever seen online together, in the same (low,
        # high) ordering the recorder used, so a membership test cannot miss one.
        coex_rows = (await session.execute(select(DeviceCoexistence))).scalars().all()
        coex_pairs = {canonical_pair(r.mac_a, r.mac_b) for r in coex_rows}

        devices = (await session.execute(select(Device))).scalars().all()

        groups: dict = {}
        for device in devices:
            if not vendor_service.is_randomized_mac(device.mac_address):
                continue
            name = normalise_hostname(device.custom_name) or normalise_hostname(device.hostname)
            if not name:
                continue
            groups.setdefault(name, []).append(device)

        removed = 0
        made_changes = False
        for name, members in groups.items():
            if len(members) < 2:
                continue

            owners = {d.user_id for d in members if d.user_id is not None}
            if len(owners) > 1:
                logger.info(
                    f"Not consolidating {len(members)} '{name}' records: they are split "
                    f"across users {sorted(owners)}, which looks like two real devices"
                )
                continue
            if not owners:
                # All unassigned - could be two guests with the same phone
                # model. Left for the manual merge-suggestions flow.
                continue

            # Co-presence: were any two of these rows ever online at the same
            # instant? If so this name is several radios, not one rotating phone.
            member_macs = sorted(d.mac_address for d in members)
            seen_together = [
                (a, b)
                for i, a in enumerate(member_macs)
                for b in member_macs[i + 1:]
                if canonical_pair(a, b) in coex_pairs
            ]
            if seen_together:
                logger.info(
                    f"Not consolidating {len(members)} '{name}' records: {len(seen_together)} "
                    f"pair(s) were seen online together, so they are separate physical devices"
                )
                if await self._should_warn_multiple(session, name, now):
                    session.add(AlertLog(
                        router_id=self.router_id,
                        alert_type="mac_rotated_multi",
                        message=(
                            f"'{name}' is more than one device on the network - two of them "
                            f"were seen online at the same time. Their records are kept "
                            f"separate; rename one or assign them to different people so they "
                            f"can be told apart."
                        ),
                    ))
                    made_changes = True
                continue

            if is_generic_hostname(name):
                vendors = {(d.vendor or "").strip().lower() for d in members if d.vendor}
                active_count = sum(1 for d in members if d.is_active)
                if len(vendors) > 1 or active_count > 1:
                    logger.info(
                        f"Not consolidating {len(members)} '{name}' records: generic "
                        f"hostname with {'mixed vendors' if len(vendors) > 1 else 'more than one online'}, "
                        f"could be two real devices"
                    )
                    continue

            target_user = owners.pop()
            # Prefer a currently-active row; break ties on last_seen.
            survivor = max(
                members,
                key=lambda d: (d.is_active, d.last_seen or datetime.min),
            )

            # Only absorb rows that have gone quiet long enough to be a rotation
            # rather than a device that is briefly offline. The survivor is
            # exempt - it is the one that is present now.
            candidates = [d for d in members if d.id != survivor.id]
            victims = [
                d for d in candidates
                if d.last_seen is None or d.last_seen < settle_cutoff
            ]
            deferred = len(candidates) - len(victims)
            if deferred:
                logger.info(
                    f"Deferring {deferred} '{name}' record(s): silent for less than "
                    f"{settle_hours:g}h, still could be a device in use"
                )
            if not victims:
                continue

            survivor.user_id = target_user
            for victim in victims:
                await self._absorb_device(session, survivor, victim)
                removed += 1
            made_changes = True

            session.add(AlertLog(
                router_id=self.router_id,
                alert_type="mac_rotated",
                message=(
                    f"Consolidated {len(victims)} rotated '{name}' record(s) into one device "
                    f"for user {target_user}. If two people genuinely own a '{name}', reassign "
                    f"the one that reappears."
                ),
            ))
            logger.info(
                f"Consolidated {len(victims)} rotated '{name}' record(s) into device "
                f"{survivor.id} (user {target_user})"
            )

        if made_changes:
            await session.commit()
        return removed

    async def _should_warn_multiple(self, session: AsyncSession, name: str, now: datetime) -> bool:
        """True if no "'<name>' is more than one device" alert was raised today.

        The background loop runs consolidation on every scan, and a co-presence
        record is permanent, so without this the same advisory would be logged
        every minute for as long as both phones stay on the network.
        """
        recent = (
            await session.execute(
                select(AlertLog)
                .where(
                    AlertLog.alert_type == "mac_rotated_multi",
                    AlertLog.message.like(f"%'{name}'%"),
                    AlertLog.created_at > now - timedelta(days=1),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        return recent is None

    async def merge_devices(self, session: AsyncSession, source_device_id: int, target_device_id: int, note: Optional[str] = None) -> Device:
        """Merge one device record into another, keeping the source's address.

        Used both by the automatic rotation suggestions and by an operator
        picking the target by hand. The source's MAC/IP become the surviving
        device's current coordinates because the source is the record the router
        saw most recently; the target's identity, owner and limits are kept.
        """
        if source_device_id == target_device_id:
            raise ValueError("A device cannot be merged into itself")

        source = await session.get(
            Device, source_device_id,
            options=[selectinload(Device.history), selectinload(Device.traffic_rollups)],
        )
        target = await session.get(
            Device, target_device_id,
            options=[selectinload(Device.history), selectinload(Device.traffic_rollups)],
        )

        if not source or not target:
            raise ValueError("Source or target device not found")

        # Force-refresh the collections: if either device was already in the
        # session (a prior refresh in the same request), its selectin
        # relationships were loaded then and the options above do not reload
        # them - so a rollup added since would be invisible and get duplicated.
        await session.refresh(source, ["history", "traffic_rollups"])
        await session.refresh(target, ["traffic_rollups"])

        old_target_mac = target.mac_address
        new_source_mac = source.mac_address
        source_ip = source.ip_address
        source_hostname = source.hostname
        source_interface = source.last_interface
        source_signal = source.last_wifi_signal
        source_active = source.is_active
        source_seen = source.last_seen
        source_vendor = source.vendor

        # Re-link history and daily traffic to the target. Assignment goes
        # through the `.device` relationship, not a Core UPDATE: `history` and
        # `traffic_rollups` are delete-orphan cascades, and a Core UPDATE would
        # leave the in-memory collections pointing at the moved rows so the
        # cascade on `session.delete(source)` would delete them again. Carrying
        # the rollups is new - leaving them behind orphaned every byte the
        # rotated-away MAC had moved, quietly shrinking the household totals.
        for h in list(source.history):
            h.device = target

        target_rolls_by_date = {r.record_date: r for r in target.traffic_rollups}
        for sr in list(source.traffic_rollups):
            existing = target_rolls_by_date.get(sr.record_date)
            if existing:
                existing.bytes_in += sr.bytes_in
                existing.bytes_out += sr.bytes_out
                sr.device = None  # orphaned -> deleted on flush
            else:
                sr.device = target
                target_rolls_by_date[sr.record_date] = sr

        # An operator merging two records by hand overrules the co-presence
        # evidence that kept them apart: drop the pair so the automatic
        # consolidation pass does not treat them as separate hardware forever.
        pair = canonical_pair(new_source_mac, old_target_mac)
        await session.execute(
            delete(DeviceCoexistence).where(
                DeviceCoexistence.mac_a == pair[0], DeviceCoexistence.mac_b == pair[1]
            )
        )

        # The source's mangle counters keep running until the next accounting
        # sync prunes them; redirect their final bytes onto the target.
        from backend.app.services.traffic_accounting import TrafficAccountingService
        await TrafficAccountingService.record_device_successor(session, source.id, target.id)

        # Delete source device and flush to avoid unique constraint conflict on mac_address
        await session.delete(source)
        await session.flush()

        # Update target with latest network coordinates from source
        target.mac_address = new_source_mac
        target.ip_address = source_ip or target.ip_address
        if source_hostname:
            target.hostname = source_hostname
        target.last_interface = source_interface or target.last_interface
        target.last_wifi_signal = source_signal or target.last_wifi_signal
        target.is_active = source_active
        target.last_seen = source_seen
        if source_vendor and source_vendor != "Unknown Vendor":
            target.vendor = source_vendor

        # Log rotation / merge in target device history
        session.add(DeviceHistory(
            device_id=target.id,
            mac_address=new_source_mac,
            hostname=target.hostname,
            ip_address=target.ip_address,
            event_type="mac_rotated",
            details=note or f"Rotated MAC from {old_target_mac} to {new_source_mac}"
        ))

        await session.commit()
        await session.refresh(target)
        return target
