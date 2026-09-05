"""Tests verifying multi-architecture dependency constraints in requirements.txt."""

from packaging.requirements import Requirement


def test_uvloop_platform_marker_in_requirements():
    """Verify uvloop has environment markers excluding armv7l where wheels are missing."""
    with open("backend/requirements.txt", "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    uvloop_req = next((r for r in lines if r.startswith("uvloop")), None)
    assert uvloop_req is not None, "uvloop must be declared in backend/requirements.txt"

    req = Requirement(uvloop_req)
    assert req.name == "uvloop"
    assert req.marker is not None, "uvloop must specify platform markers for multi-arch"

    # Should evaluate to True on x86_64 and aarch64 on Linux
    assert req.marker.evaluate({"platform_machine": "x86_64", "sys_platform": "linux"}) is True
    assert req.marker.evaluate({"platform_machine": "aarch64", "sys_platform": "linux"}) is True

    # Should evaluate to False on armv7l and Windows
    assert req.marker.evaluate({"platform_machine": "armv7l", "sys_platform": "linux"}) is False
    assert req.marker.evaluate({"platform_machine": "x86_64", "sys_platform": "win32"}) is False
