"""Tests validating GitHub Actions workflow YAML syntax and structure."""

import os

import yaml


def test_release_docker_workflow_structure():
    """Verify release-docker.yml exists and defines correct triggers, permissions, and steps."""
    workflow_path = os.path.join(".github", "workflows", "release-docker.yml")
    assert os.path.exists(workflow_path), f"{workflow_path} must exist"

    with open(workflow_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # Validate triggers
    assert "on" in data
    triggers = data["on"]
    assert "release" in triggers
    assert triggers["release"].get("types") == ["published"]
    assert "workflow_dispatch" in triggers

    # Validate permissions
    assert data.get("permissions", {}).get("packages") == "write"
    assert data.get("permissions", {}).get("contents") == "read"

    # Validate jobs
    jobs = data.get("jobs", {})
    assert "validate" in jobs, "Must have validate job as quality gate"
    assert "build-and-push" in jobs, "Must have build-and-push job"

    # Validate dependencies and platforms
    build_job = jobs["build-and-push"]
    needs = build_job.get("needs")
    assert needs == "validate" or (isinstance(needs, list) and "validate" in needs)

    steps = build_job.get("steps", [])
    step_uses = [s.get("uses", "") for s in steps]
    assert any("setup-qemu-action" in u for u in step_uses), "QEMU setup action required"
    assert any("setup-buildx-action" in u for u in step_uses), "Buildx setup action required"
    assert any("login-action" in u for u in step_uses), "Login action required"
    assert any("metadata-action" in u for u in step_uses), "Metadata action required"
    assert any("build-push-action" in u for u in step_uses), "Build-push action required"

    # Validate target platforms in build-push step
    build_push_step = next(s for s in steps if "build-push-action" in s.get("uses", ""))
    platforms = build_push_step.get("with", {}).get("platforms", "")
    assert "linux/amd64" in platforms
    assert "linux/arm64" in platforms
    assert "linux/arm/v7" in platforms
