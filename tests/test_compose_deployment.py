"""Tests pinning how docker-compose.yml obtains the application image.

The compose file is the documented, recommended way to run MikroMan, and the
devices it runs on are the weak half of the MikroTik range - an RB4011 has
1 GB of RAM and a 32-bit ARM core. Building the image there means running
`npm ci`, a Vite production build and four C extension compilations on the
router itself, which is why the release pipeline publishes multi-architecture
images to ghcr.io in the first place. A `build:` section in this file would
quietly send every user down that path instead.
"""

import yaml

COMPOSE = "docker-compose.yml"
IMAGE = "ghcr.io/masseselsev/mikroman"


def _service():
    """Return the `mikroman` service definition from the compose file."""
    with open(COMPOSE, "r", encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    services = compose.get("services", {})
    assert "mikroman" in services, "docker-compose.yml must define a 'mikroman' service"
    return services["mikroman"]


def test_compose_pulls_the_published_image():
    """Compose must reference the published multi-arch image by name."""
    image = _service().get("image", "")
    assert image.startswith(IMAGE), f"compose should run {IMAGE}, got {image!r}"


def test_compose_does_not_build_on_the_deployment_device():
    """No build section: a router must never compile the image itself."""
    assert "build" not in _service(), (
        "docker-compose.yml must not build from source - deployment targets "
        "include 1 GB armv7 boards that cannot compile this image"
    )


BUILD_OVERLAY = "docker-compose.build.yml"


def _build_overlay_service():
    """Return the `mikroman` service from the build overlay."""
    with open(BUILD_OVERLAY, "r", encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    return compose.get("services", {})["mikroman"]


def test_build_overlay_exists_for_running_unreleased_code():
    """Building from source stays available as an explicit, opt-in overlay.

    Contributors and anyone running a change that has not been released yet
    still need to bring the stack up from local sources. That path must exist -
    it just must not be what a router owner gets by default.
    """
    service = _build_overlay_service()
    assert service.get("build", {}).get("context") == ".", "overlay must build from the repo root"
    assert service.get("build", {}).get("dockerfile") == "Dockerfile"


def test_build_overlay_tags_a_local_image_and_never_pulls():
    """The overlay must not masquerade as, or reach for, the released image."""
    service = _build_overlay_service()
    image = service.get("image", "")
    assert image and not image.startswith("ghcr.io/"), (
        "a locally built image must carry a local tag so it cannot be confused "
        f"with a published release, got {image!r}"
    )
    assert service.get("pull_policy") == "build", "overlay must build rather than pull"
