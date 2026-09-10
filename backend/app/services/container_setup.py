"""Prepare a router to host a container, from inside the app.

Why this exists as a service rather than a few client calls: provisioning is a
*sequence* - storage, then directories, then a network, then a mount, then the
container - and each step is only correct in the context of what the router
already has. The old ``scripts/setup_ros_container.rsc`` could not express that,
and got three things wrong as a result: it left ``layer-dir`` and ``tmpdir`` on
internal flash (a ~340 MB image onto a board with a few hundred MB free), it forwarded the
web port with no ``in-interface``, so the dashboard answered on every interface
including WAN, and it pointed the mount at a directory that need not exist.

So the service inspects first and produces a plan, and applying the plan walks
that same plan object - what the user was shown is what was executed, not a
description rewritten afterwards.
"""
import asyncio
import ipaddress
import logging
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from backend.app.schemas.container import (
    SUPPORTED_FILE_SYSTEMS,
    ContainerDiskDTO,
    ContainerFormatRequest,
    ContainerSetupPlanDTO,
    ContainerSetupRequest,
    ContainerSetupStepDTO,
    ContainerStorageDTO,
)
from backend.app.services.routeros.provisioning import RouterOSCommandError
from backend.app.utils_format import format_bytes_human

logger = logging.getLogger("mikroman.container_setup")

# Everything this service creates is tagged with this comment prefix. It is what
# lets a second run say "already done" instead of "already someone else's", and
# what keeps `guard_foreign_resources` protecting these objects from the app's
# own later cleanups.
OWNED = "mikroman:"

# Measured on the hAP be3 Media: mikroman:latest unpacks to a few hundred MB on top of a
# ~340 MB download. Below this, a pull cannot finish and the router is left with
# half an image, so it is a hard block rather than advice.
MIN_FREE_BYTES = 400 * 1024 * 1024
# Above the floor but still tight: the layers grow with every release until the
# old ones are pruned, and the database lives on the same stick.
COMFORT_FREE_BYTES = 1 * 1024 * 1024 * 1024


def _is_owned(comment: Optional[str]) -> bool:
    return str(comment or "").strip().startswith(OWNED)


def _first(iterable, predicate):
    return next((item for item in iterable if predicate(item)), None)


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool:
    return str(value).strip().lower() in ("true", "yes", "1")


def _slot_of(path: Optional[str]) -> str:
    """The storage slot a RouterOS path starts with: ``/usb1-part1/x`` → ``usb1-part1``.

    Paths and slot names are used interchangeably in the UI and in ``/file``
    listings, and the device itself normalises ``usb1-part1`` to ``/usb1-part1``.
    Comparing anything else invites a plan that refuses a storage it is already
    using.
    """
    return str(path or "").strip().strip("/").split("/")[0]


def _format_bytes(value: Optional[int]) -> str:
    """Bytes for a message an operator reads, with an honest unknown.

    Delegates to the shared formatter rather than reimplementing the ladder - the
    only thing added here is that a disk which reports no free space must not be
    described as having zero.
    """
    if value is None:
        return "unknown size"
    return format_bytes_human(value)


def storage_slots_in_use(
    config: Dict[str, Any],
    mounts: Sequence[Dict[str, Any]],
) -> Set[str]:
    """Slots that hold container state right now, and must never be formatted.

    This is the data-loss guard for :func:`format_storage`. It covers the two
    places RouterOS keeps container bytes (``layer-dir``, ``tmpdir``) and every
    mount source - which on a running deployment includes the storage this very
    instance is booted from, so the app cannot saw off the branch it sits on.
    """
    slots: Set[str] = set()
    for key in ("layer-dir", "layer_dir", "tmpdir"):
        slot = _slot_of(config.get(key))
        if slot:
            slots.add(slot)
    for mount in mounts or []:
        slot = _slot_of(mount.get("src"))
        if slot:
            slots.add(slot)
    return slots


def _disk_dto(row: Dict[str, Any], *, in_use: Set[str]) -> ContainerDiskDTO:
    """One ``/disk`` row, plus this service's verdict on it.

    ``formatable`` is deliberately narrow. A device is offered for formatting
    only when it is not already holding container state, is not its parent's
    mounted sibling's only content, and is not busy. Everything else has to be
    reconfigured first, because formatting is the one operation here with no
    undo.
    """
    slot = str(row.get("slot") or row.get("name") or "")
    parent = str(row.get("parent") or "")
    size = _as_int(row.get("size"))
    free = _as_int(row.get("free"))
    fs = str(row.get("fs") or "").strip() or None
    mounted = _as_bool(row.get("mounted"))
    read_only = _as_bool(row.get("mount-read-only"))
    formatting = _as_bool(row.get("formatting"))
    is_partition = _as_bool(row.get("partition"))

    usable = bool(slot) and mounted and not read_only and not formatting and fs not in (None, "-")
    reasons: List[str] = []
    if not mounted:
        reasons.append("not mounted")
    if read_only:
        reasons.append("read-only")
    if formatting:
        reasons.append("formatting in progress")
    if fs in (None, "-"):
        reasons.append("no filesystem")

    # A whole device is only offered when none of its own partitions carry
    # container state: formatting the disk erases the table and every partition
    # under it, however healthy they look from here.
    conflicts = slot in in_use or (bool(parent) and parent in in_use)
    return ContainerDiskDTO(
        slot=slot,
        parent=parent or None,
        is_partition=is_partition,
        model=str(row.get("model") or "").strip() or None,
        serial=str(row.get("serial") or "").strip() or None,
        fs=fs,
        mount_point=str(row.get("mount-point") or "").strip() or None,
        mounted=mounted,
        read_only=read_only,
        formatting=formatting,
        disabled=_as_bool(row.get("disabled")),
        size_bytes=size,
        free_bytes=free,
        used_pct=_as_int(row.get("use")),
        temperature_c=_as_int(row.get("temperature")),
        io_errors=_as_int(row.get("io-errors")),
        usable_for_containers=usable,
        formatable=not conflicts and not formatting,
        note="; ".join(reasons) if reasons else (
            f"in container use ({slot}) - formatting is refused while it holds layers, "
            f"tmp or a mount" if conflicts else ""
        ),
    )


def assess_storage(
    disks: Sequence[Dict[str, Any]],
    storage_dir: str,
    *,
    in_use: Optional[Set[str]] = None,
) -> ContainerStorageDTO:
    """Judge one storage choice against what the router actually has mounted.

    The previous check was "does some path under this name appear in ``/file``".
    That passes for a disk mounted read-only, for an NTFS stick RouterOS cannot
    write, and for a 128 MB partition that cannot hold a 340 MB image - each of
    which fails later, during the pull, with a message from the registry rather
    than from here. ``/disk`` reports the real state, so the plan can refuse up
    front and say what to do about it.
    """
    in_use = set(in_use or ())
    dto = ContainerStorageDTO(
        storage_dir=storage_dir,
        required_bytes=MIN_FREE_BYTES,
        disks=[_disk_dto(row, in_use=in_use) for row in disks or []],
    )
    wanted = _slot_of(storage_dir)

    row = _first(disks or [], lambda d: _slot_of(d.get("slot") or d.get("name")) == wanted
                 or _slot_of(d.get("mount-point")) == wanted)
    if row is None:
        seen = ", ".join(sorted({str(d.get("slot") or d.get("name")) for d in disks or []} - {""})) or "none"
        dto.problems.append(
            f"'{wanted}' is not a storage this router sees. It reports: {seen}."
        )
        return dto

    slot = str(row.get("slot") or row.get("name") or wanted)
    fs = str(row.get("fs") or "").strip() or "-"
    size = _as_int(row.get("size"))
    free = _as_int(row.get("free"))
    dto.matched_slot = slot
    dto.fs = None if fs == "-" else fs
    dto.size_bytes = size
    dto.free_bytes = free

    if _as_bool(row.get("disabled")):
        dto.problems.append(f"{slot} is disabled on the router (/disk set {slot} disabled=no).")
    if not _as_bool(row.get("mounted")):
        dto.problems.append(
            f"{slot} is present but not mounted; reseat or mount it before continuing."
        )
    if _as_bool(row.get("mount-read-only")):
        dto.problems.append(
            f"{slot} is mounted read-only ({fs}); container layers and the database need writes."
        )
    if fs == "-":
        dto.problems.append(
            f"{slot} has no filesystem RouterOS can use - format it first (ext4 recommended)."
        )
    elif fs not in SUPPORTED_FILE_SYSTEMS:
        dto.warnings.append(
            f"{fs} is not one of the filesystems RouterOS formats ({', '.join(SUPPORTED_FILE_SYSTEMS)}); "
            f"it works, but expect weaker performance and no journal on a power loss."
        )
    if free is not None:
        if free < MIN_FREE_BYTES:
            dto.problems.append(
                f"{slot} has {_format_bytes(free)} free and a pull needs at least "
                f"{_format_bytes(MIN_FREE_BYTES)} (the image unpacks to ~340 MB)."
            )
        elif free < COMFORT_FREE_BYTES:
            dto.warnings.append(
                f"{_format_bytes(free)} free is enough for one pull, but layers accumulate across "
                f"releases until the old image is removed - plan on {_format_bytes(COMFORT_FREE_BYTES)}."
            )
    else:
        dto.warnings.append(f"{slot} does not report free space, so the plan cannot check it.")

    dto.ready = not dto.problems
    return dto



class ContainerSetupService:
    """Plan and apply the RouterOS configuration a container needs."""

    def __init__(self, client: Any) -> None:
        self.client = client

    async def plan(self, request: ContainerSetupRequest) -> ContainerSetupPlanDTO:
        """What would change. Touches nothing."""
        return await self._build(request, apply=False)

    async def apply(self, request: ContainerSetupRequest) -> ContainerSetupPlanDTO:
        """Make it so, stopping at the first command the router refuses.

        Returns the plan with each executed step marked, rather than raising:
        a half-applied setup is a real state the operator has to see and finish,
        and an exception would hide which steps already landed.
        """
        return await self._build(request, apply=True)

    # --- internals ----------------------------------------------------------

    async def _state(self) -> Dict[str, Any]:
        """One snapshot of everything the plan depends on.

        Fetched together because the alternative is a dozen sequential round
        trips to a router whose REST is the slowest thing in this loop.
        """
        c = self.client
        (
            config, bridges, veths, ports, addresses, srcnat, dstnat,
            mounts, envs, containers, files, disks,
        ) = await asyncio.gather(
            c.get_container_config(),
            c.list_bridge_interfaces(),
            c.list_veth_interfaces(),
            c.list_bridge_ports(),
            c.list_ip_addresses(),
            c.list_nat_rules("srcnat"),
            c.list_nat_rules("dstnat"),
            c.get_container_mounts(),
            c.get_container_envs(),
            c.get_containers(),
            c.list_file_names(),
            c.list_disks(),
        )
        return {
            "config": config or {}, "bridges": bridges, "veths": veths, "ports": ports,
            "addresses": addresses, "srcnat": srcnat, "dstnat": dstnat, "mounts": mounts,
            "envs": envs, "containers": containers, "files": set(files or []),
            "disks": disks,
        }

    def _subnet(self, request: ContainerSetupRequest) -> Tuple[ipaddress.IPv4Network, str, str]:
        """The container network plus the gateway and container addresses in it.

        Derived from one field rather than three, so a plan cannot be written
        with a gateway that is not in its own subnet.
        """
        network = ipaddress.ip_network(request.subnet, strict=True)
        hosts = network.hosts()
        return network, str(next(hosts)), str(next(hosts))

    async def _build(self, request: ContainerSetupRequest, apply: bool) -> ContainerSetupPlanDTO:
        try:
            network, gateway_ip, container_ip = self._subnet(request)
        except ValueError as exc:
            # strict=True rejects 172.17.0.5/24 style input; say so plainly.
            return ContainerSetupPlanDTO(
                ok=False, storage_dir=request.storage_dir,
                steps=[ContainerSetupStepDTO(key="subnet", action="blocked", detail=str(exc))],
                blockers=[f"Subnet: {exc}. Use a network address, e.g. 172.17.0.0/24."],
            )

        state = await self._state()
        plan = ContainerSetupPlanDTO(
            storage_dir=request.storage_dir, gateway_ip=gateway_ip, container_ip=container_ip
        )
        data_path = f"{request.storage_dir.rstrip('/')}/{request.data_dir_name}"

        async def step(key: str, action: str, detail: str = "", run=None) -> bool:
            """Record one step; when applying, run its command and mark the result.

            Returns False when the step failed and the walk must stop.
            """
            entry = ContainerSetupStepDTO(key=key, action=action, detail=detail)
            plan.steps.append(entry)
            if not apply or action not in ("create", "set") or run is None:
                return True
            try:
                await run()
                entry.applied = True
                entry.action = "done"
                return True
            except RouterOSCommandError as exc:
                entry.action = "failed"
                entry.detail = f"{detail} -> {exc}".strip(" ->")
                plan.blockers.append(f"{key}: {exc.detail}")
                plan.ok = False
                return False

        def block(key: str, detail: str) -> None:
            plan.blockers.append(f"{key}: {detail}")
            plan.ok = False
            plan.steps.append(ContainerSetupStepDTO(key=key, action="blocked", detail=detail))

        # 1. The storage everything else lands on. Judged from /disk, before any
        #    write is considered: a pull that fails halfway leaves a broken image
        #    on the device, and the router's own error for that arrives as a
        #    registry message with no hint that the answer was a mount flag.
        in_use = storage_slots_in_use(state["config"], state["mounts"])
        storage = assess_storage(state["disks"], request.storage_dir, in_use=in_use)
        plan.storage = storage
        if not storage.ready:
            block("storage_dir", " ".join(storage.problems))
            return plan
        await step(
            "storage_dir", "exists",
            f"{storage.matched_slot}: {storage.fs}, "
            f"{_format_bytes(storage.free_bytes)} free of {_format_bytes(storage.size_bytes)}"
            + (f"; {storage.warnings[0]}" if storage.warnings else ""),
        )

        config = state["config"]
        layer_dir = f"{request.storage_dir.rstrip('/')}/container-layers"
        tmpdir = f"{request.storage_dir.rstrip('/')}/container-tmp"
        wanted = {"layer-dir": layer_dir, "tmpdir": tmpdir}
        if request.dns_servers:
            wanted["dns-servers"] = request.dns_servers
        if request.ram_high:
            wanted["ram-high"] = request.ram_high

        # RouterOS applies one set atomically, and a single attribute this release
        # does not have - v7.24.2's /container/config has no dns-servers - makes
        # the whole request answer "unknown parameter" and costs the two values
        # that would have gone through. So send only what the device reported
        # back, which is also the only honest way to know: the attribute set has
        # moved between releases.
        supported = set(config.keys())
        unsupported = [k for k in wanted if k not in supported]

        # RouterOS normalises these paths with a leading slash ('usb1-part1/x'
        # reads back as '/usb1-part1/x'), so compare them without it - otherwise
        # a correctly configured router keeps offering the same no-op write.
        def differs(key: str, value: str) -> bool:
            current = str(config.get(key) or config.get(key.replace("-", "_")) or "")
            return current.lstrip("/").rstrip("/") != value.lstrip("/").rstrip("/")

        fields = {k: v for k, v in wanted.items() if k in supported and differs(k, v)}
        detail = (f"container config -> {', '.join(f'{k}={v}' for k, v in fields.items())}"
                  if fields else "")
        if unsupported:
            detail = (detail + "; " if detail else "") + (
                f"not setting {', '.join(unsupported)}: this RouterOS has no such attribute "
                "(containers resolve through the router's own DNS)")
        if fields:
            if not await step("config", "set", detail,
                              run=lambda: self.client.set_container_config(fields)):
                return plan
        elif unsupported:
            await step("config", "skip", detail)
        else:
            await step("config", "exists", "layer-dir and tmpdir already point at the storage")

        # 2. The directory the mount binds. RouterOS has no mkdir over REST and no
        #    upload endpoint at all on this release, so the directory comes into
        #    existence as the parent of a file written through /file/add. A
        #    dotted name is refused outright ("invalid file name"), hence keep.txt.
        if data_path in state["files"] or any(f.startswith(data_path + "/") for f in state["files"]):
            await step("data_dir", "exists", data_path)
        elif not await step("data_dir", "create", f"write {data_path}/keep.txt to create the directory",
                            run=lambda: self.client.add_file(f"{data_path}/keep.txt", "mikroman")):
            plan.blockers.append(
                "data_dir: if the router refuses the path, create the folder once in Winbox -> Files "
                "and re-run; MikroMan will not delete anything it did not create."
            )
            return plan

        # 3. Bridge, and the address the containers route through.
        bridge = _first(state["bridges"], lambda b: b.get("name") == request.bridge_name)
        if bridge is None:
            if not await step("bridge", "create", f"bridge {request.bridge_name}",
                              run=lambda: self.client.add_bridge(request.bridge_name, f"{OWNED}containers")):
                return plan
        elif _is_owned(bridge.get("comment")) or bridge.get("comment", "") == "":
            await step("bridge", "exists", request.bridge_name)
        else:
            block("bridge", f"'{request.bridge_name}' exists and is not managed by MikroMan; "
                            f"choose another bridge name rather than touching it.")
            return plan

        # 4. The container's own end of the pair.
        veth = _first(state["veths"], lambda v: v.get("name") == request.veth_name)
        if veth is None:
            if not await step("veth", "create", f"veth {request.veth_name} = {container_ip}/{network.prefixlen}",
                              run=lambda: self.client.add_veth(request.veth_name, f"{container_ip}/{network.prefixlen}", gateway_ip)):
                return plan
        else:
            await step("veth", "exists", request.veth_name)

        port = _first(state["ports"], lambda p: p.get("interface") == request.veth_name)
        if port is None:
            if not await step("bridge_port", "create", f"{request.veth_name} -> {request.bridge_name}",
                              run=lambda: self.client.add_bridge_port(request.bridge_name, request.veth_name, f"{OWNED}container link")):
                return plan
        else:
            await step("bridge_port", "exists", f"{request.veth_name} is on {port.get('bridge')}")

        # 5. The gateway address, with the one check that cannot be deferred: a
        #    subnet the router already uses elsewhere would take the LAN down a
        #    step later, when the container cannot be reached at all.
        clash = _first(
            state["addresses"],
            lambda a: _overlaps(a.get("address"), network) and a.get("interface") != request.bridge_name,
        )
        if clash is not None:
            block(
                "address",
                f"{clash.get('address')} on '{clash.get('interface')}' overlaps {request.subnet}; "
                f"pick another container subnet.",
            )
            return plan

        ours = _first(state["addresses"], lambda a: a.get("interface") == request.bridge_name)
        if ours is None:
            if not await step("address", "create", f"{gateway_ip}/{network.prefixlen} on {request.bridge_name}",
                              run=lambda: self.client.add_ip_address(f"{gateway_ip}/{network.prefixlen}", request.bridge_name, f"{OWNED}container gateway")):
                return plan
        else:
            await step("address", "exists", f"{ours.get('address')} on {request.bridge_name}")

        # 6. Outbound for the bot and the image pull.
        masq = _first(state["srcnat"], lambda r: r.get("action") == "masquerade" and str(request.subnet) in str(r.get("src-address") or ""))
        if masq is None:
            if not await step("nat_masquerade", "create", f"srcnat {request.subnet} -> masquerade",
                              run=lambda: self.client.add_nat_rule({
                                  "chain": "srcnat", "src-address": str(network), "action": "masquerade",
                                  "comment": f"{OWNED}container outbound"})):
                return plan
        else:
            await step("nat_masquerade", "exists", f"rule *{masq.get('.id', '')}")

        # 7. The web port. Deliberately not created without an interface to bind
        #    it to: an unbounded dstnat publishes an administrative UI on WAN,
        #    which is what the shell script did.
        if request.expose_on_interface:
            fwd = _first(state["dstnat"], lambda r: str(request.web_port) in str(r.get("dst-port") or ""))
            if fwd is None:
                if not await step("nat_web", "create",
                                  f"dstnat {request.expose_on_interface}:{request.web_port} -> {container_ip}:{request.web_port}",
                                  run=lambda: self.client.add_nat_rule({
                                      "chain": "dstnat", "in-interface": request.expose_on_interface,
                                      "dst-port": str(request.web_port), "protocol": "6",
                                      "action": "dst-nat", "to-addresses": container_ip, "to-ports": str(request.web_port),
                                      "comment": f"{OWNED}web ui, {request.expose_on_interface} only"})):
                    return plan
            elif not _is_owned(fwd.get("comment")):
                block("nat_web", f"a foreign rule already forwards :{request.web_port}; not touching it.")
                return plan
            else:
                await step("nat_web", "exists", f"port {request.web_port} already forwarded")
        else:
            await step("nat_web", "skip",
                       "no port forward: the UI stays reachable from the router itself. "
                       "Set 'expose_on_interface' (e.g. br.lan) to publish it on the LAN - never without one.")

        # 8. The data mount.
        mount = _first(
            state["mounts"],
            # RouterOS groups mounts by `list`; older readings of the same field
            # surfaced as `name`, so accept either rather than creating a twin.
            lambda m: (m.get("list") or m.get("name")) == request.mount_name,
        )
        if mount is None:
            if not await step("mount", "create", f"{request.mount_name}: {data_path} -> /data",
                              run=lambda: self.client.add_container_mount(request.mount_name, data_path, "/data")):
                return plan
        else:
            await step("mount", "exists", f"{mount.get('src')} -> {mount.get('dst')}")

        # 9. Environment. Empty by design: credentials and the bot token travel
        #    inside the database, and copying them into /container/envs would put
        #    them in plaintext in the running config and in every exported .rsc.
        wanted = {k: v for k, v in (request.extra_env or {}).items() if str(v) != ""}
        env_list = f"{request.container_name}_envs"
        have = {
            e.get("key") for e in state["envs"]
            if (e.get("list") or e.get("name")) == env_list
        }
        for key in sorted(set(wanted) - have):
            if not await step(f"env.{key}", "create", f"{key} (value not shown)",
                              run=lambda key=key: self.client.add_container_env(
                                  env_list, key, str(wanted[key]))):
                return plan
        if not wanted:
            await step("env", "skip",
                       "none: router credentials and the bot token live in the encrypted database, and "
                       "/container/envs would put them in plaintext in the running config and every .rsc")

        # 10. The container itself. Created but deliberately not started: the
        #     operator should see the row, its mount and its environment in the
        #     page before the app boots on the router, because starting is the
        #     point where a wrong mount path becomes a second, empty installation
        #     writing over the first one.
        if request.create_container:
            # RouterOS names the row after the image (`mikroman:latest`), not
            # after anything the operator chose, so matching on the requested
            # name never finds the container this step made and a second run adds
            # a twin. The comment is ours and is the only stable identifier.
            existing = _first(
                state["containers"],
                lambda k: str(k.get("comment") or "").startswith(f"{OWNED}container"),
            )
            if existing is None:
                payload = {
                    "remote-image": request.image,
                    "interface": request.veth_name,
                    # `mounts=` is refused by the device; the binding is by list
                    # name under `mountlists`.
                    "mountlists": request.mount_name,
                    "start-on-boot": "yes",
                    "logging": "yes",
                    "hostname": request.container_name,
                    "comment": f"{OWNED}container",
                }
                if wanted:
                    payload["envlists"] = env_list
                if not await step("container", "create", f"{request.image} (created, not started)",
                                  run=lambda: self.client.add_container(payload)):
                    return plan
            else:
                await step("container", "exists", f"{request.container_name} ({existing.get('status')})")
        else:
            await step("container", "skip", "create_container=false")

        return plan


def _overlaps(address: Optional[str], network: ipaddress.IPv4Network) -> bool:
    """Does an existing 'a.b.c.d/len' address sit inside `network`?"""
    if not address:
        return False
    try:
        interface = ipaddress.ip_interface(str(address).strip())
        return interface.network.network_address.version == 4 and interface.ip in network
    except ValueError:
        return False


# --- Storage: reading it, and preparing it ------------------------------------


async def read_storage(client: Any, storage_dir: Optional[str] = None) -> ContainerStorageDTO:
    """What the router has, and whether ``storage_dir`` is one of them.

    Separate from the plan because the page needs the inventory *before* the
    operator chooses: the choice should be a pick from what exists, not a text
    box they guess at. ``storage_dir`` is optional for the same reason - with
    nothing chosen yet, the caller still wants the disk list.
    """
    config, mounts, disks = await asyncio.gather(
        client.get_container_config(),
        client.get_container_mounts(),
        client.list_disks(),
    )
    in_use = storage_slots_in_use(config or {}, mounts or [])
    if not storage_dir:
        # No choice made yet: report the inventory, and mark the verdict as not
        # ready rather than inventing a directory to judge.
        return ContainerStorageDTO(
            ready=False,
            storage_dir="",
            problems=["no storage chosen yet"],
            disks=[_disk_dto(row, in_use=in_use) for row in disks or []],
            required_bytes=MIN_FREE_BYTES,
        )
    return assess_storage(disks or [], storage_dir, in_use=in_use)


class StorageFormatError(RuntimeError):
    """Why a format request was refused, phrased for the operator."""


async def format_storage(client: Any, request: ContainerFormatRequest) -> Dict[str, Any]:
    """Format one disk or partition, if and only if the guards all pass.

    This function exists to be uncallable by accident. ``/disk format`` is a
    single REST call that erases a device, and the router will happily accept it
    for the wrong slot, with no confirmation prompt of its own and nothing to
    restore afterwards. So four things are checked here, in this order, before
    the command is sent:

    1. the operator typed the slot name into ``confirm`` - the body cannot be
       assembled from a dropdown alone;
    2. the target is one of the slots ``/disk`` actually reports, so a typo
       cannot address a device nobody listed;
    3. the filesystem is one RouterOS formats (an arbitrary string would be
       refused by the device with a worse message than this one);
    4. the slot - or the device it is a partition of - holds no container state:
       not ``layer-dir``, not ``tmpdir``, not the source of any mount. That set
       includes the storage a running MikroMan booted its own database from, so
       the app cannot wipe the ground it stands on.
    """
    if request.confirm.strip() != request.slot.strip():
        raise StorageFormatError(
            "confirmation does not match the slot - type the device name exactly to format it"
        )
    if request.file_system not in SUPPORTED_FILE_SYSTEMS:
        raise StorageFormatError(
            f"unknown filesystem '{request.file_system}'; RouterOS formats: "
            f"{', '.join(SUPPORTED_FILE_SYSTEMS)}"
        )

    config, mounts, disks = await asyncio.gather(
        client.get_container_config(),
        client.get_container_mounts(),
        client.list_disks(),
    )
    slot = request.slot.strip()
    row = _first(disks or [], lambda d: str(d.get("slot") or d.get("name") or "").strip() == slot)
    if row is None:
        seen = ", ".join(sorted({str(d.get("slot") or d.get("name") or "") for d in disks or []} - {""})) or "none"
        raise StorageFormatError(f"the router reports no storage '{slot}'. It has: {seen}.")
    if _as_bool(row.get("formatting")):
        raise StorageFormatError(f"{slot} is already being formatted; wait for it to finish.")

    in_use = storage_slots_in_use(config or {}, mounts or [])
    parent = str(row.get("parent") or "").strip()
    children = {
        str(d.get("slot") or d.get("name") or "").strip()
        for d in disks or [] if str(d.get("parent") or "").strip() == slot
    }
    touched = {slot} | ({parent} if parent else set()) | children
    conflicts = sorted(touched & in_use)
    if conflicts:
        raise StorageFormatError(
            f"{', '.join(conflicts)} holds container storage (layer-dir, tmpdir or a mount"
            + (" source" if children & in_use else "")
            + f"); repoint that elsewhere before formatting {slot}."
        )

    await client.format_disk(
        slot,
        file_system=request.file_system,
        label=request.label.strip(),
        mbr_partition_table=request.mbr_partition_table,
    )
    logger.warning(
        f"Started formatting {slot} as {request.file_system}"
        + (f" (label {request.label})" if request.label else "")
        + "; the router reports it under /disk until it finishes"
    )

    # Read the row back so the caller sees the device's own answer rather than an
    # assumption about how far it got in the two seconds since the command.
    after = await client.list_disks()
    now = _first(after or [], lambda d: str(d.get("slot") or "") == slot) or {}
    return {
        "started": True,
        "slot": slot,
        "file_system": request.file_system,
        "label": request.label.strip(),
        "formatting": _as_bool(now.get("formatting")),
        "detail": (
            f"{slot} -> {request.file_system}. Formatting runs in the background; "
            f"refresh the page - the mount and its free space reappear when it is done."
        ),
    }
