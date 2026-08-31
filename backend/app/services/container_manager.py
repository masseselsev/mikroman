"""Read and drive RouterOS containers.

Container support is an optional package that a stock RouterOS install does not
carry and cannot enable without a reboot, so the first thing this does on every
call is establish whether the feature is usable at all. When it is not, the
overview still comes back - just with an empty container list and a ``support``
block that explains why - so the page can show a banner rather than an error.
"""
import logging
from typing import Any, Dict, List, Optional

from backend.app.schemas.container import (
    ContainerConfigDTO,
    ContainerDTO,
    ContainerEnvDTO,
    ContainerMountDTO,
    ContainerOverviewDTO,
    ContainerSupportDTO,
)
from backend.app.services.routeros import RouterOSClient

logger = logging.getLogger("mikroman.container_manager")


def _as_bool(value: Any) -> Optional[bool]:
    """RouterOS reports booleans as the strings 'true' / 'false' / 'yes' / 'no'."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "yes", "1"}


class ContainerManager:
    def __init__(self, client: RouterOSClient):
        self.client = client

    async def _probe_support(self) -> ContainerSupportDTO:
        """Is the container package present and enabled on this router?"""
        try:
            packages = await self.client.get_packages()
        except Exception as e:
            logger.warning(f"Could not read RouterOS packages: {e}")
            return ContainerSupportDTO(
                status="unreachable",
                message="Could not query the router for installed packages.",
            )

        pkg = next(
            (p for p in packages if str(p.get("name", "")).lower() == "container"),
            None,
        )
        if pkg is None:
            return ContainerSupportDTO(
                status="not_installed",
                message=(
                    "The 'container' package is not installed. Download the "
                    "extra-packages bundle for this RouterOS version and "
                    "architecture, upload container.npk, and reboot."
                ),
            )

        disabled = _as_bool(pkg.get("disabled")) or False
        if disabled:
            return ContainerSupportDTO(
                installed=True,
                enabled=False,
                version=pkg.get("version"),
                status="disabled",
                message="The container package is installed but disabled. Enable it and reboot.",
            )

        return ContainerSupportDTO(
            installed=True,
            enabled=True,
            version=pkg.get("version"),
            status="ready",
        )

    @staticmethod
    def _to_container(raw: Dict[str, Any]) -> ContainerDTO:
        return ContainerDTO(
            id=raw.get(".id", ""),
            name=raw.get("name"),
            tag=raw.get("tag"),
            status=raw.get("status"),
            os=raw.get("os"),
            arch=raw.get("arch"),
            interface=raw.get("interface"),
            root_dir=raw.get("root-dir"),
            mounts=raw.get("mounts"),
            envlist=raw.get("envlist"),
            cmd=raw.get("cmd"),
            entrypoint=raw.get("entrypoint"),
            hostname=raw.get("hostname"),
            logging=_as_bool(raw.get("logging")),
            start_on_boot=_as_bool(raw.get("start-on-boot")),
            comment=raw.get("comment"),
        )

    async def get_overview(self) -> ContainerOverviewDTO:
        support = await self._probe_support()
        if support.status != "ready":
            return ContainerOverviewDTO(support=support)

        containers: List[ContainerDTO] = []
        mounts: List[ContainerMountDTO] = []
        envs: List[ContainerEnvDTO] = []
        config = ContainerConfigDTO()

        try:
            containers = [self._to_container(c) for c in await self.client.get_containers()]
        except Exception as e:
            logger.warning(f"Could not list containers: {e}")

        try:
            mounts = [
                ContainerMountDTO(id=m.get(".id", ""), name=m.get("name"),
                                  src=m.get("src"), dst=m.get("dst"))
                for m in await self.client.get_container_mounts()
            ]
        except Exception as e:
            logger.debug(f"Could not list container mounts: {e}")

        try:
            envs = [
                ContainerEnvDTO(id=v.get(".id", ""), name=v.get("name"),
                                key=v.get("key"), value=v.get("value"))
                for v in await self.client.get_container_envs()
            ]
        except Exception as e:
            logger.debug(f"Could not list container envs: {e}")

        try:
            raw_cfg = await self.client.get_container_config()
            config = ContainerConfigDTO(
                tmpdir=raw_cfg.get("tmpdir"),
                registry_url=raw_cfg.get("registry-url"),
                ram_high=raw_cfg.get("ram-high"),
                layer_dir=raw_cfg.get("layer-dir"),
            )
        except Exception as e:
            logger.debug(f"Could not read container config: {e}")

        return ContainerOverviewDTO(
            support=support, containers=containers, mounts=mounts, envs=envs, config=config
        )

    async def run_action(self, action: str, container_id: str) -> bool:
        """start / stop / remove one container. Raises ValueError on a bad action."""
        if action not in {"start", "stop", "remove"}:
            raise ValueError(f"Unsupported container action: {action}")
        return await self.client.container_command(action, container_id)

    async def create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Build the RouterOS /container/add argument set and submit it."""
        args: Dict[str, Any] = {
            "remote-image": payload["remote_image"],
            "interface": payload["interface"],
            "start-on-boot": "yes" if payload.get("start_on_boot") else "no",
            "logging": "yes" if payload.get("logging", True) else "no",
        }
        for src, dst in (
            ("root_dir", "root-dir"),
            ("hostname", "hostname"),
            ("cmd", "cmd"),
            ("entrypoint", "entrypoint"),
            ("mounts", "mounts"),
            ("envlist", "envlist"),
            ("comment", "comment"),
        ):
            value = payload.get(src)
            if value:
                args[dst] = value
        return await self.client.add_container(args)
