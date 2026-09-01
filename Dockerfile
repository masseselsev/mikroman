# ==========================================
# Frontend build
# ==========================================
# The compiled bundle is built here rather than on the host. frontend/dist is
# generated output and is not committed, so a Dockerfile that COPYied it could
# only ever build on a machine that had already run `npm run build` - which made
# the documented `git clone && docker compose up -d` fail on the COPY step.
FROM node:22-alpine AS frontend

WORKDIR /build

# Manifest first, so `npm ci` is only re-run when the dependencies change and
# not on every source edit.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

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

# Install Python requirements
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir --default-timeout=120 --retries=10 -r /app/backend/requirements.txt

# Copy backend code
COPY backend/ /app/backend/

# Compiled frontend assets from the build stage above
COPY --from=frontend /build/dist/ /app/frontend/dist/

# Create persistent storage volume mount directory
RUN mkdir -p /data

EXPOSE 1928

VOLUME ["/data"]

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "1928"]
