"""RouterOS container management.

Container support ships as a separate, opt-in package that is absent from a
default install and cannot be enabled without a reboot. Every method here
tolerates that: the REST endpoints simply 404 or error, and the caller decides
how to present it rather than being handed an exception.
"""
import logging
from typing import Any, Dict, List

logger = logging.getLogger("mikroman.routeros")


class ContainersMixin:
    """`/container` and `/system/package` operations for :class:`RouterOSClient`."""

    # --- Containers -----------------------------------------------------------
    # RouterOS ships container support as a separate, opt-in package that is not
    # present on a default install and cannot be enabled without a reboot. Every
    # method here tolerates the package being absent: the REST endpoints simply
    # 404 / error, and the caller decides how to present that.

    async def get_packages(self) -> List[Dict[str, Any]]:
        """Installed RouterOS packages, each with ``name``/``version``/``disabled``."""
        async with self._get_client() as client:
            resp = await client.get("/system/package")
            if resp.status_code != 200:
                return []
            raw = resp.json()
            return raw if isinstance(raw, list) else [raw]

    async def get_containers(self) -> List[Dict[str, Any]]:
        """Every container known to RouterOS, or ``[]`` if the package is absent."""
        async with self._get_client() as client:
            resp = await client.get("/container")
            if resp.status_code != 200:
                return []
            raw = resp.json()
            return raw if isinstance(raw, list) else [raw]

    async def get_container_config(self) -> Dict[str, Any]:
        """Global container config (``tmpdir``, ``registry-url``, ``layer-dir`` …)."""
        async with self._get_client() as client:
            resp = await client.get("/container/config")
            if resp.status_code != 200:
                return {}
            raw = resp.json()
            if isinstance(raw, list):
                return raw[0] if raw else {}
            return raw or {}

    async def get_container_mounts(self) -> List[Dict[str, Any]]:
        """Configured container mount points (``/container/mounts``)."""
        async with self._get_client() as client:
            resp = await client.get("/container/mounts")
            if resp.status_code != 200:
                return []
            raw = resp.json()
            return raw if isinstance(raw, list) else [raw]

    async def get_container_envs(self) -> List[Dict[str, Any]]:
        """Configured container environment variables (``/container/envs``)."""
        async with self._get_client() as client:
            resp = await client.get("/container/envs")
            if resp.status_code != 200:
                return []
            raw = resp.json()
            return raw if isinstance(raw, list) else [raw]

    async def container_command(self, action: str, container_id: str) -> bool:
        """Run ``start`` / ``stop`` / ``remove`` against one container by id."""
        if action not in {"start", "stop", "remove"}:
            raise ValueError(f"Unsupported container action: {action}")
        async with self._get_client() as client:
            resp = await client.post(f"/container/{action}", json={".id": container_id})
            return resp.status_code in (200, 201, 204)

    async def add_container(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create a container from a remote image (``/container/add``).

        ``payload`` is passed through to RouterOS - typically
        ``{"remote-image": "repo/name:tag", "interface": "veth1", ...}``.
        """
        async with self._get_client() as client:
            resp = await client.post("/container/add", json=payload)
            resp.raise_for_status()
            body = resp.json() if resp.content else {}
            return body if isinstance(body, dict) else {"result": body}
