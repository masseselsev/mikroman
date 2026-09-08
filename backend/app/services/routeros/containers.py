"""RouterOS container management.

Container support ships as a separate, opt-in package that is absent from a
default install and cannot be enabled without a reboot. The *read* methods here
tolerate that: the REST endpoints simply 404 or error, and the caller decides
how to present it rather than being handed an exception.

The *write* methods at the bottom deliberately do not. Provisioning a router to
host a container is a sequence in which a silently refused step is worse than an
error - a container whose ``layer-dir`` never moved off internal flash starts up
fine and then fails when it runs out of room - so each one raises
:class:`~backend.app.services.routeros.provisioning.RouterOSCommandError` with
the router's own message.
"""
import logging
from typing import Any, Dict, List

from backend.app.services.routeros.provisioning import RouterOSCommandError, _detail

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

        Raises RouterOSCommandError rather than calling ``raise_for_status``:
        the setup walk has to record *which* step the router refused and why, and
        a bare HTTPStatusError escaping the endpoint turned a precise
        "unknown parameter mounts" into an anonymous HTTP 500.
        """
        async with self._get_client() as client:
            resp = await client.post("/container/add", json=payload)
            if resp.status_code not in (200, 201, 204):
                raise RouterOSCommandError("/container/add", resp.status_code, _detail(resp))
            try:
                body = resp.json()
            except Exception:
                return {}
            return body if isinstance(body, dict) else {"result": body}

    # --- Writes the setup needs -----------------------------------------------
    # The reads above answer [] when the package is absent, because that is a
    # state worth rendering. These must not be tolerant: a setup that half
    # applied (a mount that was refused, a tmpdir still on internal flash) leaves
    # a container that starts and then fails somewhere much worse - so every one
    # of them raises RouterOSCommandError with the router's own message.

    async def set_container_config(self, fields: Dict[str, str]) -> bool:
        """Set global container settings (``layer-dir``, ``tmpdir``, ``dns-servers``).

        ``/container/config`` is a single-row menu, so this is a set, not an add.
        """
        if not fields:
            return True
        async with self._get_client() as client:
            resp = await client.post("/container/config/set", json=fields)
            if resp.status_code not in (200, 201, 204):
                raise RouterOSCommandError("/container/config/set", resp.status_code, _detail(resp))
            return True

    async def add_container_mount(self, list_name: str, src: str, dst: str) -> Dict[str, Any]:
        """Bind a path on the router to a path inside the container.

        The grouping field is ``list``, not ``name`` - RouterOS answers
        ``unknown parameter name`` and then ``missing =list=`` on the way to
        proving it, which is why this takes an explicit argument name.
        """
        return await self._run(
            "/container/mounts/add", {"list": list_name, "src": src, "dst": dst}
        )

    async def remove_container_mount(self, mount_id: str) -> bool:
        return await self.remove_by_id("/container/mounts", mount_id)

    async def add_container_env(self, list_name: str, key: str, value: str) -> Dict[str, Any]:
        """Add one variable to a named env list (``/container/envs``).

        Several rows share one ``list``; the container references that list by
        name via ``envlist``.
        """
        return await self._run(
            "/container/envs/add", {"list": list_name, "key": key, "value": value}
        )

    async def remove_container_env(self, env_id: str) -> bool:
        return await self.remove_by_id("/container/envs", env_id)
