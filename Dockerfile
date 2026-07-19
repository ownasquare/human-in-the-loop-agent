# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS web-builder
WORKDIR /build/web
ENV CYPRESS_INSTALL_BINARY=0
COPY web/package.json web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY web/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.11.29 AS uv-bin

FROM python:3.12-slim-bookworm AS python-builder
COPY --from=uv-bin /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm AS runtime
RUN groupadd --gid 10001 relay \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/relay relay \
    && install -d -m 0700 -o 10001 -g 10001 /app/data \
    && install -d -m 0755 -o 10001 -g 10001 /app/static

WORKDIR /app
COPY --from=python-builder --chown=10001:10001 /app/.venv /app/.venv
COPY --from=python-builder --chown=10001:10001 /app/src /app/src
COPY --from=web-builder --chown=10001:10001 /build/web/dist /app/static

ENV PATH="/app/.venv/bin:$PATH" \
    XDG_CACHE_HOME=/tmp/.cache \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANGGRAPH_STRICT_MSGPACK=true \
    RELAY_MODE=demo \
    RELAY_DATA_DIR=/app/data \
    RELAY_STATIC_DIR=/app/static \
    RELAY_HOST=127.0.0.1 \
    RELAY_PORT=8000

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3).read()"]

CMD ["relay-api"]
