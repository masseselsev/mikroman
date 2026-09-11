# Stage 1: Build Vue 3 Frontend
FROM --platform=$BUILDPLATFORM node:22-alpine AS frontend-builder
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci --prefer-offline --no-audit
COPY frontend/ ./
RUN npm run build

# Stage 2: Build Golang Backend (Statically Linked, CGO Disabled, Vendored, Fast Cross-Compilation)
FROM --platform=$BUILDPLATFORM golang:alpine AS go-builder
WORKDIR /src
COPY backend-go/ ./

ARG TARGETOS TARGETARCH TARGETVARIANT
RUN export GOARM=$(echo "${TARGETVARIANT}" | sed 's/^v//') && \
    CGO_ENABLED=0 GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH:-amd64} GOARM=${GOARM} \
    go build -mod=vendor -ldflags="-s -w" -o /mikroman ./cmd/mikroman

# Stage 3: Minimal Alpine Runtime Container (< 30 MB)
FROM alpine:3.20
RUN apk --no-cache add ca-certificates tzdata mailcap
WORKDIR /app

COPY --from=go-builder /mikroman /app/mikroman
COPY --from=frontend-builder /frontend/dist /app/frontend/dist

EXPOSE 1928
VOLUME ["/data"]

ENV DATA_DIR=/data \
    DIST_DIR=/app/frontend/dist \
    PORT=1928

ENTRYPOINT ["/app/mikroman", "-data-dir=/data", "-dist-dir=/app/frontend/dist"]
