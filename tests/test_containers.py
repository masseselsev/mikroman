"""RouterOS container management.

The container package is optional and not on a stock install, so the behaviour
that matters most here is graceful degradation: the overview still comes back
when the feature is absent, with a ``support`` block that explains why, and the
action endpoints refuse cleanly rather than throwing.

The second half covers the provisioning service - preparing a router to host a
container - where the behaviour that matters is the opposite: a refused command
must stop the run and be reported, never swallowed.
"""
import json

import pytest
import respx

from backend.app.core.config import Settings
from backend.app.services.container_manager import ContainerManager, _as_bool


class FakeClient:
    """Stands in for RouterOSClient with canned container-API responses."""

    def __init__(self, *, packages=None, containers=None, mounts=None, envs=None, config=None,
                 disks=None, resource=None):
        self._packages = packages if packages is not None else []
        self._containers = containers or []
        self._mounts = mounts or []
        self._envs = envs or []
        self._config = config or {}
        self._disks = disks or []
        self._resource = resource
        self.commands = []

    async def get_packages(self):
        return self._packages

    async def get_containers(self):
        return self._containers

    async def get_container_mounts(self):
        return self._mounts

    async def get_container_envs(self):
        return self._envs

    async def get_container_config(self):
        return self._config

    async def list_disks(self):
        """Storage inventory, read by the overview so the page can judge mounts."""
        return self._disks

    async def get_system_resource(self):
        if self._resource is None:
            raise ConnectionError("no canned resource for this test")
        return self._resource

    async def container_command(self, action, container_id):
        self.commands.append((action, container_id))
        return True

    async def add_container(self, payload):
        self.commands.append(("add", payload))
        return {".id": "*9", **payload}


def test_as_bool_reads_routeros_string_booleans():
    assert _as_bool("true") is True
    assert _as_bool("yes") is True
    assert _as_bool("false") is False
    assert _as_bool("no") is False
    assert _as_bool("") is None
    assert _as_bool(None) is None


@pytest.mark.asyncio
async def test_overview_reports_not_installed_without_erroring():
    mgr = ContainerManager(FakeClient(packages=[{"name": "security", "version": "7.25"}]))
    overview = await mgr.get_overview()
    assert overview.support.installed is False
    assert overview.support.status == "not_installed"
    assert overview.containers == []
    assert "extra-packages" in overview.support.message


@pytest.mark.asyncio
async def test_overview_reports_disabled_package():
    mgr = ContainerManager(FakeClient(
        packages=[{"name": "container", "version": "7.25", "disabled": "true"}],
    ))
    overview = await mgr.get_overview()
    assert overview.support.installed is True
    assert overview.support.enabled is False
    assert overview.support.status == "disabled"


@pytest.mark.asyncio
async def test_overview_maps_containers_when_ready():
    mgr = ContainerManager(FakeClient(
        packages=[{"name": "container", "version": "7.25", "disabled": "false"}],
        containers=[{
            ".id": "*1", "name": "adguard", "tag": "adguard/adguardhome:latest",
            "status": "running", "arch": "arm64", "interface": "veth1",
            "root-dir": "usb1/adguard", "start-on-boot": "yes", "logging": "yes",
        }],
        config={"registry-url": "https://registry-1.docker.io", "tmpdir": "usb1/tmp"},
    ))
    overview = await mgr.get_overview()
    assert overview.support.status == "ready"
    assert len(overview.containers) == 1
    c = overview.containers[0]
    assert c.id == "*1"
    assert c.name == "adguard"
    assert c.status == "running"
    assert c.root_dir == "usb1/adguard"
    assert c.start_on_boot is True
    assert overview.config.registry_url == "https://registry-1.docker.io"


@pytest.mark.asyncio
async def test_overview_carries_the_resources_each_container_is_using():
    """The page has to show what a container costs, not just that it exists.

    Everything here already arrives on the ``/container`` row - ``cpu-usage``,
    ``memory-current``, ``container-size`` - and was being dropped on the way to
    the DTO, which is why the deployed router showed a container and no answer to
    "is this much normal?".

    Note ``memory-high: "unlimited"``: the device spells "no ceiling" as text, and
    reading that as 0 would render a full bar for a container with no limit set.
    """
    from backend.app.schemas.routeros import RouterSystemResource

    mgr = ContainerManager(FakeClient(
        packages=[{"name": "container", "version": "7.24.2", "disabled": "false"}],
        containers=[{
            ".id": "*1", "name": "mikroman:latest", "running": "true", "cpu-usage": "17.6",
            "memory-current": "519647232", "memory-high": "unlimited", "memory-max": "unlimited",
            "container-size": "262440508", "restart-count": "0", "stop-time": "10s",
            "logging": "true", "mountlists": "mikroman_data", "comment": "mikroman:container",
        }],
        config={"layer-dir": "/usb1-part1/container-layers", "memory-current": "519647232",
                "memory-high": "unlimited"},
        disks=[{"slot": "usb1-part1", "type": "partition", "fs": "ext4", "mounted": "true",
                "mount-point": "usb1-part1", "parent": "usb1", "partition": "true",
                "size": "500105740288", "free": "280677068800", "use": "43"}],
        resource=RouterSystemResource(
            cpu_load=19, total_memory=2147483648, free_memory=885293056, uptime="1d6h15m8s"
        ),
    ))
    overview = await mgr.get_overview()
    container = overview.containers[0]
    assert container.running is True
    assert container.cpu_usage_pct == 17.6
    assert container.memory_current_bytes == 519647232
    assert container.memory_high_bytes is None, "'unlimited' is not a zero-byte ceiling"
    assert container.disk_size_bytes == 262440508
    assert container.stop_time_seconds == 10
    assert container.restart_count == 0
    assert container.mounts == "mikroman_data"

    # The device totals, so 519 MB reads as a share of what the board has.
    assert overview.host.cpu_load_pct == 19
    assert overview.host.total_memory_bytes == 2147483648

    # Storage comes along too: the page judges the mount without a second call.
    assert [d.slot for d in overview.storage.disks] == ["usb1-part1"]
    assert overview.storage.disks[0].usable_for_containers is True


@pytest.mark.asyncio
async def test_a_router_that_reports_no_resource_still_returns_the_overview():
    """One unreadable menu must not take the whole page down."""
    mgr = ContainerManager(FakeClient(
        packages=[{"name": "container", "version": "7.24.2", "disabled": "false"}],
        containers=[{".id": "*1", "name": "mikroman:latest", "running": "true"}],
    ))
    overview = await mgr.get_overview()
    assert len(overview.containers) == 1
    assert overview.host.cpu_load_pct is None
    assert overview.storage.disks == []


@pytest.mark.asyncio
async def test_unreachable_router_is_its_own_support_state():
    class Boom(FakeClient):
        async def get_packages(self):
            raise ConnectionError("down")

    overview = await ContainerManager(Boom()).get_overview()
    assert overview.support.status == "unreachable"
    assert overview.containers == []


@pytest.mark.asyncio
async def test_create_translates_fields_to_routeros_argument_names():
    fake = FakeClient(packages=[{"name": "container", "disabled": "false"}])
    mgr = ContainerManager(fake)
    await mgr.create({
        "remote_image": "library/nginx:alpine",
        "interface": "veth2",
        "root_dir": "usb1/nginx",
        "start_on_boot": True,
        "logging": True,
        "hostname": None,
        "cmd": None,
        "entrypoint": None,
        "mounts": "webroot",
        "envlist": None,
        "comment": "test",
    })
    action, args = fake.commands[-1]
    assert action == "add"
    assert args["remote-image"] == "library/nginx:alpine"
    assert args["interface"] == "veth2"
    assert args["root-dir"] == "usb1/nginx"
    assert args["start-on-boot"] == "yes"
    assert args["mountlists"] == "webroot"  # RouterOS binds by list, not "mounts"
    assert "hostname" not in args  # None fields are dropped
    assert "envlist" not in args


@pytest.mark.asyncio
async def test_run_action_rejects_an_unknown_verb():
    mgr = ContainerManager(FakeClient(packages=[{"name": "container", "disabled": "false"}]))
    with pytest.raises(ValueError):
        await mgr.run_action("restart", "*1")


@pytest.mark.asyncio
async def test_mount_and_env_names_are_read_from_the_list_attribute():
    """RouterOS calls the grouping field `list`; the UI shows that as the name.

    Reading `name` instead left real, working mounts and env rows displayed
    nameless, which made the setup panel look like it had created nothing.
    """
    fake = FakeClient(
        packages=[{"name": "container", "disabled": "false"}],
        mounts=[{".id": "*1", "list": "mikroman_data", "src": "/usb1-part1/mikroman_data", "dst": "/data"}],
        envs=[{".id": "*1", "list": "mikroman_envs", "key": "PORT", "value": "1928"}],
    )
    overview = await ContainerManager(fake).get_overview()
    assert overview.mounts[0].name == "mikroman_data"
    assert overview.mounts[0].dst == "/data"
    assert overview.envs[0].name == "mikroman_envs"


def test_container_routes_are_registered():
    from backend.app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/routers/{router_id}/containers" in paths
    assert "/api/v1/routers/{router_id}/containers/{container_id}/{action}" in paths
    # The setup pair has to be registered too - and reachable, which the next
    # test checks, because route order decides that and OpenAPI does not.
    assert "/api/v1/routers/{router_id}/containers/setup/plan" in paths
    assert "/api/v1/routers/{router_id}/containers/setup/apply" in paths
    # Storage is read and prepared from the page, so the picker never offers a
    # device that cannot take a pull.
    assert "/api/v1/routers/{router_id}/containers/storage" in paths
    assert "/api/v1/routers/{router_id}/containers/storage/format" in paths
    # Carrying an installation across was a one-time cutover helper and is not
    # part of the product: it is deliberately not registered.
    assert "/api/v1/routers/{router_id}/containers/migrate-data" not in paths


# --- Provisioning a router to host a container --------------------------------


class SetupFake:
    """A router that reports whatever state a test needs, and records commands."""

    def __init__(self, **state):
        self.files = state.get("files", ["usb1-part1", "usb1-part1/shared"])
        # Mirrors what a real hAP be3 Media reports: one hardware row for the
        # device with no filesystem of its own, one mounted ext4 partition. The
        # plan judges storage from /disk, not from these path names, so a test
        # that wants "no such storage" has to take the row away, not the file.
        self.disks = state.get("disks", [
            {".id": "*1", "slot": "usb1", "type": "hardware", "fs": "-", "mounted": "false",
             "parent": "", "partition": "false", "size": "500107862016",
             "model": "DM  HD001", "mount-read-only": "false", "formatting": "false"},
            {".id": "*2", "slot": "usb1-part1", "type": "partition", "fs": "ext4",
             "mounted": "true", "mount-point": "usb1-part1", "parent": "usb1",
             "partition": "true", "size": "500105740288", "free": "280677068800",
             "use": "43", "mount-read-only": "false", "formatting": "false"},
        ])
        self.addresses = state.get("addresses", [
            {"address": "192.168.88.1/24", "interface": "br.lan"},
            {"address": "10.75.16.78/30", "interface": "ether1"},
        ])
        self.bridges = state.get("bridges", [{"name": "br.lan", "comment": ""}])
        self.veths = state.get("veths", [])
        self.ports = state.get("ports", [])
        self.srcnat = state.get("srcnat", [])
        self.dstnat = state.get("dstnat", [])
        self.mounts = state.get("mounts", [])
        self.envs = state.get("envs", [])
        self.containers = state.get("containers", [])
        self.config = state.get("config", {"tmpdir": "", "layer-dir": ""})
        self.refuse = state.get("refuse")  # command key that raises, e.g. "veth"
        self.commands = []

    # reads
    async def get_container_config(self):
        return self.config

    async def get_packages(self):
        return [{"name": "container", "version": "7.24.2", "disabled": "false"}]

    async def list_bridge_interfaces(self):
        return self.bridges

    async def list_veth_interfaces(self):
        return self.veths

    async def list_bridge_ports(self):
        return self.ports

    async def list_ip_addresses(self):
        return self.addresses

    async def list_nat_rules(self, chain=None):
        return self.srcnat if chain == "srcnat" else self.dstnat

    async def get_container_mounts(self):
        return self.mounts

    async def get_container_envs(self):
        return self.envs

    async def get_containers(self):
        return self.containers

    async def list_file_names(self):
        return self.files

    async def list_disks(self):
        return self.disks

    # writes
    async def format_disk(self, slot, file_system="ext4", label="", mbr_partition_table=False):
        self._check("format")
        self.commands.append(
            ("format", slot, file_system, label, "yes" if mbr_partition_table else "no")
        )

    async def set_container_config(self, fields):
        self.commands.append(("config", dict(fields)))

    async def add_file(self, name, contents=""):
        self._check("file")
        self.commands.append(("file", name, contents))

    async def add_bridge(self, name, comment):
        self._check("bridge")
        self.commands.append(("bridge", name, comment))

    async def add_veth(self, name, address, gateway):
        self._check("veth")
        self.commands.append(("veth", name, address, gateway))

    async def add_bridge_port(self, bridge, interface, comment):
        self._check("port")
        self.commands.append(("port", bridge, interface, comment))

    async def add_ip_address(self, address, interface, comment):
        self._check("address")
        self.commands.append(("address", address, interface, comment))

    async def add_nat_rule(self, args):
        self._check("nat")
        self.commands.append(("nat", dict(args)))

    async def add_container_mount(self, name, src, dst):
        self._check("mount")
        self.commands.append(("mount", name, src, dst))

    async def add_container_env(self, name, key, value):
        self.commands.append(("env", name, key, value))

    async def add_container(self, payload):
        self._check("container")
        self.commands.append(("container", dict(payload)))

    def _check(self, key):
        if self.refuse == key:
            from backend.app.services.routeros.provisioning import RouterOSCommandError
            raise RouterOSCommandError(f"/{key}/add", 400, f"no such command for {key}")


def _ready_state():
    """A router where the whole setup has already been applied once."""
    return dict(
        files=["usb1-part1", "usb1-part1/mikroman_data/keep.txt"],
        bridges=[{"name": "br.lan", "comment": ""}, {"name": "bridge-containers", "comment": "mikroman:containers"}],
        veths=[{"name": "veth-mikroman"}],
        ports=[{"bridge": "bridge-containers", "interface": "veth-mikroman"}],
        addresses=[{"address": "172.17.0.1/24", "interface": "bridge-containers"}],
        srcnat=[{"action": "masquerade", "src-address": "172.17.0.0/24"}],
        dstnat=[{"dst-port": "1928", "in-interface": "br.lan", "comment": "mikroman:web ui, br.lan only"}],
        mounts=[{"name": "mikroman_data", "src": "usb1-part1/mikroman_data", "dst": "/data"}],
        containers=[{"name": "mikroman:latest", "status": "stopped",
                     "tag": "ghcr.io/masseselsev/mikroman:latest",
                     "comment": "mikroman:container", "mountlists": "mikroman_data"}],
        config={"tmpdir": "usb1-part1/container-tmp", "layer-dir": "usb1-part1/container-layers",
                "dns-servers": "172.17.0.1"},
    )


@pytest.fixture
def request_():
    from backend.app.schemas.container import ContainerSetupRequest
    return ContainerSetupRequest(storage_dir="usb1-part1", expose_on_interface="br.lan")


@pytest.mark.asyncio
async def test_plan_is_a_dry_run_that_names_every_step(request_):
    from backend.app.services.container_setup import ContainerSetupService

    fake = SetupFake()
    plan = await ContainerSetupService(fake).plan(request_)
    assert plan.ok is True and plan.blockers == []
    assert [s.key for s in plan.steps] == [
        "storage_dir", "config", "data_dir", "bridge", "veth", "bridge_port", "address",
        "nat_masquerade", "nat_web", "mount", "env", "container",
    ]
    assert plan.gateway_ip == "172.17.0.1" and plan.container_ip == "172.17.0.2"
    # The storage verdict travels with the plan, so the UI shows the mount state
    # it judged rather than a step that says only "ok".
    assert plan.storage.ready is True and plan.storage.matched_slot == "usb1-part1"
    assert plan.storage.free_bytes == 280677068800
    # A plan touches nothing: it has to be safe to show before it is obeyed.
    assert fake.commands == []


@pytest.mark.asyncio
async def test_apply_runs_exactly_the_commands_the_plan_promised(request_):
    from backend.app.services.container_setup import ContainerSetupService

    fake = SetupFake()
    plan = await ContainerSetupService(fake).apply(request_)
    assert plan.ok is True
    kinds = [c[0] for c in fake.commands]
    assert kinds == ["config", "file", "bridge", "veth", "port", "address", "nat", "nat", "mount", "container"]
    assert all(s.action in ("done", "skip", "exists") for s in plan.steps), plan.steps
    # Storage is what the whole exercise turns on: layers must never land on flash.
    config_call = fake.commands[0][1]
    assert config_call["layer-dir"].startswith("usb1-part1")
    assert config_call["tmpdir"].startswith("usb1-part1")


@pytest.mark.asyncio
async def test_the_container_is_bound_by_mountlists_not_mounts(request_):
    """`mounts=` is not a parameter of /container/add; `mountlists=` is.

    The device refused the whole add with "unknown parameter mounts", which also
    means a run that got that far must not create a second container on a retry:
    RouterOS names the row after the image, so the mikroman: comment is the only
    handle that identifies what this step made.
    """
    from backend.app.services.container_setup import ContainerSetupService

    fake = SetupFake()
    await ContainerSetupService(fake).apply(request_)
    added = next(c for c in fake.commands if c[0] == "container")[1]
    assert added["mountlists"] == "mikroman_data"
    assert "mounts" not in added
    assert added["comment"].startswith("mikroman:")

    # A second run over the same router must not add a twin container.
    done = SetupFake(**_ready_state())
    plan2 = await ContainerSetupService(done).apply(request_)
    assert not [c for c in done.commands if c[0] == "container"]
    assert next(s for s in plan2.steps if s.key == "container").action == "exists"


@pytest.mark.asyncio
async def test_create_maps_the_ui_fields_onto_the_routeros_names():
    from backend.app.services.container_manager import ContainerManager

    fake = FakeClient(packages=[{"name": "container", "disabled": "false"}])
    await ContainerManager(fake).create({
        "remote_image": "ghcr.io/masseselsev/mikroman:latest",
        "interface": "veth-mikroman",
        "mounts": "mikroman_data",
        "envlist": "mikroman_envs",
        "comment": "mikroman:container",
    })
    args = fake.commands[-1][1]
    assert args["mountlists"] == "mikroman_data"
    assert args["envlists"] == "mikroman_envs"
    assert "mounts" not in args and "envlist" not in args


def test_a_container_row_reports_its_mount_list():
    from backend.app.services.container_manager import ContainerManager

    row = {".id": "*1", "name": "mikroman:latest", "mountlists": "mikroman_data",
           "envlists": "", "comment": "mikroman:container"}
    dto = ContainerManager._to_container(row)
    assert dto.mounts == "mikroman_data"


@pytest.mark.asyncio
async def test_an_attribute_this_release_lacks_is_skipped_not_fatal(request_):
    """One unknown parameter used to veto the whole set - and it did, live.

    v7.24.2's /container/config has no dns-servers; sending it alongside the two
    values that were accepted made RouterOS refuse all three with
    "unknown parameter". The plan must degrade to setting what exists.
    """
    from backend.app.services.container_setup import ContainerSetupService

    fake = SetupFake(config={"tmpdir": "", "assumed-registry-url": "docker.io"})  # no layer-dir at all
    plan = await ContainerSetupService(fake).apply(request_)
    sent = fake.commands[0][1]
    assert sent == {"tmpdir": "usb1-part1/container-tmp"}, sent
    assert "layer-dir" not in str(plan.blockers)
    assert plan.ok is True


@pytest.mark.asyncio
async def test_a_router_that_normalises_paths_with_a_leading_slash_is_already_done(request_):
    """'usb1-part1/x' stored as '/usb1-part1/x' is the same setting, not a diff."""
    from backend.app.services.container_setup import ContainerSetupService

    fake = SetupFake(config={
        "layer-dir": "/usb1-part1/container-layers",
        "tmpdir": "/usb1-part1/container-tmp",
    })
    plan = await ContainerSetupService(fake).apply(request_)
    assert next(s for s in plan.steps if s.key == "config").action == "exists"
    assert not [c for c in fake.commands if c[0] == "config"]


@pytest.mark.asyncio
async def test_the_web_forward_is_always_bound_to_one_interface(request_):
    """An unbounded dstnat publishes an admin UI on WAN. That was a real defect."""
    from backend.app.services.container_setup import ContainerSetupService

    fake = SetupFake()
    await ContainerSetupService(fake).apply(request_)
    dstnat = [c[1] for c in fake.commands if c[0] == "nat" and c[1].get("chain") == "dstnat"]
    assert len(dstnat) == 1
    assert dstnat[0]["in-interface"] == "br.lan"
    assert "mikroman:" in dstnat[0]["comment"]


@pytest.mark.asyncio
async def test_no_interface_means_no_forward_at_all(request_):
    from backend.app.schemas.container import ContainerSetupRequest
    from backend.app.services.container_setup import ContainerSetupService

    fake = SetupFake()
    plan = await ContainerSetupService(fake).apply(
        ContainerSetupRequest(storage_dir="usb1-part1")  # expose_on_interface defaults to None
    )
    assert not [c for c in fake.commands if c[0] == "nat" and c[1].get("chain") == "dstnat"]
    assert next(s for s in plan.steps if s.key == "nat_web").action == "skip"


@pytest.mark.asyncio
async def test_reapplying_an_already_prepared_router_does_nothing(request_):
    from backend.app.services.container_setup import ContainerSetupService

    fake = SetupFake(**_ready_state())
    plan = await ContainerSetupService(fake).apply(request_)
    assert fake.commands == [], fake.commands
    assert plan.ok is True
    assert {s.action for s in plan.steps} <= {"exists", "skip"}


@pytest.mark.asyncio
async def test_it_refuses_to_touch_someone_elses_bridge(request_):
    from backend.app.services.container_setup import ContainerSetupService

    fake = SetupFake(bridges=[{"name": "bridge-containers", "comment": "managed by mgmt"}])
    plan = await ContainerSetupService(fake).apply(request_)
    assert plan.ok is False
    assert any("not managed by MikroMan" in b for b in plan.blockers)
    assert not [c for c in fake.commands if c[0] == "bridge"]


@pytest.mark.asyncio
async def test_it_refuses_a_subnet_the_router_already_uses(request_):
    from backend.app.services.container_setup import ContainerSetupService

    fake = SetupFake(addresses=[{"address": "172.17.0.9/24", "interface": "ether2"}])
    plan = await ContainerSetupService(fake).plan(request_)
    assert plan.ok is False
    assert any("overlaps" in b for b in plan.blockers)


@pytest.mark.asyncio
async def test_storage_that_is_not_there_blocks_before_anything_is_created(request_):
    from backend.app.services.container_setup import ContainerSetupService

    fake = SetupFake(disks=[
        {".id": "*1", "slot": "usb1", "type": "hardware", "fs": "-", "mounted": "false",
         "parent": "", "partition": "false", "size": "500107862016"},
    ])
    plan = await ContainerSetupService(fake).plan(request_)
    assert plan.ok is False and plan.steps[0].key == "storage_dir"
    # The message has to name what the router does have - "not found" without it
    # sends the operator to Winbox to work out the spelling themselves.
    assert "usb1" in plan.blockers[0]
    assert fake.commands == []


@pytest.mark.asyncio
async def test_a_mount_read_only_partition_is_refused_not_retried(request_):
    """The failure a filesystem RouterOS cannot write produces is a half-pull.

    Better to say "mounted read-only" before 340 MB has been downloaded than to
    let the registry explain it afterwards.
    """
    from backend.app.services.container_setup import ContainerSetupService

    fake = SetupFake(disks=[
        {".id": "*2", "slot": "usb1-part1", "type": "partition", "fs": "ntfs",
         "mounted": "true", "mount-point": "usb1-part1", "parent": "usb1",
         "partition": "true", "size": "32000000000", "free": "30000000000",
         "mount-read-only": "true"},
    ])
    plan = await ContainerSetupService(fake).plan(request_)
    assert plan.ok is False
    assert any("read-only" in b for b in plan.blockers)
    assert fake.commands == []


@pytest.mark.asyncio
async def test_a_partition_too_small_for_the_image_blocks_with_the_numbers(request_):
    from backend.app.services.container_setup import ContainerSetupService

    fake = SetupFake(disks=[
        {".id": "*2", "slot": "usb1-part1", "type": "partition", "fs": "ext4",
         "mounted": "true", "mount-point": "usb1-part1", "parent": "usb1",
         "partition": "true", "size": "128000000", "free": "120000000"},
    ])
    plan = await ContainerSetupService(fake).plan(request_)
    assert plan.ok is False
    blocker = " ".join(plan.blockers)
    # The numbers have to be in the message: "not enough space" without them is
    # not actionable when the disk shows 128 MB in Winbox and the image is 340 MB.
    assert "114.4 MB free" in blocker, blocker
    assert "400.0 MB" in blocker, blocker


@pytest.mark.asyncio
async def test_a_refused_command_stops_the_run_and_shows_what_landed(request_):
    """Half-applied setup must be visible, not exception-shaped."""
    from backend.app.services.container_setup import ContainerSetupService

    fake = SetupFake(refuse="veth")
    plan = await ContainerSetupService(fake).apply(request_)
    assert plan.ok is False
    failed = next(s for s in plan.steps if s.action == "failed")
    assert failed.key == "veth"
    assert any("veth" in b for b in plan.blockers)
    # Earlier steps really did happen and are reported as such.
    assert [c[0] for c in fake.commands] == ["config", "file", "bridge"]
    # Nothing after the failure was attempted.
    assert not [c for c in fake.commands if c[0] in ("port", "mount", "container")]


@pytest.mark.asyncio
async def test_environment_values_never_come_back_out_of_a_plan(request_):
    """If an operator does put something in env, the plan must not broadcast it."""
    from backend.app.schemas.container import ContainerSetupRequest
    from backend.app.services.container_setup import ContainerSetupService

    fake = SetupFake()
    plan = await ContainerSetupService(fake).apply(
        ContainerSetupRequest(storage_dir="usb1-part1", extra_env={"SOME_TOKEN": "hunter2"})
    )
    assert ("env", "mikroman_envs", "SOME_TOKEN", "hunter2") in fake.commands
    assert "hunter2" not in str(plan.model_dump())


@pytest.mark.asyncio
async def test_an_ambiguous_subnet_is_rejected_rather_than_guessed():
    from backend.app.schemas.container import ContainerSetupRequest
    from backend.app.services.container_setup import ContainerSetupService

    # 172.17.0.5/24 is a host inside the network, not the network itself: guessing
    # which was meant would place the gateway somewhere the operator did not say.
    plan = await ContainerSetupService(SetupFake()).plan(
        ContainerSetupRequest(storage_dir="usb1-part1", subnet="172.17.0.5/24")
    )
    assert plan.ok is False
    assert "network address" in plan.blockers[0]


def test_the_setup_route_is_not_read_as_a_container_action():
    """``/setup/plan`` has to win against ``/{container_id}/{action}``.

    FastAPI matches routes in declaration order, so with the generic action route
    declared first the request would be parsed as container_id='setup',
    action='plan' and answered with "Unknown action 'plan'" - a route that looks
    registered in the OpenAPI output and is unreachable in practice.
    """
    from fastapi.testclient import TestClient

    from backend.app.api.v1.endpoints import containers as containers_module
    from backend.app.db.session import get_db
    from backend.app.main import app

    fake = SetupFake()

    async def fake_manager(router_id, db):
        return ContainerManager(fake)

    async def fake_db():
        yield None

    containers_module._manager = fake_manager
    app.dependency_overrides[get_db] = fake_db
    try:
        response = TestClient(app).post(
            "/api/v1/routers/1/containers/setup/plan", json={"storage_dir": "usb1-part1"}
        )
        assert response.status_code == 200, response.text
        steps = response.json()["data"]["steps"]
        assert [s["key"] for s in steps][:3] == ["storage_dir", "config", "data_dir"]
        # Reaching the router through the API must still write nothing.
        assert fake.commands == []
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def ros_settings():
    return Settings(
        ROUTEROS_HOST="192.168.88.1", ROUTEROS_PORT=443, ROUTEROS_USE_SSL=True,
        ROUTEROS_SSL_VERIFY=False, ROUTEROS_USER="admin", ROUTEROS_PASSWORD="password",
    )


@pytest.mark.asyncio
async def test_provisioning_sends_the_explicit_routeros_verbs(ros_settings):
    """``POST /menu`` answers "no such command"; ``POST /menu/add`` is the command.

    Verified the hard way against a real 7.24.2 unit while diagnosing why the
    first attempt at this feature failed, so the shapes are pinned here.
    """
    from backend.app.services.routeros import RouterOSClient

    client = RouterOSClient(ros_settings)
    with respx.mock(base_url="https://192.168.88.1:443/rest") as mock:
        cfg = mock.post("/container/config/set").respond(200, json={})
        mounts = mock.post("/container/mounts/add").respond(200, json={".id": "*1"})
        veth = mock.post("/interface/veth/add").respond(200, json={".id": "*2"})
        addfile = mock.post("/file/add").respond(200, json={"ret": "**abc"})

        await client.set_container_config({"layer-dir": "usb1-part1/layers"})
        await client.add_container_mount("mikroman_data", "usb1-part1/mikroman_data", "/data")
        await client.add_veth("veth-mikroman", "172.17.0.2/24", "172.17.0.1")
        await client.add_file("usb1-part1/mikroman_data/keep.txt", "mikroman")

        assert cfg.called and mounts.called and veth.called and addfile.called
        # Mounts and env rows are grouped by `list`; sending `name` gets
        # "unknown parameter name", and omitting `list` gets "missing =list=".
        # Both were learned from the device, so both shapes are pinned.
        mount_body = json.loads(mounts.calls.last.request.content)
        assert mount_body == {"list": "mikroman_data", "src": "usb1-part1/mikroman_data", "dst": "/data"}
        assert json.loads(veth.calls.last.request.content)["gateway"] == "172.17.0.1"
        body = json.loads(addfile.calls.last.request.content)
        assert body == {"name": "usb1-part1/mikroman_data/keep.txt", "contents": "mikroman"}
        # A refusal has to surface, not look like a successful no-op.
        assert addfile.calls.last.request.url.path == "/rest/file/add"


@pytest.mark.asyncio
async def test_a_refused_command_carries_the_routers_own_message(ros_settings):
    from backend.app.services.routeros import RouterOSClient
    from backend.app.services.routeros.provisioning import RouterOSCommandError

    client = RouterOSClient(ros_settings)
    with respx.mock(base_url="https://192.168.88.1:443/rest") as mock:
        mock.post("/container/config/set").respond(400, json={"error": 400, "message": "no such command"})
        with pytest.raises(RouterOSCommandError) as exc:
            await client.set_container_config({"tmpdir": "usb1/tmp"})
        assert "no such command" in str(exc.value)
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_file_listing_never_asks_for_contents(ros_settings):
    """``GET /file`` without a proplist returns every file's bytes.

    One binary on flash - a ``.backup``, a film - makes the body undecodable and
    the call fails for reasons that look like a connection problem. The trap is
    already documented in docs/LESSONS.md from the backup sweeper; a new call
    site has to keep the guard.
    """
    from backend.app.services.routeros import RouterOSClient

    client = RouterOSClient(ros_settings)
    with respx.mock(base_url="https://192.168.88.1:443/rest") as mock:
        route = mock.get("/file").respond(200, json=[{"name": "usb1-part1"}, {"name": "usb1-part1/x.rsc"}])
        assert await client.list_file_names() == ["usb1-part1", "usb1-part1/x.rsc"]
        assert ".proplist" in str(route.calls.last.request.url)
        assert "contents" not in str(route.calls.last.request.url)


# --- Preparing storage: format only where it cannot cost anything -------------


def _format_request(**overrides):
    from backend.app.schemas.container import ContainerFormatRequest

    args = {"slot": "usb1-part1", "file_system": "ext4", "label": "data",
            "confirm": "usb1-part1"}
    args.update(overrides)
    return ContainerFormatRequest(**args)


@pytest.mark.asyncio
async def test_format_needs_the_slot_retyped_before_anything_is_sent():
    """A body a dropdown can fill is one mis-click from erasing a device.

    The confirmation is the only thing between this endpoint and an
    unrecoverable action, so it is checked before the router is even asked.
    """
    from backend.app.services.container_setup import StorageFormatError, format_storage

    fake = SetupFake()
    with pytest.raises(StorageFormatError) as exc:
        await format_storage(fake, _format_request(confirm="yes"))
    assert "confirmation" in str(exc.value).lower()
    assert fake.commands == []


@pytest.mark.asyncio
async def test_format_refuses_the_storage_container_state_lives_on():
    """The app must not saw off the branch it is running from.

    On a deployed router, /container/config points layer-dir and tmpdir at
    usb1-part1 and the data mount reads its src from the same partition - so
    formatting that slot would destroy the image layers and the database in one
    command, including the storage this very instance booted from.
    """
    from backend.app.services.container_setup import StorageFormatError, format_storage

    fake = SetupFake(
        config={"tmpdir": "/usb1-part1/container-tmp", "layer-dir": "/usb1-part1/container-layers"},
        mounts=[{".id": "*1", "list": "mikroman_data", "src": "/usb1-part1/mikroman_data", "dst": "/data"}],
    )
    with pytest.raises(StorageFormatError) as exc:
        await format_storage(fake, _format_request())
    assert "usb1-part1" in str(exc.value)
    assert fake.commands == []


@pytest.mark.asyncio
async def test_formatting_a_device_refuses_when_a_partition_of_it_is_in_use():
    """A whole disk is only offered when nothing under it is holding anything.

    Formatting `usb1` erases the partition table and every partition on it,
    however healthy those partitions look when listed separately.
    """
    from backend.app.services.container_setup import StorageFormatError, format_storage

    fake = SetupFake(mounts=[{"list": "mikroman_data", "src": "/usb1-part1/mikroman_data", "dst": "/data"}])
    with pytest.raises(StorageFormatError) as exc:
        await format_storage(fake, _format_request(slot="usb1", confirm="usb1"))
    assert "usb1-part1" in str(exc.value)
    assert fake.commands == []


@pytest.mark.asyncio
async def test_format_accepts_only_a_filesystem_routeros_can_write():
    from backend.app.schemas.container import ContainerFormatRequest
    from backend.app.services.container_setup import StorageFormatError, format_storage

    fake = SetupFake()
    with pytest.raises(StorageFormatError):
        await format_storage(fake, _format_request(file_system="ntfs"))
    with pytest.raises(StorageFormatError):
        # An arbitrary string would be refused by the device with a worse
        # message; `discard` is a mode, not a filesystem.
        ContainerFormatRequest(slot="usb2", file_system="discard", confirm="usb2")
        await format_storage(fake, _format_request(slot="usb2", file_system="discard", confirm="usb2"))
    assert fake.commands == []


@pytest.mark.asyncio
async def test_an_unknown_slot_is_refused_with_what_does_exist():
    from backend.app.services.container_setup import StorageFormatError, format_storage

    fake = SetupFake()
    with pytest.raises(StorageFormatError) as exc:
        await format_storage(fake, _format_request(slot="usb9", confirm="usb9"))
    message = str(exc.value)
    assert "usb9" in message and "usb1" in message
    assert fake.commands == []


@pytest.mark.asyncio
async def test_a_free_device_is_formatted_with_the_operators_parameters():
    from backend.app.services.container_setup import format_storage

    fake = SetupFake(disks=[
        {".id": "*3", "slot": "usb2", "type": "partition", "fs": "fat32", "mounted": "true",
         "mount-point": "usb2", "parent": "usb2", "partition": "true",
         "size": "64000000000", "free": "63000000000"},
    ])
    result = await format_storage(fake, _format_request(slot="usb2", confirm="usb2",
                                                        file_system="ext4", label="media"))
    assert result["started"] is True
    assert ("format", "usb2", "ext4", "media", "no") in fake.commands


@pytest.mark.asyncio
async def test_format_reports_the_devices_own_state_afterwards():
    """`/disk format` returns while the device is still working.

    The answer has to say so, or the UI shows a finished job that has not
    started writing yet and the next step fails against an unmounted disk.
    """
    from backend.app.services.container_setup import format_storage

    class Busy(SetupFake):
        """Idle when asked whether to format, busy when asked how it went."""

        def __init__(self, **state):
            super().__init__(**state)
            self._reads = 0

        async def list_disks(self):
            self._reads += 1
            return [{"slot": "usb2-part1", "type": "partition", "fs": "-", "mounted": "false",
                     "partition": "true", "parent": "usb2", "size": "64000000000",
                     "formatting": "true" if self._reads > 1 else "false"}]

    result = await format_storage(Busy(), _format_request(slot="usb2-part1", confirm="usb2-part1"))
    assert result["formatting"] is True
    assert "background" in result["detail"]


@pytest.mark.asyncio
async def test_storage_inventory_marks_what_is_and_is_not_usable():
    from backend.app.services.container_setup import read_storage

    storage = await read_storage(SetupFake())
    by_slot = {d.slot: d for d in storage.disks}
    assert by_slot["usb1-part1"].usable_for_containers is True
    assert by_slot["usb1-part1"].formatable is True
    # The device row itself: no filesystem of its own, and its partition is the
    # one the app would run on.
    assert by_slot["usb1"].usable_for_containers is False
    assert "no filesystem" in by_slot["usb1"].note

    chosen = await read_storage(SetupFake(), "usb1-part1")
    assert chosen.ready is True and chosen.free_bytes == 280677068800
    assert chosen.problems == []


@pytest.mark.asyncio
async def test_storage_inventory_without_a_choice_still_lists_the_disks():
    """The picker needs the list before anything is selected."""
    from backend.app.services.container_setup import read_storage

    storage = await read_storage(SetupFake(), None)
    assert storage.ready is False
    assert {d.slot for d in storage.disks} == {"usb1", "usb1-part1"}
