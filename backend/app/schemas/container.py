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
    """One container as reported by ``/container``.

    The resource fields are what the page had nothing to show before. RouterOS
    reports them live on the container row, so no polling endpoint is needed:
    ``cpu-usage`` is a percentage of the device, ``memory-current`` is the
    cgroup's usage in bytes. That memory figure is deliberately labelled as the
    cgroup and not the process, because it includes the page cache the container
    pushes through its data mount - the difference between "the app uses 500 MB"
    and the ~230 MB it actually holds.
    """
    id: str
    name: Optional[str] = None
    tag: Optional[str] = None
    status: Optional[str] = None          # running | stopped | error | extracting …
    running: Optional[bool] = None        # `/container` reports `running`, not `status`, on 7.x
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
    cpu_usage_pct: Optional[float] = None
    memory_current_bytes: Optional[int] = None
    memory_high_bytes: Optional[int] = None
    memory_max_bytes: Optional[int] = None
    # Unpacked size of the container's own storage, bytes.
    disk_size_bytes: Optional[int] = None
    restart_count: Optional[int] = None
    # Seconds the device waits after SIGTERM before SIGKILL.
    stop_time_seconds: Optional[int] = None


class ContainerHostDTO(BaseModel):
    """The router's own totals, so a container's numbers can be read as a share."""

    cpu_load_pct: Optional[int] = None
    total_memory_bytes: Optional[int] = None
    free_memory_bytes: Optional[int] = None
    uptime: Optional[str] = None


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
    """``/container/config`` - where the device keeps container state.

    ``layer_dir``/``tmpdir`` decide whether a pull survives on flash or on
    external storage; ``memory_high`` is the ceiling across all containers, which
    on a board shared with routing is the difference between a slow app and an
    unrestrained one.
    """

    tmpdir: Optional[str] = None
    registry_url: Optional[str] = None
    ram_high: Optional[str] = None
    layer_dir: Optional[str] = None
    memory_current_bytes: Optional[int] = None
    memory_high_bytes: Optional[int] = None
    memory_max_bytes: Optional[int] = None


class ContainerDiskDTO(BaseModel):
    """One row of ``/disk``: a device or a partition, as the router sees it.

    RouterOS 7.13+ exposes storage here rather than letting a path name imply a
    mounted volume, which matters because the failure modes are different and
    only some of them are fixable by the operator: a disk that is present but
    unmounted, a filesystem RouterOS mounts read-only, a partition with no room
    for a 340 MB image, and a device with no filesystem at all.
    """

    slot: str
    parent: Optional[str] = None
    is_partition: bool = False
    model: Optional[str] = None
    serial: Optional[str] = None
    fs: Optional[str] = None                 # '-', 'ext4', 'fat32', 'ntfs', …
    mount_point: Optional[str] = None
    mounted: bool = False
    read_only: bool = False
    formatting: bool = False
    disabled: bool = False
    size_bytes: Optional[int] = None
    free_bytes: Optional[int] = None
    used_pct: Optional[int] = None
    temperature_c: Optional[int] = None
    io_errors: Optional[int] = None
    # What this service would do with it, and why. Populated by the assessment.
    usable_for_containers: bool = False
    formatable: bool = False
    note: str = ""


class ContainerStorageDTO(BaseModel):
    """Verdict on one storage directory, plus every disk the router can see.

    ``ready`` is the answer the plan needs. ``disks`` is the whole inventory, so
    the UI can offer the alternatives (and the format action) instead of telling
    the operator to go and look in Winbox.
    """

    ready: bool = False
    storage_dir: str = ""
    matched_slot: Optional[str] = None
    free_bytes: Optional[int] = None
    size_bytes: Optional[int] = None
    fs: Optional[str] = None
    # Blocking: Apply refuses while any of these is present.
    problems: List[str] = Field(default_factory=list)
    # Non-blocking: worth showing to the operator, but the pull will work.
    warnings: List[str] = Field(default_factory=list)
    disks: List[ContainerDiskDTO] = Field(default_factory=list)
    # Bytes a pull needs before anything else is considered: the unpacked image
    # plus room for a layer or two of churn.
    required_bytes: int = 0


class ContainerOverviewDTO(BaseModel):
    """Everything the container page needs in one round trip."""
    support: ContainerSupportDTO
    host: ContainerHostDTO = Field(default_factory=ContainerHostDTO)
    containers: List[ContainerDTO] = Field(default_factory=list)
    mounts: List[ContainerMountDTO] = Field(default_factory=list)
    envs: List[ContainerEnvDTO] = Field(default_factory=list)
    config: ContainerConfigDTO = Field(default_factory=ContainerConfigDTO)
    storage: ContainerStorageDTO = Field(default_factory=ContainerStorageDTO)


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
        description="Router storage slot with room for image layers and the database, e.g. 'usb1-part1'. "
                    "Validated against /disk, not guessed from a path that happens to resolve.",
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
    ram_high: Optional[str] = Field(
        None,
        description="Ceiling for all containers together, e.g. '768M'. Left alone when unset: the "
                    "unlimited default is what a 2 GB board runs on today, and a value that is too low "
                    "turns a slow app into an OOM-killed one.",
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
                    "needed here - they live in the encrypted database, and writing them as env would put "
                    "them in plaintext in the running config and in every exported .rsc.",
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
    # What /disk said about the chosen storage, including every alternative the
    # router can see. The plan refuses to describe storage as a step without it.
    storage: ContainerStorageDTO = Field(default_factory=ContainerStorageDTO)
    steps: List[ContainerSetupStepDTO] = Field(default_factory=list)
    blockers: List[str] = Field(default_factory=list)


# --- Preparing storage --------------------------------------------------------

#: Filesystems RouterOS can write on a USB disk. ``ntfs`` is absent on purpose:
#: it mounts read-only without extra packages, which is precisely the state a
#: container must not have its layer directory on.
SUPPORTED_FILE_SYSTEMS = ("ext4", "fat32", "exfat", "xfs", "btrfs")


class ContainerFormatRequest(BaseModel):
    """Format one disk or partition. Destructive, and shaped like it.

    ``confirm`` has to repeat the slot exactly. That is not ceremony for the
    operator's sake - a request body that can be assembled from a dropdown alone
    is one mis-click from erasing the stick a router serves its shares off, and
    the app cannot undo it.
    """

    slot: str = Field(..., min_length=1, description="The /disk slot, e.g. 'usb1-part1'")
    file_system: str = Field("ext4", description=f"One of: {', '.join(SUPPORTED_FILE_SYSTEMS)}")
    label: str = Field("", max_length=16)
    mbr_partition_table: bool = Field(
        False, description="Write an MBR table when formatting a whole device (yes/no in RouterOS)"
    )
    confirm: str = Field(..., min_length=1, description="Must equal 'slot' exactly, or the request is refused")


class ContainerFormatResultDTO(BaseModel):
    """What the device accepted, and how to watch it finish."""

    started: bool = False
    slot: str = ""
    file_system: str = ""
    label: str = ""
    # Formatting runs in the background; the row reports formatting=true until
    # it is done, so the UI refreshes rather than pretending to poll a job id.
    formatting: bool = False
    detail: str = ""
