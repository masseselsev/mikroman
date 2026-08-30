import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import AppSetting, Device, User
from backend.app.schemas.traffic import SimpleQueueItem
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


class TrafficController:
    """Controls per-user traffic shaping (Simple Queues) and firewall pausing."""

    _fasttrack_checked_at: dict = {}

    def __init__(self, router_client: RouterOSClient):
        self.router_client = router_client

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
        comment_tag = f"mikroman:managed:{user_name}"
        max_limit = parse_bandwidth_string(speed_limit)
        target_str = ",".join(valid_ips)

        try:
            existing_queues: List[SimpleQueueItem] = await self.router_client.get_simple_queues()
        except Exception as e:
            logger.error(f"Failed to fetch queues for sync: {e}")
            return None

        # Find existing queue for this user by comment tag or name variants
        matched_queue = None
        for q in existing_queues:
            if (q.comment and (f"user_{user_id}" in q.comment or f":managed:{user_name}" in q.comment)) or q.name in (queue_name, f"mikroman-user-{user_id}", user_name):
                matched_queue = q
                break

        # Manage mikroman_queued address list for FastTrack bypass (required for Simple Queues accounting)
        try:
            existing_queued = await self.router_client.get_address_list("mikroman_queued")
            user_queued_entries = [
                item for item in existing_queued
                if (item.get("comment") and (f"user_{user_id}" in item.get("comment") or f":queued:{user_name}" in item.get("comment")))
                or item.get("address") in valid_ips
            ]

            if valid_ips:
                for ip in valid_ips:
                    matching_entry = next((item for item in user_queued_entries if item.get("address") == ip), None)
                    if not matching_entry:
                        await self.router_client.add_to_address_list(
                            address=ip,
                            list_name="mikroman_queued",
                            comment=f"mikroman:queued:{user_name}"
                        )
                    elif matching_entry.get("comment") != f"mikroman:queued:{user_name}":
                        # Refresh comment from legacy user_id to user_name
                        item_id = matching_entry.get(".id")
                        if item_id:
                            await self.router_client.remove_from_address_list(item_id)
                            await self.router_client.add_to_address_list(
                                address=ip,
                                list_name="mikroman_queued",
                                comment=f"mikroman:queued:{user_name}"
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
            # Only send update PATCH if fields actually changed
            needs_update = (
                matched_queue.name != queue_name
                or matched_queue.max_limit != max_limit
                or (matched_queue.target and matched_queue.target.strip() != target_str.strip())
                or matched_queue.comment != comment_tag
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

    async def pause_user_internet(self, user_id: int, session: AsyncSession) -> bool:
        """Pause internet for user by adding active IPs to RouterOS mikroman_blocked address list."""
        user = await session.get(User, user_id)
        if not user:
            return False

        user.is_paused = True
        await session.commit()
        await session.refresh(user)

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
        comment_tag = f"mikroman:managed:dev_{device.id}"

        try:
            existing_queues: List[SimpleQueueItem] = await self.router_client.get_simple_queues()
        except Exception as e:
            logger.error(f"Failed to fetch queues for device sync: {e}")
            return None

        # Find existing device child queue
        matched_queue = None
        for q in existing_queues:
            if (q.comment and f"dev_{device.id}" in q.comment) or q.name in (queue_name, f"mikroman-dev-{device.id}"):
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
            setting_res = await session.execute(select(AppSetting).where(AppSetting.key == "unassigned_device_speed_limit"))
            setting_row = setting_res.scalar_one_or_none()
            effective_limit = setting_row.value if setting_row else "5M/5M"

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
                or matched_queue.max_limit != max_limit
                or (matched_queue.target and matched_queue.target.strip() != target_str.strip())
                or getattr(matched_queue, "parent", None) != parent_name
                or matched_queue.comment != comment_tag
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

    async def get_realtime_traffic_stats(self, session: AsyncSession) -> List[Dict[str, Any]]:
        """Fetch live queues and map metrics to active users."""
        result = await session.execute(select(User))
        users = result.scalars().all()

        try:
            queues = await self.router_client.get_simple_queues()
            queue_map = {q.name: q for q in queues}
        except Exception as e:
            logger.error(f"Failed to fetch real-time queue stats: {e}")
            queues = []
            queue_map = {}

        user_metrics = []
        for user in users:
            matched_q = queue_map.get(f"mikroman-{user.name}") or queue_map.get(f"mikroman-user-{user.id}") or queue_map.get(user.name)
            if not matched_q:
                for q in queues:
                    if q.comment and (f"user_{user.id}" in q.comment or f":managed:{user.name}" in q.comment):
                        matched_q = q
                        break

            rate_in, rate_out = (0, 0)
            bytes_in, bytes_out = (0, 0)
            if matched_q:
                # rate: "upload/download", bytes: "upload/download"
                rate_out, rate_in = parse_rate_string(matched_q.rate)
                bytes_out, bytes_in = parse_bytes_string(matched_q.bytes)

            user_metrics.append({
                "user_id": user.id,
                "name": user.name,
                "avatar_icon": user.avatar_icon,
                "speed_limit": user.speed_limit,
                "is_paused": user.is_paused,
                "device_count": len(user.devices),
                "active_device_count": len([d for d in user.devices if d.is_active]),
                "current_rate_in": rate_in,    # bps download
                "current_rate_out": rate_out,  # bps upload
                "bytes_in": bytes_in,
                "bytes_out": bytes_out,
            })

        return user_metrics
