# ==========================================
# Production Python Runtime (Slim)
# ==========================================
FROM python:3.12-slim AS runtime

LABEL maintainer="MikroMan Team" \
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

# Copy compiled frontend assets
COPY frontend/dist/ /app/frontend/dist/

# Create persistent storage volume mount directory
RUN mkdir -p /data

EXPOSE 1928

VOLUME ["/data"]

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "1928"]
