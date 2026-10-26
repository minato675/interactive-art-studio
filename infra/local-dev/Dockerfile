# syntax=docker/dockerfile:1
# Official uv + CPython 3.12 + Debian Bookworm; multi-platform image pinned by digest.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58 AS base

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

COPY pyproject.toml uv.lock README.md AGENTS.md ./
COPY apps ./apps
COPY packages ./packages
COPY controller ./controller
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package a504-central
COPY infra/local-dev/start.py infra/local-dev/verify.py ./infra/local-dev/
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --create-home app \
    && mkdir -p /data \
    && chown 10001:10001 /data \
    && find apps packages controller infra -type f -name '*.py' -exec chmod 0644 {} +

FROM base AS runtime
ENV STUDIO_DATA=/data
USER 10001:10001
EXPOSE 8000
CMD ["python", "infra/local-dev/start.py"]

FROM base AS test
# Qt Widgets tests run offscreen; no host display or Windows DLLs are mounted.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 libegl1 libopengl0 libdbus-1-3 libxkbcommon0 \
        libglib2.0-0 libfontconfig1 libfreetype6 \
    && rm -rf /var/lib/apt/lists/*
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --all-packages
COPY tests ./tests
COPY td/artwork_reader.py td/formation_sim.py ./td/
COPY td/fake_hands.py ./td/fake_hands.py
COPY td/project/Json/sensor_input_config_ai.json ./td/project/Json/sensor_input_config_ai.json
COPY infra ./infra
RUN find tests td -type f -name '*.py' -exec chmod 0644 {} + \
    && mkdir -p controller/runtime-data \
    && chown 10001:10001 controller/runtime-data
ENV QT_QPA_PLATFORM=offscreen \
    UV_CACHE_DIR=/tmp/uv-cache \
    RUFF_CACHE_DIR=/tmp/ruff-cache \
    XDG_CACHE_HOME=/tmp/cache \
    STUDIO_DATA=/tmp/studio-tests
USER 10001:10001
CMD ["uv", "run", "--no-sync", "pytest", "-q", "-o", "cache_dir=/tmp/pytest-cache"]
