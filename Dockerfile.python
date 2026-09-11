# syntax=docker/dockerfile:1.7
# ==========================================
# Frontend build
# ==========================================
# The compiled bundle is built here rather than on the host. frontend/dist is
# generated output and is not committed, so a Dockerfile that COPYied it could
# only ever build on a machine that had already run `npm run build` - which made
# the documented `git clone && docker compose up -d` fail on the COPY step.
# Using --platform=$BUILDPLATFORM builds the static frontend bundle natively on
# the runner host rather than under slow QEMU emulation during multi-arch builds.
FROM --platform=$BUILDPLATFORM node:22-alpine AS frontend

WORKDIR /build

# Manifest first, so `npm ci` is only re-run when the dependencies change and
# not on every source edit.
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --prefer-offline --fetch-timeout=180000

COPY frontend/ ./
RUN npm run build

# ==========================================
# Python wheel builder
# ==========================================
# Four hard dependencies publish no linux/arm/v7 wheels on PyPI and ship source
# distributions only: cffi (pulled in by cryptography), greenlet (pulled in by
# SQLAlchemy's asyncio support), MarkupSafe (pulled in by Mako/alembic) and
# PyYAML. python:3.12-slim contains no compiler and no libffi headers, so
# installing them directly in the runtime stage aborts the arm/v7 build at the
# very first source package. armv7 is the architecture of RB4011-class MikroTik
# boards, so that leg has to work.
#
# This throwaway stage owns the toolchain and resolves every requirement into a
# local wheelhouse: packages that already publish an armv7 wheel are downloaded
# as-is, the four above are compiled once here. Nothing from this stage reaches
# the shipped image - the runtime installs the finished wheels and the compiler
# is left behind.
#
# Deliberately NOT --platform=$BUILDPLATFORM: wheels carry native code and must
# be compiled for the target architecture, so this stage runs emulated.
FROM python:3.12-slim AS wheelbuilder

# build-essential supplies gcc/g++/make (greenlet is C++, not just C) and
# libffi-dev supplies the ffi.h header cffi compiles against. Apt lists are left
# in place on purpose: this stage is discarded, so cleaning them saves nothing.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends build-essential libffi-dev

# The pip download cache is a BuildKit cache mount rather than `--no-cache-dir`.
# On a slow or flaky link to files.pythonhosted.org a single read timeout fails
# the whole layer, and with no cache every retry re-downloaded all 43 wheels
# from scratch - so a build could never make forward progress. With the mount,
# each attempt keeps whatever it managed to fetch and picks up where it left
# off. The cache lives in the builder, not in the image, so the image stays the
# same size.
COPY backend/requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip wheel --wheel-dir /wheels --default-timeout=180 --retries=15 \
    -r /tmp/requirements.txt

# ==========================================
# Production Python Runtime (Slim)
# ==========================================
FROM python:3.12-slim AS runtime

LABEL maintainer="masseselsev" \
      description="Lightweight MikroTik RouterOS Companion Container"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MALLOC_ARENA_MAX=2 \
    MALLOC_TRIM_THRESHOLD_=131072 \
    DATABASE_URL="sqlite+aiosqlite:////data/app.db"

WORKDIR /app

# Install from the wheelhouse only. --no-index is what keeps this honest: if a
# dependency were ever missing from the builder's output, pip fails loudly here
# instead of quietly reaching for PyPI and trying to compile a source package in
# an image that has no compiler. The wheelhouse arrives as a bind mount, so it
# is never written into a layer and adds nothing to the shipped image size.
COPY backend/requirements.txt /app/backend/requirements.txt
RUN --mount=type=bind,from=wheelbuilder,source=/wheels,target=/wheels \
    pip install --no-cache-dir --no-index --find-links=/wheels \
    -r /app/backend/requirements.txt

# Copy backend code
COPY backend/ /app/backend/

# Compiled frontend assets from the build stage above
COPY --from=frontend /build/dist/ /app/frontend/dist/

# Create persistent storage volume mount directory
RUN mkdir -p /data

EXPOSE 1928

VOLUME ["/data"]

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "1928", "--loop", "asyncio"]
