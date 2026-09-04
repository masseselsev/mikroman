"""Simple Queues - bandwidth *shaping* only.

Their byte counters are not used and must not be: on RouterOS 7.25 a freshly
created queue placed first in the order, targeting the busiest client, counted
0 bytes through a 4.9 MB burst. Volume is measured with mangle counters instead
(see :mod:`backend.app.services.routeros.firewall`). Shaping through queues
works correctly and is what they are kept for.
"""
import logging
from typing import List, Optional

from backend.app.schemas.traffic import SimpleQueueItem
from backend.app.services.guards import (
    guard_foreign_resources,
    guard_immune_targets,
    guard_queue_invariants,
)

logger = logging.getLogger("mikroman.routeros")


class QueuesMixin:
    """`/queue/simple` operations for :class:`RouterOSClient`."""

    # --- Simple Queue Operations ---

    async def get_simple_queues(self) -> List[SimpleQueueItem]:
        """Fetch all simple queues."""
        async with self._get_client() as client:
            resp = await client.get("/queue/simple")
            resp.raise_for_status()
            raw = resp.json()
            if not isinstance(raw, list):
                raw = [raw]

            results = []
            for item in raw:
                results.append(SimpleQueueItem(
                    id=item.get(".id"),
                    name=item.get("name", ""),
                    target=item.get("target", ""),
                    max_limit=item.get("max-limit", "0/0"),
                    rate=item.get("rate", "0/0"),
                    bytes=item.get("bytes", "0/0"),
                    comment=item.get("comment"),
                    disabled=item.get("disabled", "false") == "true" or item.get("disabled") is True,
                    parent=item.get("parent")
                ))
            return results

    async def create_simple_queue(
        self,
        name: str,
        target: str,
        max_limit: str = "0/0",
        comment: Optional[str] = None,
        parent: Optional[str] = None,
        limit_at: Optional[str] = None
    ) -> str:
        """Create a new Simple Queue (supporting hierarchical parent queue trees)."""
        payload = {
            "name": name,
            "target": target,
            "max-limit": max_limit,
            "comment": comment or "mikroman:managed"
        }
        if parent:
            payload["parent"] = parent
        if limit_at:
            payload["limit-at"] = limit_at

        target_val = payload.get("target", "")
        max_limit_val = payload.get("max-limit", "0/0")
        limit_at_val = payload.get("limit-at")
        name_val = payload.get("name")
        parent_val = payload.get("parent")
        guard_queue_invariants(target=target_val, max_limit=max_limit_val, limit_at=limit_at_val, parent=parent_val, name=name_val)
        if max_limit_val not in ("0/0", "0") and target_val:
            immune = self.get_immune_ips() if hasattr(self, "get_immune_ips") else set()
            guard_immune_targets(target_val, immune, action="queue")

        async with self._get_client() as client:
            resp = await client.put("/queue/simple", json=payload)
            resp.raise_for_status()
            res_data = resp.json()
            return res_data.get(".id", "")

    async def update_simple_queue(
        self,
        queue_id: str,
        name: Optional[str] = None,
        max_limit: Optional[str] = None,
        target: Optional[str] = None,
        disabled: Optional[bool] = None,
        comment: Optional[str] = None,
        parent: Optional[str] = None,
        limit_at: Optional[str] = None
    ) -> None:
        """Update an existing Simple Queue."""
        payload = {}
        if name is not None:
            payload["name"] = name
        if max_limit is not None:
            payload["max-limit"] = max_limit
        if target is not None:
            payload["target"] = target
        if disabled is not None:
            payload["disabled"] = "true" if disabled else "false"
        if comment is not None:
            payload["comment"] = comment
        if parent is not None:
            payload["parent"] = parent
        if limit_at is not None:
            payload["limit-at"] = limit_at

        target_val = payload.get("target", "")
        max_limit_val = payload.get("max-limit", "0/0")
        limit_at_val = payload.get("limit-at")
        name_val = payload.get("name")
        parent_val = payload.get("parent")
        guard_queue_invariants(target=target_val, max_limit=max_limit_val, limit_at=limit_at_val, parent=parent_val, name=name_val)
        if max_limit_val not in ("0/0", "0") and target_val:
            immune = self.get_immune_ips() if hasattr(self, "get_immune_ips") else set()
            guard_immune_targets(target_val, immune, action="queue")

        async with self._get_client() as client:
            resp = await client.patch(f"/queue/simple/{queue_id}", json=payload)
            resp.raise_for_status()

    async def delete_simple_queue(self, queue_id: str, comment: Optional[str] = None) -> None:
        """Delete a Simple Queue."""
        if comment is not None:
            guard_foreign_resources(comment, action="delete", resource_type="queue")

        async with self._get_client() as client:
            resp = await client.delete(f"/queue/simple/{queue_id}")
            resp.raise_for_status()

