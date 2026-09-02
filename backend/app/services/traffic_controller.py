import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import AppSetting, Device, User
from backend.app.schemas.traffic import SimpleQueueItem
from backend.app.services.queue_identity import (
    DEVICE_QUEUE_COMMENT,
    USER_QUEUE_COMMENT,
    USER_QUEUED_COMMENT,
    normalize_parent,
    normalize_rate_limit,
    normalize_target,
    queue_matches_device,
    queue_matches_user,
)
from backend.app.services.routeros import RouterOSClient

logger = logging.getLogger("mikroman.traffic_controller")


def parse_bandwidth_string(limit_str: str) -> str:
    """Format bandwidth limit into RouterOS upload/download format (e.g. '10M/50M').

    If single number or string like '20M', applies symmetric '20M/20M'.
    If 'unlimited' or empty, returns '0/0'.
    """
    if not limit_str or limit_str.lower() in ["unlimited", "0", "none"]:
        return "0/0"
    if "/" in limit_str:
        return limit_str
    return f"{limit_str}/{limit_str}"


def parse_rate_string(rate_str: Optional[str]) -> Tuple[int, int]:
    """Parse RouterOS rate 'rx_bps/tx_bps' into integer (upload_bps, download_bps)."""
    if not rate_str or "/" not in rate_str:
        return 0, 0
    try:
        parts = rate_str.split("/")
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return 0, 0


def parse_bytes_string(bytes_str: Optional[str]) -> Tuple[int, int]:
    """Parse RouterOS bytes 'bytes_in/bytes_out' into integer (upload_bytes, download_bytes)."""
    if not bytes_str or "/" not in bytes_str:
        return 0, 0
    try:
        parts = bytes_str.split("/")
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return 0, 0


DEFAULT_UNASSIGNED_LIMIT = "5M/5M"


async def resolve_unassigned_limit(session: AsyncSession, router_id: Optional[int] = None) -> str:
    """The quarantine bandwidth applied to devices that belong to nobody.

    Read from settings on every use rather than copied onto the device row.
    ``Device.speed_limit`` means "an explicit override the operator chose for
    this one device"; quarantine is a consequence of having no owner, so it is
    resolved here and disappears the moment the device is assigned.
    """
    key = f"unassigned_device_speed_limit_{router_id}" if router_id is not None else "unassigned_device_speed_limit"
    row = (await session.execute(
        select(AppSetting).where(AppSetting.key == key)
    )).scalar_one_or_none()
    if not row and router_id is not None:
        row = (await session.execute(
            select(AppSetting).where(AppSetting.key == "unassigned_device_speed_limit")
        )).scalar_one_or_none()
    return row.value if row and row.value else DEFAULT_UNASSIGNED_LIMIT


class TrafficController:
    """Controls per-user traffic shaping (Simple Queues) and firewall pausing."""

    _fasttrack_checked_at: dict = {}

    def __init__(self, router_client: RouterOSClient, router_id: Optional[int] = None):
        self.router_client = router_client
        self.router_id = router_id
    async def ensure_fasttrack_exemption(self) -> None:
        """Ensure FastTrack rule excludes mikroman_queued IPs so Simple Queues take effect."""
        client_key = getattr(self.router_client, "base_url", "default")
        import time
        now = time.time()
        # Check at most once every 5 minutes per router client
        if client_key in TrafficController._fasttrack_checked_at and (now - TrafficController._fasttrack_checked_at[client_key]) < 300:
            return

        try:
            rules = await self.router_client.get_firewall_filter_rules()
            for r in rules:
                if r.get("action") == "fasttrack-connection":
                    rule_id = r.get(".id")
                    src_list = r.get("src-address-list")
                    dst_list = r.get("dst-address-list")
                    if src_list != "!mikroman_queued" or dst_list != "!mikroman_queued":
                        logger.info(f"Configuring FastTrack rule {rule_id} with mikroman_queued exemption")
                        await self.router_client.update_firewall_filter_rule(rule_id, {
                            "src-address-list": "!mikroman_queued",
                            "dst-address-list": "!mikroman_queued"
                        })
            TrafficController._fasttrack_checked_at[client_key] = now
        except Exception as e:
            logger.warning(f"Could not check/update FastTrack exemption: {e}")

    async def sync_user_queue(
        self,
        user_id: int,
        user_name: str,
        ip_addresses: List[str],
        speed_limit: str = "unlimited"
    ) -> Optional[str]:
        """Synchronize a user's Simple Queue on RouterOS and manage FastTrack exemptions.

        Returns:
            Queue ID on RouterOS or None if no IPs to queue.
        """
        valid_ips = [ip.strip() for ip in ip_addresses if ip and ip.strip()]
        queue_name = f"mikroman-{user_name}"
        comment_tag = USER_QUEUE_COMMENT.format(user_id=user_id)
        max_limit = parse_bandwidth_string(speed_limit)
        # Send targets in the same /32 host form RouterOS stores them in, so the
        # value read back on the next tick compares equal and no write is issued.
        target_str = ",".join(f"{ip}/32" if "/" not in ip else ip for ip in valid_ips)

        try:
            existing_queues: List[SimpleQueueItem] = await self.router_client.get_simple_queues()
        except Exception as e:
            logger.error(f"Failed to fetch queues for sync: {e}")
            return None

        # Find existing queue for this user by exact comment tag or name variants
        matched_queue = None
        for q in existing_queues:
            if queue_matches_user(q, user_id, user_name):
                matched_queue = q
                break

        # Manage mikroman_queued address list for FastTrack bypass (required for Simple Queues accounting)
        try:
            existing_queued = await self.router_client.get_address_list("mikroman_queued")
            queued_tag = USER_QUEUED_COMMENT.format(user_id=user_id)
            legacy_tag = f"mikroman:queued:{user_name}"
            # Exact tag matching only - a substring test let user "M" claim the
            # entries belonging to "Mark".
            user_queued_entries = [
                item for item in existing_queued
                if (item.get("comment") or "").strip() in (queued_tag, legacy_tag)
                or item.get("address") in valid_ips
            ]

            if valid_ips:
                for ip in valid_ips:
                    matching_entry = next((item for item in user_queued_entries if item.get("address") == ip), None)
                    if not matching_entry:
                        await self.router_client.add_to_address_list(
                            address=ip,
                            list_name="mikroman_queued",
                            comment=queued_tag
                        )
                    elif (matching_entry.get("comment") or "").strip() != queued_tag:
                        # Migrate a legacy name-based tag to the stable id-based one
                        item_id = matching_entry.get(".id")
                        if item_id:
                            await self.router_client.remove_from_address_list(item_id)
                            await self.router_client.add_to_address_list(
                                address=ip,
                                list_name="mikroman_queued",
                                comment=queued_tag
                            )
                # Ensure FastTrack bypass rule is active
                await self.ensure_fasttrack_exemption()
            else:
                for item in user_queued_entries:
                    item_id = item.get(".id")
                    if item_id:
                        await self.router_client.remove_from_address_list(item_id)
        except Exception as e:
            logger.warning(f"Failed to sync mikroman_queued address list: {e}")

        if not valid_ips:
            # If user has no active devices/IPs, delete or disable the queue
            if matched_queue and matched_queue.id:
                try:
                    await self.router_client.delete_simple_queue(matched_queue.id)
                except Exception as e:
                    logger.warning(f"Could not delete empty queue: {e}")
            return None

        if matched_queue and matched_queue.id:
            # Only send update PATCH if fields actually changed. Values are
            # compared in RouterOS' own normalised form ("/32" targets, bps rate
            # limits); comparing raw strings made this permanently true and
            # rewrote every queue on every poll tick.
            needs_update = (
                matched_queue.name != queue_name
                or normalize_rate_limit(matched_queue.max_limit) != normalize_rate_limit(max_limit)
                or normalize_target(matched_queue.target) != normalize_target(target_str)
                or (matched_queue.comment or "") != comment_tag
            )
            if needs_update:
                await self.router_client.update_simple_queue(
                    queue_id=matched_queue.id,
                    name=queue_name,
                    max_limit=max_limit,
                    target=target_str,
                    disabled=False,
                    comment=comment_tag
                )
            return matched_queue.id
        else:
            # Create new queue
            queue_id = await self.router_client.create_simple_queue(
                name=queue_name,
                target=target_str,
                max_limit=max_limit,
                comment=comment_tag
            )
            return queue_id

    async def set_user_speed_limit(self, user_id: int, speed_limit: str, session: AsyncSession) -> bool:
        """Update speed limit for user in database and RouterOS."""
        user = await session.get(User, user_id)
        if not user:
            return False

        user.speed_limit = speed_limit
        await session.commit()
        await session.refresh(user)

        active_ips = [d.ip_address for d in user.devices if d.is_active and d.ip_address]
        await self.sync_user_queue(user.id, user.name, active_ips, speed_limit)
        return True

    async def ensure_pause_firewall_rules(self, session: AsyncSession) -> bool:
        """Ensure RouterOS has the drop filter rule and allowed LANs address-list for paused devices.

        Allows traffic to local LAN subnets, router services (input chain), and custom
        allowed subnets while dropping all other forwarded internet-bound traffic.
        """
        try:
            from backend.app.db.models import AppSetting
            key = f"pause_allowed_networks_{self.router_id}" if self.router_id is not None else "pause_allowed_networks"
            setting = await session.get(AppSetting, key)
            if not setting and self.router_id is not None:
                setting = await session.get(AppSetting, "pause_allowed_networks")
            raw_val = setting.value if setting and setting.value else "192.168.0.0/16,10.0.0.0/8,172.16.0.0/12"

            allowed_nets = set()
            for part in raw_val.replace("\n", ",").split(","):
                p = part.strip()
                if p:
                    allowed_nets.add(p)
            if not allowed_nets:
                allowed_nets = {"192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12"}

            # Sync mikroman_allowed_lans in /ip/firewall/address-list
            existing_allowed = await self.router_client.get_address_list("mikroman_allowed_lans")
            existing_ips = {item.get("address"): item.get(".id") for item in existing_allowed if item.get("address")}

            for ip, item_id in existing_ips.items():
                if ip not in allowed_nets and item_id:
                    try:
                        await self.router_client.remove_from_address_list(item_id)
                    except Exception as e:
                        logger.debug(f"Failed to remove {ip} from mikroman_allowed_lans: {e}")

            for net in allowed_nets:
                if net not in existing_ips:
                    try:
                        await self.router_client.add_to_address_list(
                            address=net,
                            list_name="mikroman_allowed_lans",
                            comment="mikroman:allowed_lan"
                        )
                    except Exception as e:
                        logger.debug(f"Failed to add {net} to mikroman_allowed_lans: {e}")

            # Ensure firewall filter drop rule exists
            # chain=forward action=drop src-address-list=mikroman_blocked dst-address-list=!mikroman_allowed_lans comment="mikroman:drop_blocked_internet"
            filter_rules = await self.router_client.get_firewall_filter_rules()
            target_rule = None
            for r in filter_rules:
                comment = r.get("comment", "")
                if comment in ("mikroman:drop_blocked_internet", "mikroman:drop_blocked_users"):
                    target_rule = r
                    break

            desired_payload = {
                "chain": "forward",
                "action": "drop",
                "src-address-list": "mikroman_blocked",
                "dst-address-list": "!mikroman_allowed_lans",
                "comment": "mikroman:drop_blocked_internet"
            }

            if not target_rule:
                try:
                    await self.router_client.create_firewall_filter_rule(desired_payload)
                except Exception as e:
                    logger.warning(f"Failed to create pause filter drop rule: {e}")
            else:
                rule_id = target_rule.get(".id")
                needs_update = (
                    target_rule.get("chain") != "forward" or
                    target_rule.get("action") != "drop" or
                    target_rule.get("src-address-list") != "mikroman_blocked" or
                    target_rule.get("dst-address-list") != "!mikroman_allowed_lans" or
                    target_rule.get("disabled") in (True, "true")
                )
                if needs_update and rule_id:
                    try:
                        await self.router_client.update_firewall_filter_rule(rule_id, {
                            "chain": "forward",
                            "action": "drop",
                            "src-address-list": "mikroman_blocked",
                            "dst-address-list": "!mikroman_allowed_lans",
                            "disabled": False,
                            "comment": "mikroman:drop_blocked_internet"
                        })
                    except Exception as e:
                        logger.debug(f"Failed to update pause filter drop rule: {e}")

            return True
        except Exception as e:
            logger.error(f"Error ensuring pause firewall rules: {e}")
            return False

    async def pause_user_internet(self, user_id: int, session: AsyncSession) -> bool:
        """Pause internet for user by adding active IPs to RouterOS mikroman_blocked address list."""
        user = await session.get(User, user_id)
        if not user:
            return False

        user.is_paused = True
        await session.commit()
        await session.refresh(user)

        await self.ensure_pause_firewall_rules(session)

        active_ips = [d.ip_address for d in user.devices if d.is_active and d.ip_address]
        for ip in active_ips:
            try:
                await self.router_client.add_to_address_list(
                    address=ip,
                    list_name="mikroman_blocked",
                    comment=f"mikroman:paused:{user.name}"
                )
            except Exception as e:
                logger.error(f"Failed to add {ip} to mikroman_blocked: {e}")

        return True

    async def resume_user_internet(self, user_id: int, session: AsyncSession) -> bool:
        """Resume internet for user by removing IPs from RouterOS mikroman_blocked address list."""
        user = await session.get(User, user_id)
        if not user:
            return False

        user.is_paused = False
        await session.commit()
        await session.refresh(user)

        try:
            blocked_items = await self.router_client.get_address_list("mikroman_blocked")
            for item in blocked_items:
                comment = item.get("comment", "")
                if f"user_{user.id}" in comment or f":paused:{user.name}" in comment or item.get("address") in [d.ip_address for d in user.devices]:
                    item_id = item.get(".id")
                    if item_id:
                        await self.router_client.remove_from_address_list(item_id)
        except Exception as e:
            logger.error(f"Failed to clear blocked IPs for user {user.id}: {e}")

        return True

    async def sync_device_queue(
        self,
        device_id: int,
        session: AsyncSession
    ) -> Optional[str]:
        """Synchronize an individual device's child Simple Queue on RouterOS.

        If device belongs to a user and has custom limits, creates a child queue under parent 'mikroman-{user_name}'.
        If device has 'default' limit, removes child queue so it is shaped by parent user queue directly.
        """
        device = await session.get(Device, device_id)
        if not device or not device.ip_address:
            return None

        clean_ip = device.ip_address.strip()
        user = await session.get(User, device.user_id) if device.user_id else None
        user_name = user.name if user else "unassigned"
        dev_display = device.custom_name or device.hostname or f"dev{device.id}"
        # Sanitize name for RouterOS
        safe_dev_name = "".join(c for c in dev_display if c.isalnum() or c in ("-", "_")).strip() or f"dev{device.id}"
        queue_name = f"mikroman-{user_name}-{safe_dev_name}"
        comment_tag = DEVICE_QUEUE_COMMENT.format(device_id=device.id)

        try:
            existing_queues: List[SimpleQueueItem] = await self.router_client.get_simple_queues()
        except Exception as e:
            logger.error(f"Failed to fetch queues for device sync: {e}")
            return None

        # Find existing device child queue by exact comment tag
        matched_queue = None
        for q in existing_queues:
            if queue_matches_device(q, device.id):
                matched_queue = q
                break

        # If device is inactive, remove queue
        if not device.is_active:
            if matched_queue and matched_queue.id:
                try:
                    await self.router_client.delete_simple_queue(matched_queue.id)
                except Exception as e:
                    logger.warning(f"Could not delete device child queue: {e}")
            return None

        # For unassigned devices, resolve 'default' to the configured unassigned quarantine limit
        effective_limit = device.speed_limit
        if device.user_id is None and effective_limit in ("default", None):
            effective_limit = await resolve_unassigned_limit(session)

        # Determine limits: "default" for user device means "0/0" child limit (bounded by parent user queue)
        if user is not None and effective_limit in ("default", None):
            max_limit = "0/0"
        else:
            max_limit = parse_bandwidth_string(effective_limit)

        parent_name = f"mikroman-{user_name}" if user else None

        # Add to mikroman_queued for FastTrack bypass
        try:
            existing_queued = await self.router_client.get_address_list("mikroman_queued")
            already_in = any(item.get("address") == clean_ip for item in existing_queued)
            if not already_in:
                await self.router_client.add_to_address_list(
                    address=clean_ip,
                    list_name="mikroman_queued",
                    comment=f"mikroman:queued:dev_{device.id}"
                )
            await self.ensure_fasttrack_exemption()
        except Exception as e:
            logger.warning(f"Failed to add device IP to mikroman_queued: {e}")

        target_str = f"{clean_ip}/32" if "/" not in clean_ip else clean_ip

        if matched_queue and matched_queue.id:
            # Only send update PATCH if fields actually changed
            needs_update = (
                matched_queue.name != queue_name
                or normalize_rate_limit(matched_queue.max_limit) != normalize_rate_limit(max_limit)
                or normalize_target(matched_queue.target) != normalize_target(target_str)
                or normalize_parent(matched_queue.parent) != normalize_parent(parent_name)
                or (matched_queue.comment or "") != comment_tag
            )
            if needs_update:
                await self.router_client.update_simple_queue(
                    queue_id=matched_queue.id,
                    name=queue_name,
                    max_limit=max_limit,
                    target=target_str,
                    parent=parent_name,
                    disabled=False,
                    comment=comment_tag
                )
            return matched_queue.id
        else:
            queue_id = await self.router_client.create_simple_queue(
                name=queue_name,
                target=target_str,
                max_limit=max_limit,
                parent=parent_name,
                comment=comment_tag
            )
            return queue_id

    async def set_device_speed_limit(self, device_id: int, speed_limit: str, session: AsyncSession) -> bool:
        """Update speed limit for an individual device."""
        device = await session.get(Device, device_id)
        if not device:
            return False

        device.speed_limit = speed_limit
        await session.commit()
        await session.refresh(device)

        await self.sync_device_queue(device_id, session)
        return True

    async def pause_device_internet(self, device_id: int, session: AsyncSession) -> bool:
        """Pause internet for a single device by adding its IP to mikroman_blocked."""
        device = await session.get(Device, device_id)
        if not device or not device.ip_address:
            return False

        device.is_paused = True
        await session.commit()
        await session.refresh(device)

        await self.ensure_pause_firewall_rules(session)

        clean_ip = device.ip_address.strip()
        try:
            await self.router_client.add_to_address_list(
                address=clean_ip,
                list_name="mikroman_blocked",
                comment=f"mikroman:paused:dev_{device.id}"
            )
        except Exception as e:
            logger.error(f"Failed to add device {device.id} to mikroman_blocked: {e}")

        return True

    async def resume_device_internet(self, device_id: int, session: AsyncSession) -> bool:
        """Resume internet for a single device by removing its IP from mikroman_blocked."""
        device = await session.get(Device, device_id)
        if not device:
            return False

        device.is_paused = False
        await session.commit()
        await session.refresh(device)

        try:
            blocked_items = await self.router_client.get_address_list("mikroman_blocked")
            for item in blocked_items:
                comment = item.get("comment", "")
                if f"dev_{device.id}" in comment or (device.ip_address and item.get("address") == device.ip_address.strip()):
                    item_id = item.get(".id")
                    if item_id:
                        await self.router_client.remove_from_address_list(item_id)
        except Exception as e:
            logger.error(f"Failed to unblock device {device.id}: {e}")

        return True

    @staticmethod
    async def _todays_user_volume(
        session: AsyncSession, router_id: Optional[int] = None
    ) -> Dict[int, Tuple[int, int]]:
        """Today's accumulated (download, upload) bytes per user from the rollups."""
        from backend.app.db.models import TrafficRollup
        from backend.app.services.router_time import router_local_date

        stmt = select(
            TrafficRollup.user_id, TrafficRollup.bytes_in, TrafficRollup.bytes_out
        ).where(TrafficRollup.record_date == await router_local_date(session, router_id=router_id))
        rows = (await session.execute(stmt)).all()
        return {row[0]: (int(row[1] or 0), int(row[2] or 0)) for row in rows}

    @staticmethod
    async def _todays_device_volume(
        session: AsyncSession, router_id: Optional[int] = None
    ) -> Dict[int, Tuple[int, int]]:
        """Today's accumulated (download, upload) bytes per device from the rollups."""
        from backend.app.db.models import DeviceTrafficRollup
        from backend.app.services.router_time import router_local_date

        stmt = select(
            DeviceTrafficRollup.device_id,
            DeviceTrafficRollup.bytes_in,
            DeviceTrafficRollup.bytes_out,
        ).where(DeviceTrafficRollup.record_date == await router_local_date(session, router_id=router_id))
        rows = (await session.execute(stmt)).all()
        return {row[0]: (int(row[1] or 0), int(row[2] or 0)) for row in rows}

    async def reconcile_managed_queues(self, session: AsyncSession, router_id: Optional[int] = None) -> int:
        """Delete managed Simple Queues whose owning user or device is gone.
        Per-object sync can only correct queues it is asked about, so a queue
        whose owner was deleted - or a device child queue whose custom limit was
        reverted to "inherit user" - was never revisited and stayed on RouterOS
        indefinitely, still carrying its old target and ``max-limit``.

        Only queues tagged as MikroMan-managed are considered; operator-created
        queues are never touched.

        Returns:
            Number of stale queues removed.
        """
        eff_router_id = router_id if router_id is not None else self.router_id
        try:
            queues = await self.router_client.get_simple_queues()
        except Exception as e:
            logger.warning(f"Could not read queues for reconciliation: {e}")
            return 0

        u_stmt = select(User)
        d_stmt = select(Device)
        if eff_router_id is not None:
            u_stmt = u_stmt.where((User.router_id == eff_router_id) | (User.router_id.is_(None)))
            d_stmt = d_stmt.where((Device.router_id == eff_router_id) | (Device.router_id.is_(None)))

        users = (await session.execute(u_stmt)).scalars().all()
        devices = (await session.execute(d_stmt)).scalars().all()

        # A user queue is wanted only while the user has somewhere to point it.
        # Derived from the device rows directly rather than the ORM relationship,
        # which can be stale on a long-lived session.
        live_user_ids = {
            d.user_id for d in devices
            if d.user_id and d.is_active and d.ip_address
        }
        name_to_user_id = {u.name: u.id for u in users}
        # A device keeps its own child queue only when it is shaped separately
        # from its parent user: unassigned (quarantine) or a custom limit.
        live_device_ids = {
            d.id for d in devices
            if d.is_active and d.ip_address and (d.user_id is None or d.speed_limit != "default")
        }

        removed = 0
        for queue in queues:
            comment = (queue.comment or "").strip()
            if not comment.startswith("mikroman:managed:"):
                continue
            suffix = comment[len("mikroman:managed:"):]

            if suffix.startswith("user_"):
                keep = self._parse_id(suffix[5:]) in live_user_ids
            elif suffix.startswith("dev_"):
                keep = self._parse_id(suffix[4:]) in live_device_ids
            else:
                # Legacy name-based tag from an older version.
                keep = name_to_user_id.get(suffix) in live_user_ids

            if not keep and queue.id:
                try:
                    await self.router_client.delete_simple_queue(queue.id)
                    removed += 1
                    logger.info(f"Removed stale managed queue '{queue.name}' ({comment})")
                except Exception as e:
                    logger.warning(f"Could not remove stale queue {queue.id}: {e}")

        return removed

    async def reconcile_device_limits(self, session: AsyncSession, router_id: Optional[int] = None) -> List[int]:
        """Clear quarantine limits left on devices that now belong to a user.

        Discovery used to copy the quarantine bandwidth onto ``speed_limit``.
        Assignment only ever set ``user_id``, so the copy survived and the device
        unlimited parent. The owner's limit was therefore never what actually
        applied, and the queue tree read as if someone had throttled the family
        at random.

        Discovery no longer writes that value, and migration 008 cleared the
        rows that already carried it. This pass is the standing guard: it runs
        on the queue-sync tick and catches anything that reintroduces the state -
        a restored backup, an older instance writing to the same database, or a
        future code path that copies the setting by mistake.

        Only an exact match against the *current* quarantine setting is cleared,
        so a limit the operator chose is left alone unless they happened to pick
        precisely the quarantine value. That trade is deliberate: the cost of
        clearing one deliberate limit is that the operator sets it again, while
        the cost of leaving one behind is a user silently capped at 5 Mbps.

        Returns:
            Ids of the devices whose limit was reset.
        """
        eff_router_id = router_id if router_id is not None else self.router_id
        quarantine = await resolve_unassigned_limit(session)

        stmt = select(Device).where(
            Device.user_id.is_not(None),
            Device.speed_limit == quarantine,
        )
        if eff_router_id is not None:
            stmt = stmt.where((Device.router_id == eff_router_id) | (Device.router_id.is_(None)))
        stranded = (await session.execute(stmt)).scalars().all()
        if not stranded:
            return []

        for device in stranded:
            logger.info(
                f"Device {device.id} ({device.custom_name or device.mac_address}) belongs to "
                f"user {device.user_id} but still carried the quarantine limit "
                f"{quarantine}; reverting to the owner's limit"
            )
            device.speed_limit = "default"
        await session.commit()

        # Rebuild each affected queue so the router agrees with the database
        # immediately, rather than on whichever later tick happens to touch it.
        for device in stranded:
            await self.sync_device_queue(device.id, session)

        return [d.id for d in stranded]

    @staticmethod
    def _parse_id(raw: str) -> Optional[int]:
        """Parse a numeric id out of a comment tag, or None if malformed."""
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    async def get_realtime_traffic_stats(self, session: AsyncSession, router_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Live per-user throughput and today's volume, measured from firewall counters.

        Simple Queue ``rate``/``bytes`` are deliberately NOT used: on RouterOS 7.x
        they were observed frozen (one user pinned at 488 Kbps / 2.4 Mbps for
        hours while the WAN was idle, everyone else stuck at 0 bps). Rates are
        come from the same rollups the analytics view uses, so the dashboard and
        the reports can never disagree.
        """
        from backend.app.services.traffic_accounting import (
            aggregate_user_rates,
            live_rate_tracker,
        )

        eff_router_id = router_id if router_id is not None else self.router_id

        try:
            rules = await self.router_client.get_mangle_rules()
        except Exception as e:
            logger.warning(f"Could not read mangle rules for real-time stats: {e}")
            rules = []

        per_device_rates = live_rate_tracker.sample(rules)
        user_rates = await aggregate_user_rates(session, per_device_rates)
        user_volume = await self._todays_user_volume(session, eff_router_id)
        device_volume = await self._todays_device_volume(session, eff_router_id)

        user_stmt = select(User)
        dev_stmt = select(Device)
        if eff_router_id is not None:
            user_stmt = user_stmt.where((User.router_id == eff_router_id) | (User.router_id.is_(None)))
            dev_stmt = dev_stmt.where((Device.router_id == eff_router_id) | (Device.router_id.is_(None)))

        users = (await session.execute(user_stmt)).scalars().all()
        all_devices = (await session.execute(dev_stmt)).scalars().all()
        devices_by_user: Dict[int, List[Device]] = {}
        for device in all_devices:
            if device.user_id:
                devices_by_user.setdefault(device.user_id, []).append(device)

        user_metrics = []
        for user in users:
            rates = user_rates.get(user.id, {})
            rate_in = int(rates.get("rx_bps", 0))
            rate_out = int(rates.get("tx_bps", 0))
            bytes_in, bytes_out = user_volume.get(user.id, (0, 0))

            # Per-device breakdown, so the dashboard can name the device that is
            # actually consuming the bandwidth rather than only its owner.
            # A device with no counter sample reports zero, never a stale value.
            owned = devices_by_user.get(user.id, [])
            device_metrics: Dict[int, Dict[str, int]] = {}
            for device in owned:
                d_rate = per_device_rates.get(device.id, {})
                d_in, d_out = device_volume.get(device.id, (0, 0))
                device_metrics[device.id] = {
                    "current_rate_in": int(d_rate.get("rx_bps", 0)),
                    "current_rate_out": int(d_rate.get("tx_bps", 0)),
                    "bytes_today_in": d_in,
                    "bytes_today_out": d_out,
                }

            user_metrics.append({
                "user_id": user.id,
                "name": user.name,
                "avatar_icon": user.avatar_icon,
                "speed_limit": user.speed_limit,
                "is_paused": user.is_paused,
                "device_count": len(owned),
                "active_device_count": len([d for d in owned if d.is_active]),
                "current_rate_in": rate_in,    # bps download
                "current_rate_out": rate_out,  # bps upload
                "bytes_in": bytes_in,
                "bytes_out": bytes_out,
                "devices": device_metrics,
            })

        return user_metrics
