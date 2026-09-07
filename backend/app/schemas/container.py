"""DTOs for the RouterOS container-management page.

Container support is an optional RouterOS package. The list endpoint always
returns a ``support`` block first, so the UI can render an informative banner
instead of an error when the package is not installed.
"""
from typing import List, Optional

from pydantic import BaseModel, Field


class ContainerSupportDTO(BaseModel):
    """Whether this router can run containers, and why not if it cannot."""
    installed: bool = False
    enabled: bool = False
    version: Optional[str] = None
    # 'ready'          - package present and enabled
    # 'not_installed'  - the container package is not on the router
    # 'disabled'       - installed but disabled; needs enable + reboot
    # 'unreachable'    - could not query the router
    status: str = "not_installed"
    message: Optional[str] = None


class ContainerDTO(BaseModel):
    """One container as reported by ``/container``."""
    id: str
    name: Optional[str] = None
    tag: Optional[str] = None
    status: Optional[str] = None          # running | stopped | error | extracting …
    os: Optional[str] = None
    arch: Optional[str] = None
    interface: Optional[str] = None
    root_dir: Optional[str] = None
    mounts: Optional[str] = None
    envlist: Optional[str] = None
    cmd: Optional[str] = None
    entrypoint: Optional[str] = None
    hostname: Optional[str] = None
    logging: Optional[bool] = None
    start_on_boot: Optional[bool] = None
    comment: Optional[str] = None


class ContainerMountDTO(BaseModel):
    id: str
    name: Optional[str] = None
    src: Optional[str] = None
    dst: Optional[str] = None


class ContainerEnvDTO(BaseModel):
    id: str
    name: Optional[str] = None
    key: Optional[str] = None
    value: Optional[str] = None


class ContainerConfigDTO(BaseModel):
    tmpdir: Optional[str] = None
    registry_url: Optional[str] = None
    ram_high: Optional[str] = None
    layer_dir: Optional[str] = None


class ContainerOverviewDTO(BaseModel):
    """Everything the container page needs in one round trip."""
    support: ContainerSupportDTO
    containers: List[ContainerDTO] = Field(default_factory=list)
    mounts: List[ContainerMountDTO] = Field(default_factory=list)
    envs: List[ContainerEnvDTO] = Field(default_factory=list)
    config: ContainerConfigDTO = Field(default_factory=ContainerConfigDTO)


class ContainerCreateRequest(BaseModel):
    """Create a container from a remote image.

    Mirrors the RouterOS ``/container/add`` arguments that matter for a first
    cut; anything else can be set later on the router.
    """
    remote_image: str = Field(..., min_length=1, description="e.g. 'library/nginx:alpine'")
    interface: str = Field(..., min_length=1, description="veth interface the container attaches to")
    root_dir: Optional[str] = Field(None, description="Where the container's root filesystem is unpacked")
    hostname: Optional[str] = None
    cmd: Optional[str] = None
    entrypoint: Optional[str] = None
    mounts: Optional[str] = Field(None, description="Comma-separated names from /container/mounts")
    envlist: Optional[str] = Field(None, description="A name from /container/envs")
    start_on_boot: bool = False
    logging: bool = True
    comment: Optional[str] = None


# --- One-click preparation of a router to host a container ---------------------


class ContainerSetupRequest(BaseModel):
    """What :class:`ContainerSetupService` will create on the router.

    Only ``storage_dir`` has no default. Everything else has a value that is
    correct for MikroMan on a typical board, because the dangerous failure here
    is a silently defaulted one: image layers are ~340 MB unpacked, and several
    RouterOS boards have under 512 MB of internal flash, so a setup that guessed
    the storage path would either fail mid-pull or eat the flash the router
    restores its own configuration from.
    """

    storage_dir: str = Field(
        ..., min_length=2,
        description="Router path with room for image layers and the database, e.g. 'usb1-part1'",
    )
    image: str = "ghcr.io/masseselsev/mikroman:latest"
    container_name: str = "mikroman"
    bridge_name: str = "bridge-containers"
    veth_name: str = "veth-mikroman"
    mount_name: str = "mikroman_data"
    data_dir_name: str = "mikroman_data"
    subnet: str = Field("172.17.0.0/24", description="Container network; the router takes .1, the container .2")
    web_port: int = Field(1928, ge=1, le=65535)
    expose_on_interface: Optional[str] = Field(
        None,
        description="LAN interface allowed to reach the web UI (e.g. 'br.lan'). "
                    "None keeps the port forward off, and the UI stays reachable only from the router.",
    )
    dns_servers: Optional[str] = Field(
        None,
        description="Only sent if /container/config actually has the attribute on this RouterOS release; "
                    "v7.24.2 does not, and containers resolve through the router's own resolver anyway.",
    )
    create_container: bool = Field(True, description="Also create (not start) the container at the end")
    extra_env: dict = Field(
        default_factory=dict,
        description="Non-secret environment overrides. Router credentials and the Telegram token are NOT "
                    "needed here - they live in the database the migration carries, and writing them as env "
                    "would put them in plaintext in the router config and in every exported .rsc.",
    )


class ContainerSetupStepDTO(BaseModel):
    """One line of the plan: what was found, and what will be done about it.

    ``dry_run`` and ``apply`` build the same list, so what the user was shown is
    literally what was executed rather than a description written afterwards.
    """

    key: str
    action: str = Field(..., description="create | set | exists | skip | conflict | blocked | done | failed")
    detail: str = ""
    # Set only when apply() actually ran the command.
    applied: bool = False


class ContainerSetupPlanDTO(BaseModel):
    """The whole plan, with blockers listed separately so the UI cannot miss them."""

    ok: bool = True
    storage_dir: str = ""
    gateway_ip: str = ""
    container_ip: str = ""
    steps: List[ContainerSetupStepDTO] = Field(default_factory=list)
    blockers: List[str] = Field(default_factory=list)


class ContainerMigrateRequest(BaseModel):
    """Carry this installation's data into the container's mount."""

    storage_dir: str = Field(..., min_length=2)
    data_dir_name: str = "mikroman_data"
    container_name: str = "mikroman"


class ContainerMigrateResultDTO(BaseModel):
    """A staged snapshot and the hand-off that is still the operator's to make.

    RouterOS exposes no upload endpoint for binaries on this release, so the API
    says plainly what has been prepared and what has to be copied - a migration
    that silently did half the job would be worse than one that stops and tells.
    """

    database_bytes: int = 0
    staged_path: str = ""
    secret_key_path: str = ""
    destination: str = ""
    # 'included' or 'absent' - the key's value is never returned.
    secret_key: str = "absent"
    next_steps: List[str] = Field(default_factory=list)
