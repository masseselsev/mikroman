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
# Production Python Runtime (Slim)
# ==========================================
FROM python:3.12-slim AS runtime

LABEL maintainer="masseselsev" \
      description="Lightweight MikroTik RouterOS Companion Container"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_URL="sqlite+aiosqlite:////data/app.db" \
    PORT=1928

WORKDIR /app

# Install Python requirements.
#
# The pip download cache is a BuildKit cache mount rather than `--no-cache-dir`.
# On a slow or flaky link to files.pythonhosted.org a single read timeout fails
# the whole layer, and with no cache every retry re-downloaded all 43 wheels
# from scratch - so a build could never make forward progress. With the mount,
# each attempt keeps whatever it managed to fetch and picks up where it left
# off. The cache lives in the builder, not in the image, so the image stays the
# same size.
COPY backend/requirements.txt /app/backend/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --default-timeout=180 --retries=15 -r /app/backend/requirements.txt

# Copy backend code
COPY backend/ /app/backend/

# Compiled frontend assets from the build stage above
COPY --from=frontend /build/dist/ /app/frontend/dist/

# Create persistent storage volume mount directory
RUN mkdir -p /data

EXPOSE 1928

VOLUME ["/data"]

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "1928"]
