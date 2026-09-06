"""Tests verifying multi-architecture dependency constraints in requirements.txt."""

import pytest
from packaging.requirements import Requirement

# Optional C-accelerated dependencies that publish no linux/arm/v7 wheels and are
# not required for correctness. uvicorn silently falls back to its pure-Python
# implementations (asyncio event loop, h11 parser) when these are absent, so on
# armv7 - the architecture of RB4011-class MikroTik boards - they are excluded by
# environment marker rather than compiled from source for no functional gain.
OPTIONAL_ACCELERATORS = ["uvloop", "httptools"]


def _requirement(name):
    """Parse the pinned requirement line declaring ``name`` in backend/requirements.txt."""
    with open("backend/requirements.txt", "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    raw = next((r for r in lines if Requirement(r).name == name), None)
    assert raw is not None, f"{name} must be declared in backend/requirements.txt"
    return Requirement(raw)


@pytest.mark.parametrize("package", OPTIONAL_ACCELERATORS)
def test_optional_accelerator_excluded_on_armv7(package):
    """Verify optional C accelerators carry markers excluding armv7l and Windows."""
    req = _requirement(package)
    assert req.marker is not None, f"{package} must specify platform markers for multi-arch"

    # Should evaluate to True on x86_64 and aarch64 on Linux
    assert req.marker.evaluate({"platform_machine": "x86_64", "sys_platform": "linux"}) is True
    assert req.marker.evaluate({"platform_machine": "aarch64", "sys_platform": "linux"}) is True

    # Should evaluate to False on armv7l and Windows
    assert req.marker.evaluate({"platform_machine": "armv7l", "sys_platform": "linux"}) is False
    assert req.marker.evaluate({"platform_machine": "x86_64", "sys_platform": "win32"}) is False
