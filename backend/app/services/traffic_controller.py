import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import User
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

    def __init__(self, router_client: RouterOSClient):
        self.router_client = router_client

    async def sync_user_queue(
        self,
        user_id: int,
        user_name: str,
        ip_addresses: List[str],
        speed_limit: str = "unlimited"
    ) -> Optional[str]:
        """Synchronize a user's Simple Queue on RouterOS.

        Returns:
            Queue ID on RouterOS or None if no IPs to queue.
        """
        valid_ips = [ip.strip() for ip in ip_addresses if ip and ip.strip()]
        queue_name = f"mikroman-user-{user_id}"
        comment_tag = f"mikroman:managed:user_{user_id}"
        max_limit = parse_bandwidth_string(speed_limit)

        try:
            existing_queues: List[SimpleQueueItem] = await self.router_client.get_simple_queues()
        except Exception as e:
            logger.error(f"Failed to fetch queues for sync: {e}")
            return None

        # Find existing queue for this user
        matched_queue = None
        for q in existing_queues:
            if q.name == queue_name or (q.comment and f"user_{user_id}" in q.comment):
                matched_queue = q
                break

        if not valid_ips:
            # If user has no active devices/IPs, delete or disable the queue
            if matched_queue and matched_queue.id:
                try:
                    await self.router_client.delete_simple_queue(matched_queue.id)
                except Exception as e:
                    logger.warning(f"Could not delete empty queue: {e}")
            return None

        target_str = ",".join([f"{ip}/32" if "/" not in ip else ip for ip in valid_ips])

        if matched_queue and matched_queue.id:
            # Update existing queue
            await self.router_client.update_simple_queue(
                queue_id=matched_queue.id,
                max_limit=max_limit,
                target=target_str,
                disabled=False
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
                    comment=f"mikroman:paused:user_{user.id}"
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
                if f"user_{user.id}" in comment or item.get("address") in [d.ip_address for d in user.devices]:
                    item_id = item.get(".id")
                    if item_id:
                        await self.router_client.remove_from_address_list(item_id)
        except Exception as e:
            logger.error(f"Failed to clear blocked IPs for user {user.id}: {e}")

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
            queue_map = {}

        user_metrics = []
        for user in users:
            q_name = f"mikroman-user-{user.id}"
            matched_q = queue_map.get(q_name)

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
