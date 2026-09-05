# GitHub Actions Multi-Arch Docker Container Releases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create an automated GitHub Actions CI/CD workflow that validates code quality and builds/publishes multi-architecture Docker container images (`linux/amd64`, `linux/arm64`, `linux/arm/v7`) to GitHub Container Registry (`ghcr.io`) upon release publications.

**Architecture:** A two-stage GitHub Actions pipeline (`validate` followed by `build-and-push`) with native runner BuildKit compilation for frontend assets, QEMU multi-arch emulation, GHA layer caching, and automatic semantic version tagging.

**Tech Stack:** GitHub Actions, Docker Buildx, QEMU, GitHub Container Registry (`ghcr.io`), Python 3.12, Node.js 22, Pytest, Ruff, Vitest.

## Global Constraints
- Target Registry: `ghcr.io/masseselsev/mikroman`
- Architectures: `linux/amd64`, `linux/arm64`, `linux/arm/v7`
- Triggers: `release: types: [published]`, `workflow_dispatch`
- Authentication: Automatic GitHub Actions `${{ secrets.GITHUB_TOKEN }}` (no external secrets)
- No broken commits; preserve all existing tests and linters

---

### Task 1: Optimize Dockerfile and Dependencies for Multi-Architecture Builds

**Files:**
- Modify: `Dockerfile`
- Modify: `backend/requirements.txt`
- Test: `tests/test_requirements_multiarch.py`

**Interfaces:**
- Consumes: Existing `Dockerfile` and `backend/requirements.txt`
- Produces: BuildKit-optimized `Dockerfile` with `--platform=$BUILDPLATFORM` for the frontend build stage, and platform-specific environment markers for `uvloop` in `backend/requirements.txt`.

- [ ] **Step 1: Write a test verifying requirements markers and compatibility**

Create `tests/test_requirements_multiarch.py`:
```python
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

    # Should evaluate to True on x86_64 and aarch64
    assert req.marker.evaluate({"platform_machine": "x86_64", "sys_platform": "linux"}) is True
    assert req.marker.evaluate({"platform_machine": "aarch64", "sys_platform": "linux"}) is True

    # Should evaluate to False on armv7l and Windows
    assert req.marker.evaluate({"platform_machine": "armv7l", "sys_platform": "linux"}) is False
    assert req.marker.evaluate({"platform_machine": "x86_64", "sys_platform": "win32"}) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_requirements_multiarch.py -v`  
Expected: FAIL (assertion error: `req.marker is not None`)

- [ ] **Step 3: Update `Dockerfile` and `backend/requirements.txt`**

In `Dockerfile`:
Update line 9 from:
```dockerfile
FROM node:22-alpine AS frontend
```
to:
```dockerfile
FROM --platform=$BUILDPLATFORM node:22-alpine AS frontend
```

In `backend/requirements.txt`:
Update line 40 from:
```text
uvloop==0.22.1
```
to:
```text
uvloop==0.22.1; sys_platform != "win32" and (platform_machine == "x86_64" or platform_machine == "aarch64")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_requirements_multiarch.py -v`  
Expected: PASS

---

### Task 2: Create GitHub Actions Workflow for Release Container Builds

**Files:**
- Create: `.github/workflows/release-docker.yml`
- Test: `tests/test_github_workflow_syntax.py`

**Interfaces:**
- Consumes: GitHub Actions runner environment, Dockerfile, repository metadata
- Produces: Validated multi-platform Docker container published to `ghcr.io/masseselsev/mikroman` with semver and latest tags on GitHub Releases.

- [ ] **Step 1: Write a test verifying workflow YAML syntax and schema integrity**

Create `tests/test_github_workflow_syntax.py`:
```python
"""Tests validating GitHub Actions workflow YAML syntax and structure."""

import os
import yaml


def test_release_docker_workflow_structure():
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
    assert build_job.get("needs") == "validate" or "validate" in build_job.get("needs", [])

    steps = build_job.get("steps", [])
    step_uses = [s.get("uses", "") for s in steps]
    assert any("setup-qemu-action" in u for u in step_uses)
    assert any("setup-buildx-action" in u for u in step_uses)
    assert any("login-action" in u for u in step_uses)
    assert any("metadata-action" in u for u in step_uses)
    assert any("build-push-action" in u for u in step_uses)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_github_workflow_syntax.py -v`  
Expected: FAIL (file does not exist)

- [ ] **Step 3: Create `.github/workflows/release-docker.yml`**

Create `.github/workflows/release-docker.yml` with full workflow specification:
```yaml
name: Release Docker Container

on:
  release:
    types: [published]
  workflow_dispatch:

permissions:
  contents: read
  packages: write

jobs:
  validate:
    name: Validate Code & Build Assets
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python 3.12
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: "pip"

      - name: Install backend dependencies & test tooling
        run: |
          python -m pip install --upgrade pip
          pip install pytest pytest-asyncio ruff -r backend/requirements.txt

      - name: Run backend linter (ruff)
        run: |
          ruff check .

      - name: Run backend test suite
        run: |
          pytest -v

      - name: Set up Node.js 22
        uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: "npm"
          cache-dependency-path: frontend/package-lock.json

      - name: Install frontend dependencies
        run: |
          cd frontend
          npm ci

      - name: Run frontend unit tests
        run: |
          cd frontend
          npm test -- --run

      - name: Verify frontend production bundle
        run: |
          cd frontend
          npm run build

  build-and-push:
    name: Build & Push Multi-Arch Docker Image
    needs: [validate]
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up QEMU
        uses: docker/setup-qemu-action@v3
        with:
          platforms: linux/amd64,linux/arm64,linux/arm/v7

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract Docker metadata & tags
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ghcr.io/${{ github.repository }}
          tags: |
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=semver,pattern={{major}}
            type=raw,value=latest,enable=${{ github.event_name == 'release' }}
            type=sha,format=short

      - name: Build and push Docker image
        uses: docker/build-push-action@v6
        with:
          context: .
          file: ./Dockerfile
          platforms: linux/amd64,linux/arm64,linux/arm/v7
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_github_workflow_syntax.py -v`  
Expected: PASS

---

### Task 3: Update README.md and Verify Entire Project Test Suite

**Files:**
- Modify: `README.md`
- Test: Full backend & frontend test suites

**Interfaces:**
- Consumes: Built GHCR images and quick-start docs
- Produces: Updated user documentation for running `ghcr.io/masseselsev/mikroman:latest` directly.

- [ ] **Step 1: Update `README.md`**

In `README.md`:
Under the **Quick Start** section, add pre-built GHCR image instructions alongside the local build option:
```bash
# Option A: Pull & run official pre-built multi-arch image from GitHub Container Registry
docker run -d \
  --name mikroman \
  --restart unless-stopped \
  -p 1928:1928 \
  -v mikroman_data:/data \
  ghcr.io/masseselsev/mikroman:latest

# Option B: Clone and run via Docker Compose
git clone https://github.com/masseselsev/mikroman.git
cd mikroman
docker compose up -d
```
Document support for `linux/amd64`, `linux/arm64`, and `linux/arm/v7` platforms.

- [ ] **Step 2: Run all backend tests and linter**

Run:
```bash
.venv/bin/ruff check .
.venv/bin/pytest -v
```
Expected: All tests pass, zero lint errors.

- [ ] **Step 3: Run frontend tests and bundle build**

Run:
```bash
cd frontend && npm test -- --run && npm run build
```
Expected: All tests pass, build succeeds cleanly.
