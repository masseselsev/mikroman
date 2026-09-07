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

    def __init__(self, *, packages=None, containers=None, mounts=None, envs=None, config=None):
        self._packages = packages if packages is not None else []
        self._containers = containers or []
        self._mounts = mounts or []
        self._envs = envs or []
        self._config = config or {}
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
    assert args["mounts"] == "webroot"
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
    assert "/api/v1/routers/{router_id}/containers/migrate-data" in paths


# --- Provisioning a router to host a container --------------------------------


class SetupFake:
    """A router that reports whatever state a test needs, and records commands."""

    def __init__(self, **state):
        self.files = state.get("files", ["usb1-part1", "usb1-part1/shared"])
        self.addresses = state.get("addresses", [
            {"address": "192.168.123.1/24", "interface": "br.lan"},
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

    # writes
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
        containers=[{"name": "mikroman", "status": "stopped", "tag": "ghcr.io/masseselsev/mikroman:latest"}],
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
        "config", "data_dir", "bridge", "veth", "bridge_port", "address",
        "nat_masquerade", "nat_web", "mount", "env", "container",
    ]
    assert plan.gateway_ip == "172.17.0.1" and plan.container_ip == "172.17.0.2"
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
    assert all(s.action == "done" or s.action == "skip" for s in plan.steps), plan.steps
    # Storage is what the whole exercise turns on: layers must never land on flash.
    config_call = fake.commands[0][1]
    assert config_call["layer-dir"].startswith("usb1-part1")
    assert config_call["tmpdir"].startswith("usb1-part1")


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

    fake = SetupFake(files=["flash"])
    plan = await ContainerSetupService(fake).plan(request_)
    assert plan.ok is False and plan.steps[0].key == "storage_dir"
    assert fake.commands == []


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
        assert [s["key"] for s in steps][:3] == ["config", "data_dir", "bridge"]
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


# --- Carrying the installation's data into the container ----------------------


@pytest.mark.asyncio
async def test_migration_refuses_to_replace_a_running_containers_database():
    from backend.app.services.container_setup import DataMigrationError, migrate_data

    fake = SetupFake(containers=[{"name": "mikroman", "status": "running"}])
    with pytest.raises(DataMigrationError) as exc:
        await migrate_data(fake, storage_dir="usb1-part1")
    assert "running" in str(exc.value)
    assert fake.commands == []


@pytest.mark.asyncio
async def test_migration_sends_the_database_and_the_key_together(tmp_path, monkeypatch):
    """Both or neither: app.db without .secret_key decrypts nothing on the other side."""
    import sqlite3

    from backend.app.services import container_setup as cs

    source = tmp_path / "app.db"
    con = sqlite3.connect(source)
    con.execute("create table routers (id integer primary key, name text)")
    con.execute("insert into routers (name) values ('hAP be3 Media')")
    con.commit()
    con.close()
    (tmp_path / ".secret_key").write_bytes(b"a-key-value")

    data_dir = tmp_path / "runtime"
    data_dir.mkdir()
    # The key belongs in the data directory, beside the database it decrypts -
    # that is where secrets.py looks, and the same rule holds for /data inside the
    # container image.
    (data_dir / ".secret_key").write_bytes(b"a-key-value")
    monkeypatch.setattr(cs, "resolve_data_dir", lambda: data_dir)
    monkeypatch.setattr(cs.settings, "DATABASE_URL", f"sqlite+aiosqlite:///{source}")

    fake = SetupFake()
    result = await cs.migrate_data(fake, storage_dir="usb1-part1")

    # Staged, not shipped: RouterOS takes no binary upload, so the API's job is to
    # produce a consistent file and say exactly where it has to land.
    staged = data_dir / "mikroman-migration.db"
    assert result["database_bytes"] > 0
    assert staged.exists() and staged.stat().st_size == result["database_bytes"]
    assert result["destination"] == "usb1-part1/mikroman_data/app.db"
    assert result["secret_key"] == "included"
    assert fake.commands == []  # nothing is pushed over REST on this release
    # The snapshot is a readable SQLite database with the row that was in the
    # source - proof it is an actual backup and not a truncated copy.
    import sqlite3
    con = sqlite3.connect(staged)
    assert con.execute("select name from sqlite_master where type='table'").fetchall() == [("routers",)]
    assert con.execute("select count(*) from routers").fetchone()[0] == 1
    con.close()
    assert any("app.db" in step for step in result["next_steps"])
    assert any(".secret_key" in step for step in result["next_steps"])


@pytest.mark.asyncio
async def test_migration_reports_a_missing_source_database(tmp_path, monkeypatch):
    from backend.app.services import container_setup as cs
    from backend.app.services.container_setup import DataMigrationError

    data_dir = tmp_path / "runtime"
    data_dir.mkdir()
    monkeypatch.setattr(cs, "resolve_data_dir", lambda: data_dir)
    monkeypatch.setattr(cs.settings, "DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/nowhere.db")

    with pytest.raises(DataMigrationError) as exc:
        await cs.migrate_data(SetupFake(), storage_dir="usb1-part1")
    assert "no database" in str(exc.value)
