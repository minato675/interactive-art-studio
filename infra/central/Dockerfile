FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58 AS base
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY apps ./apps
COPY packages ./packages
COPY controller ./controller
RUN uv sync --locked --no-dev --package a504-central --no-editable

FROM base AS runtime
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.revision=$VCS_REF \
    org.opencontainers.image.created=$BUILD_DATE \
    org.opencontainers.image.source="https://lab.ssafy.com/s15-final/S15P31A504"
RUN groupadd --gid 10001 ranel && useradd --uid 10001 --gid 10001 --no-create-home ranel \
    && mkdir -p /data && chown 10001:10001 /data
ENV PATH="/app/.venv/bin:$PATH" STUDIO_DATA=/data
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "central.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS test
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libegl1 libopengl0 libdbus-1-3 libxkbcommon0 libglib2.0-0 libfontconfig1 libfreetype6 \
    && rm -rf /var/lib/apt/lists/* \
    && uv sync --locked --all-packages
COPY tests ./tests
COPY td/artwork_reader.py td/formation_sim.py ./td/
COPY td/fake_hands.py ./td/fake_hands.py
COPY td/project/Json/sensor_input_config_ai.json ./td/project/Json/sensor_input_config_ai.json
COPY infra ./infra
ENV QT_QPA_PLATFORM=offscreen
CMD ["uv", "run", "--no-sync", "pytest", "-q"]
