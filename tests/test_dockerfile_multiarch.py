"""Tests verifying the Dockerfile can install every dependency on linux/arm/v7.

Several hard dependencies (cffi via cryptography, greenlet via SQLAlchemy,
MarkupSafe via Mako/alembic, PyYAML) publish no armv7 wheels on PyPI and ship
only source distributions. The runtime base image, python:3.12-slim, carries no
compiler, so installing them there fails outright on the arm/v7 leg of the
release build. These tests pin the arrangement that fixes it: a throwaway
builder stage that owns the toolchain and compiles a local wheelhouse, and a
runtime stage that installs from that wheelhouse offline.
"""

import re

DOCKERFILE = "Dockerfile"


def _stages():
    """Split the Dockerfile into {stage_name: body} keyed by each FROM ... AS <name>."""
    with open(DOCKERFILE, "r", encoding="utf-8") as f:
        text = f.read()

    stages, name, body = {}, None, []
    for line in text.splitlines():
        match = re.match(r"^\s*FROM\s+.*\bAS\s+(\S+)", line, re.IGNORECASE)
        if match:
            if name:
                stages[name] = "\n".join(body)
            name, body = match.group(1).lower(), [line]
        elif name:
            body.append(line)
    if name:
        stages[name] = "\n".join(body)
    return stages


def test_wheelbuilder_stage_provides_c_toolchain():
    """A dedicated builder stage must install a compiler and libffi headers."""
    stages = _stages()
    assert "wheelbuilder" in stages, "Dockerfile must define a 'wheelbuilder' stage"

    body = stages["wheelbuilder"]
    assert "build-essential" in body, "wheelbuilder must install a C/C++ compiler toolchain"
    assert "libffi-dev" in body, "wheelbuilder must install libffi headers so cffi can compile"


def test_wheelbuilder_stage_compiles_a_wheelhouse():
    """The builder stage must turn requirements.txt into a local wheelhouse."""
    body = _stages()["wheelbuilder"]
    assert "pip wheel" in body, "wheelbuilder must build wheels with 'pip wheel'"
    assert "backend/requirements.txt" in body, "wheelbuilder must build from backend/requirements.txt"


def test_runtime_installs_offline_from_the_wheelhouse():
    """The runtime stage must install from the wheelhouse, never compiling or fetching."""
    body = _stages()["runtime"]
    assert "--no-index" in body, "runtime must install offline so no source build can be triggered"
    assert "--find-links" in body, "runtime must resolve packages from the wheelhouse"
    assert "wheelbuilder" in body, "runtime must consume the wheels produced by the builder stage"


def test_runtime_base_image_stays_slim():
    """The shipped image must remain the slim base; the toolchain must not leak into it."""
    body = _stages()["runtime"]
    assert "python:3.12-slim" in body.splitlines()[0], "runtime stage must build on python:3.12-slim"
    assert "apt-get install" not in body, "runtime stage must not install build tooling"
