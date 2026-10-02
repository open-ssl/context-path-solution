# syntax=docker/dockerfile:1

# -----------------------------------------------------------------------------
# Stage 1: Build a static binary with module caching.
# -----------------------------------------------------------------------------
FROM golang:1.22-alpine AS builder

WORKDIR /src

# Dependencies first (cached across source changes).
COPY services/streamer/go.mod services/streamer/go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download

COPY services/streamer/ ./
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /streamer .

# -----------------------------------------------------------------------------
# Stage 2: Minimal runtime.
# -----------------------------------------------------------------------------
FROM alpine:latest

RUN apk add --no-cache ca-certificates \
    && adduser -D -u 10001 appuser

COPY --from=builder /streamer /streamer

USER appuser
ENTRYPOINT ["/streamer"]
