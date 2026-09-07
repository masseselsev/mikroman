"""Prepare a router to host a container, from inside the app.

Why this exists as a service rather than a few client calls: provisioning is a
*sequence* - storage, then directories, then a network, then a mount, then the
container - and each step is only correct in the context of what the router
already has. The old ``scripts/setup_ros_container.rsc`` could not express that,
and got three things wrong as a result: it left ``layer-dir`` and ``tmpdir`` on
internal flash (a ~340 MB image onto a board with 473 MB free), it forwarded the
web port with no ``in-interface``, so the dashboard answered on every interface
including WAN, and it pointed the mount at a directory that need not exist.

So the service inspects first and produces a plan, and applying the plan walks
that same plan object - what the user was shown is what was executed, not a
description rewritten afterwards.
"""
import asyncio
import ipaddress
import logging
from typing import Any, Dict, Optional, Tuple

# Importing the rule rather than repeating it: where the database lives and where
# the key that decrypts it lives are decided by the app's own config and secrets
# module, and a migration that guessed either path would quietly copy the wrong
# thing.
from backend.app.core.config import settings
from backend.app.core.secrets import _data_dir as resolve_data_dir
from backend.app.schemas.container import (
    ContainerSetupPlanDTO,
    ContainerSetupRequest,
    ContainerSetupStepDTO,
)
from backend.app.services.routeros.provisioning import RouterOSCommandError

logger = logging.getLogger("mikroman.container_setup")

# Everything this service creates is tagged with this comment prefix. It is what
# lets a second run say "already done" instead of "already someone else's", and
# what keeps `guard_foreign_resources` protecting these objects from the app's
# own later cleanups.
OWNED = "mikroman:"


def _is_owned(comment: Optional[str]) -> bool:
    return str(comment or "").strip().startswith(OWNED)


def _first(iterable, predicate):
    return next((item for item in iterable if predicate(item)), None)


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
            mounts, envs, containers, files,
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
        )
        return {
            "config": config or {}, "bridges": bridges, "veths": veths, "ports": ports,
            "addresses": addresses, "srcnat": srcnat, "dstnat": dstnat, "mounts": mounts,
            "envs": envs, "containers": containers, "files": set(files or []),
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

        # 1. Where the layers and the writable root go. This is the step that
        #    decides whether the pull survives, so it is checked before anything
        #    else and refused outright when the storage is not there.
        if request.storage_dir.rstrip("/") not in {f.split("/")[0] for f in state["files"]}:
            block(
                "storage_dir",
                f"'{request.storage_dir}' is not a storage the router can see. "
                f"Check Winbox -> Files for the mount name (e.g. usb1-part1).",
            )
            return plan

        config = state["config"]
        layer_dir = f"{request.storage_dir.rstrip('/')}/container-layers"
        tmpdir = f"{request.storage_dir.rstrip('/')}/container-tmp"
        wanted = {"layer-dir": layer_dir, "tmpdir": tmpdir}
        if request.dns_servers:
            wanted["dns-servers"] = request.dns_servers

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
            await step("env", "skip", "none: the migrated database carries the router credentials and the bot token")

        # 10. The container itself. Created but not started - the migration has
        #     to land in the mount first, or the app boots against an empty
        #     database and writes a fresh one over the path we are about to use.
        if request.create_container:
            existing = _first(state["containers"], lambda k: (k.get("name") or k.get("tag") or "") == request.container_name
                              or request.image in str(k.get("tag") or ""))
            if existing is None:
                payload = {
                    "remote-image": request.image,
                    "interface": request.veth_name,
                    "mounts": request.mount_name,
                    "start-on-boot": "yes",
                    "logging": "yes",
                    "hostname": request.container_name,
                    "comment": f"{OWNED}container",
                }
                if wanted:
                    payload["envlist"] = f"{request.container_name}_envs"
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


# --- Carrying the existing installation's data over ----------------------------


class DataMigrationError(RuntimeError):
    """Why the data could not be carried over, phrased for the operator."""


def _database_path(database_url: str, data_dir):
    """The sqlite file this installation is using, from DATABASE_URL.

    Handles both shapes the setting takes in practice: an absolute path inside
    the container image (``sqlite+aiosqlite:////data/app.db``) and the relative
    one a source checkout uses (``sqlite+aiosqlite:///./data/app.db``).
    """
    from pathlib import Path

    raw = str(database_url or "")
    tail = raw.split(":///", 1)[-1] if ":///" in raw else raw.split("://", 1)[-1]
    tail = tail.split("?", 1)[0]
    candidate = Path(tail)
    if not candidate.is_absolute():
        candidate = data_dir / candidate.name
    return candidate


def _snapshot_local_db(data_dir, destination: str) -> int:
    """Copy the running database to ``data_dir/destination``; return its size.

    The SQLite backup API, not a file copy. The app writes samples every few
    seconds, so copying a live database file byte-for-byte catches it mid-
    transaction and the result arrives on the router reporting "database disk
    image is malformed". ``backup()`` yields a consistent snapshot from a
    concurrently-written source, and the output is one self-contained file with
    no -wal left behind to transfer.
    """
    import sqlite3

    source = _database_path(settings.DATABASE_URL, data_dir)
    if not source.exists():
        raise DataMigrationError(f"no database found at {source}")
    target = data_dir / destination
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return target.stat().st_size


async def migrate_data(
    client: Any,
    *,
    storage_dir: str,
    data_dir_name: str = "mikroman_data",
    container_name: str = "mikroman",
    tmp_name: str = "mikroman-migration.db",
) -> Dict[str, Any]:
    """Stage a consistent copy of this installation for the container to boot on.

    Not a transfer - a staging. RouterOS 7.24.2 has no upload endpoint over REST,
    and ``/file/add`` carries JSON text, so a hundred-megabyte binary cannot leave
    by the channel that configured the rest of it. The snapshot is taken here
    (SQLite's backup API, so it stays consistent while this instance keeps writing
    samples every few seconds) and reported with the two paths that must end up
    beside each other in the mount: ``app.db`` and ``.secret_key``.

    The key travels with the database because it has to: router credentials and
    the bot token are stored encrypted, and an install that gets ``app.db``
    without the matching ``.secret_key`` generates a new key on first boot and
    then cannot decrypt anything - which surfaces as a broken, empty deployment
    rather than as a missing file.

    Refuses while the target container is running: replacing a live application's
    database underneath it is the one action here that really can lose data.
    """
    containers = await client.get_containers()
    running = _first(
        containers or [],
        lambda c: (c.get("name") or "") == container_name and (c.get("status") or "") == "running",
    )
    if running is not None:
        raise DataMigrationError(
            f"container '{container_name}' is running - stop it first, or this would replace "
            f"the database underneath a live application"
        )

    data_dir = resolve_data_dir()
    key_path = data_dir / ".secret_key"
    size = await asyncio.to_thread(_snapshot_local_db, data_dir, tmp_name)
    staged = data_dir / tmp_name
    remote = f"{storage_dir.rstrip('/')}/{data_dir_name}"

    return {
        "database_bytes": size,
        "staged_path": str(staged),
        "secret_key_path": str(key_path) if key_path.exists() else "",
        "destination": f"{remote}/app.db",
        "secret_key": "included" if key_path.exists() else "absent",
        # Spelled out rather than half-automated: the operator has to see that the
        # last metre is theirs, and what for.
        "next_steps": [
            f"copy {staged} to the router as {remote}/app.db",
            (f"copy {key_path} to the router as {remote}/.secret_key" if key_path.exists()
             else "no .secret_key here; the container will create one"),
            "start the container, then stop this instance - only one of them may write",
        ],
    }
