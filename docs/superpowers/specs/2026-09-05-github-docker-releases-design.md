# GitHub Actions Multi-Arch Docker Container Release Pipeline

**Date:** 2026-09-05  
**Topic:** Automated Docker Container Builds on GitHub Releases  
**Registry:** GitHub Container Registry (`ghcr.io`)  
**Target Architectures:** `linux/amd64`, `linux/arm64`, `linux/arm/v7`  

---

## 1. Overview & Objectives

This specification defines the automated CI/CD pipeline for building and publishing production-ready, multi-architecture Docker images for **MikroMan** to GitHub Container Registry (`ghcr.io`).

### Key Goals
1. **Automated Triggering:** Automatically build and publish images upon GitHub Release publication (`release: types: [published]`), with manual fallback triggering (`workflow_dispatch`).
2. **Broad Hardware Architecture Support:** Target `linux/amd64`, `linux/arm64`, and `linux/arm/v7` to support standard servers, Cloud Hosted Routers (CHR), modern 64-bit ARM RouterOS devices (e.g. RB5009, hAP ax), and 32-bit ARM RouterOS devices (e.g. RB4011, hAP ac).
3. **Strict Quality Gate:** Enforce unit testing, linting, and asset build compilation before triggering Docker image builds to ensure zero broken builds reach production.
4. **BuildKit Performance Optimizations:** Utilize native host execution for CPU-agnostic frontend asset bundling and GitHub Actions layer caching (`type=gha`).
5. **Zero External Secrets:** Utilize GitHub Actions built-in `GITHUB_TOKEN` permissions (`packages: write`) for seamless, maintenance-free authentication.

---

## 2. Pipeline Architecture & Workflow Design

The workflow will be defined in [`.github/workflows/release-docker.yml`](../../.github/workflows/release-docker.yml).

### 2.1 Workflow Triggers & Permissions
```yaml
name: Release Docker Container

on:
  release:
    types: [published]
  workflow_dispatch:

permissions:
  contents: read
  packages: write
```

### 2.2 Stage 1: Quality Gate (`validate`)
The validation job executes on `ubuntu-latest` and verifies codebase integrity:
1. **Checkout:** `actions/checkout@v4`.
2. **Python Environment:** `actions/setup-python@v5` configured for Python 3.12 with pip cache.
3. **Backend Linter & Tests:**
   * Install test dependencies: `pip install pytest pytest-asyncio ruff -r backend/requirements.txt`
   * Run linter: `ruff check .`
   * Run unit tests: `pytest -v`
4. **Node.js Environment:** `actions/setup-node@v4` configured for Node 22 with npm cache.
5. **Frontend Tests & Production Build:**
   * Install dependencies: `cd frontend && npm ci`
   * Run frontend tests: `npm test -- --run`
   * Verify production compilation: `npm run build`

If any validation step fails, the workflow terminates immediately.

### 2.3 Stage 2: Multi-Arch Build & Publish (`build-and-push`)
Depends directly on the success of `validate` (`needs: [validate]`):
1. **Checkout:** `actions/checkout@v4`.
2. **QEMU Emulation:** `docker/setup-qemu-action@v3` with support for `arm64` and `arm/v7`.
3. **Docker Buildx:** `docker/setup-buildx-action@v3` configuring BuildKit builder.
4. **GHCR Authentication:** `docker/login-action@v3` to `ghcr.io` using `${{ github.actor }}` and `${{ secrets.GITHUB_TOKEN }}`.
5. **Metadata & OCI Tag Generation:** `docker/metadata-action@v5`:
   * Primary Image: `ghcr.io/${{ github.repository }}`
   * Tagging rules:
     * `type=semver,pattern={{version}}` (e.g. `v1.2.3`)
     * `type=semver,pattern={{major}}.{{minor}}` (e.g. `1.2`)
     * `type=semver,pattern={{major}}` (e.g. `1`)
     * `type=raw,value=latest,enable=${{ github.event_name == 'release' }}`
     * `type=sha,format=short`
6. **Multi-Platform Build & Push:** `docker/build-push-action@v6`:
   * Platforms: `linux/amd64,linux/arm64,linux/arm/v7`
   * Context: `.`
   * File: `./Dockerfile`
   * Push: `true`
   * Tags & Labels: Passed directly from metadata step.
   * Cache:
     * `cache-from: type=gha`
     * `cache-to: type=gha,mode=max`

---

## 3. Dockerfile & Dependency Optimizations

### 3.1 Native Runner Frontend Bundling
In [`Dockerfile`](../../Dockerfile):
```dockerfile
# Change from:
FROM node:22-alpine AS frontend
# To:
FROM --platform=$BUILDPLATFORM node:22-alpine AS frontend
```
* **Rationale:** The frontend asset bundle (`frontend/dist`) consists of standard HTML, JS, and CSS files that do not depend on the target runtime CPU architecture. Compiling via native host runner architecture (`BUILDPLATFORM`) avoids 15–20 minutes of emulated Node.js execution under QEMU for ARM targets.

### 3.2 ARMv7 Wheel Compatibility Marker
In [`backend/requirements.txt`](../../backend/requirements.txt):
```text
uvloop==0.22.1; sys_platform != "win32" and (platform_machine == "x86_64" or platform_machine == "aarch64")
```
* **Rationale:** `uvloop` 0.22.1 distributes pre-compiled binary wheels exclusively for `x86_64` and `aarch64`. Adding the platform marker ensures `amd64` and `arm64` benefit from high-throughput uvloop event loops, while `armv7l` cleanly falls back to standard Python `asyncio` without requiring GCC/C-toolchain compilation inside `python:3.12-slim`.

---

## 4. Documentation & Usage Updates

Update [`README.md`](../../README.md) to document running the published GHCR container directly:
```bash
docker run -d \
  --name mikroman \
  --restart unless-stopped \
  -p 1928:1928 \
  -v mikroman_data:/data \
  ghcr.io/masseselsev/mikroman:latest
```
And provide updated `docker-compose.yml` snippet referencing the GHCR image.

---

## 5. Verification Plan

1. **Workflow Syntax & Schema:**
   * Validate YAML syntax using Python `yaml.safe_load`.
   * Verify all GitHub action versions (`checkout@v4`, `setup-python@v5`, `setup-node@v4`, `setup-qemu@v3`, `setup-buildx@v3`, `login-action@v3`, `metadata-action@v5`, `build-push-action@v6`).
2. **Dependency Resolution:**
   * Test pip requirements parsing on Python 3.12 to verify `uvloop` marker evaluation for `x86_64` vs `armv7l`.
3. **Local Quality Gate Run:**
   * Run `ruff check .`
   * Run `pytest -v`
   * Run `cd frontend && npm test -- --run`
   * Run `cd frontend && npm run build`
4. **Container Build Verification:**
   * Run a local BuildKit build check on the updated Dockerfile.
