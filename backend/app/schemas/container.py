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
