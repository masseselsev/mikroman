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
    ContainerHostDTO,
    ContainerMountDTO,
    ContainerOverviewDTO,
    ContainerStorageDTO,
    ContainerSupportDTO,
)
from backend.app.services.container_setup import read_storage
from backend.app.services.routeros import RouterOSClient

logger = logging.getLogger("mikroman.container_manager")


def _as_bool(value: Any) -> Optional[bool]:
    """RouterOS reports booleans as the strings 'true' / 'false' / 'yes' / 'no'."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "yes", "1"}


def _as_count(value: Any) -> Optional[int]:
    """A byte count or percentage from a router that spells "no limit" as text.

    `memory-high` and `memory-max` read back as ``unlimited`` when no ceiling is
    set, and reporting that as 0 would tell the UI the container is out of room.
    """
    if value is None or value == "":
        return None
    text = str(value).strip()
    if text.lower() in {"unlimited", "none", "-", "auto"}:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _as_float(value: Any) -> Optional[float]:
    """A percentage the device spells as text, or None when it is not a number."""
    if value is None or value == "":
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _as_seconds(value: Any) -> Optional[int]:
    """RouterOS duration ("10s", "1m", "1d2h3m4s") in seconds."""
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    if text in {"none", "-", "unlimited"}:
        return None
    total = 0
    number = ""
    for char in text:
        if char.isdigit():
            number += char
            continue
        if not number:
            continue
        unit, value_of = char, int(number)
        number = ""
        if unit == "s":
            total += value_of
        elif unit == "m":
            total += value_of * 60
        elif unit == "h":
            total += value_of * 3600
        elif unit == "d":
            total += value_of * 86400
        elif unit == "w":
            total += value_of * 604800
    if number:  # a bare number in RouterOS durations means seconds
        total += int(number)
    return total or None


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
            # 7.x reports the live state as `running`; there is no `status`
            # attribute, which is why the table used to show a dash for every
            # container that was in fact up.
            running=_as_bool(raw.get("running")),
            os=raw.get("os"),
            arch=raw.get("arch"),
            interface=raw.get("interface"),
            root_dir=raw.get("root-dir"),
            # RouterOS binds mount and env rows through `mountlists` /
            # `envlists`; reading `mounts` returned nothing, so a container that
            # was mounted correctly looked unmounted in the table.
            mounts=raw.get("mountlists") or raw.get("mounts"),
            envlist=raw.get("envlists") or raw.get("envlist"),
            cmd=raw.get("cmd"),
            entrypoint=raw.get("entrypoint"),
            hostname=raw.get("hostname"),
            logging=_as_bool(raw.get("logging")),
            start_on_boot=_as_bool(raw.get("start-on-boot")),
            comment=raw.get("comment"),
            # Resource usage, reported live on the row - no extra call needed.
            # `cpu-usage` is a share of the whole device, and `memory-current`
            # is the cgroup figure, which counts the page cache the container
            # pushes through its data mount as well as its own heap.
            cpu_usage_pct=_as_float(raw.get("cpu-usage")),
            memory_current_bytes=_as_count(raw.get("memory-current")),
            memory_high_bytes=_as_count(raw.get("memory-high")),
            memory_max_bytes=_as_count(raw.get("memory-max")),
            disk_size_bytes=_as_count(raw.get("container-size")),
            restart_count=_as_count(raw.get("restart-count")),
            stop_time_seconds=_as_seconds(raw.get("stop-time")),
        )

    async def get_overview(self) -> ContainerOverviewDTO:
        support = await self._probe_support()
        if support.status != "ready":
            return ContainerOverviewDTO(support=support)

        containers: List[ContainerDTO] = []
        mounts: List[ContainerMountDTO] = []
        envs: List[ContainerEnvDTO] = []
        config = ContainerConfigDTO()
        host = ContainerHostDTO()

        try:
            containers = [self._to_container(c) for c in await self.client.get_containers()]
        except Exception as e:
            logger.warning(f"Could not list containers: {e}")

        try:
            # RouterOS groups both mounts and env rows by `list`, not `name`;
            # reading the wrong attribute left the UI showing empty names for
            # rows that exist and work.
            mounts = [
                ContainerMountDTO(id=m.get(".id", ""), name=m.get("list") or m.get("name"),
                                  src=m.get("src"), dst=m.get("dst"))
                for m in await self.client.get_container_mounts()
            ]
        except Exception as e:
            logger.debug(f"Could not list container mounts: {e}")

        try:
            envs = [
                ContainerEnvDTO(id=v.get(".id", ""), name=v.get("list") or v.get("name"),
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
                # Reported as `memory-high` by the device on current releases;
                # `ram-high` is the older name, accepted so the value survives an
                # upgrade either way.
                ram_high=raw_cfg.get("ram-high") or raw_cfg.get("memory-high"),
                layer_dir=raw_cfg.get("layer-dir"),
                memory_current_bytes=_as_count(raw_cfg.get("memory-current")),
                memory_high_bytes=_as_count(raw_cfg.get("memory-high")),
                memory_max_bytes=_as_count(raw_cfg.get("memory-max")),
            )
        except Exception as e:
            logger.debug(f"Could not read container config: {e}")

        # The router's own totals, so a container's 500 MB can be read as a share
        # of the 2 GB it is competing with routing for.
        try:
            resource = await self.client.get_system_resource()
            host = ContainerHostDTO(
                cpu_load_pct=resource.cpu_load,
                total_memory_bytes=resource.total_memory,
                free_memory_bytes=resource.free_memory,
                uptime=resource.uptime,
            )
        except Exception as e:
            logger.debug(f"Could not read router resource totals: {e}")

        try:
            storage = await read_storage(self.client)
        except Exception as e:
            logger.debug(f"Could not read storage inventory: {e}")
            storage = ContainerStorageDTO()

        return ContainerOverviewDTO(
            support=support, host=host, containers=containers, mounts=mounts,
            envs=envs, config=config, storage=storage,
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
            # `mounts=` is not a parameter of /container/add - the device answers
            # "unknown parameter mounts" - the binding is done by list name.
            ("mounts", "mountlists"),
            ("envlist", "envlists"),
            ("comment", "comment"),
        ):
            value = payload.get(src)
            if value:
                args[dst] = value
        return await self.client.add_container(args)
