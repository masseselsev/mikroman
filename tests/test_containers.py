"""RouterOS container management.

The container package is optional and not on a stock install, so the behaviour
that matters most here is graceful degradation: the overview still comes back
when the feature is absent, with a ``support`` block that explains why, and the
action endpoints refuse cleanly rather than throwing.
"""
import pytest

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


def test_container_routes_are_registered():
    from backend.app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/routers/{router_id}/containers" in paths
    assert "/api/v1/routers/{router_id}/containers/{container_id}/{action}" in paths
