# syntax=docker/dockerfile:1
#
# Shōgai Relay: the server people (and their agents) use to ask someone a question with a key that person gave
# them. FastAPI + SQLite, no build step for the page. Build and run it with docker compose — see README.md.

ARG PYTHON_VERSION=3.13
ARG UV_VERSION=0.11.16

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# --------------------------------------------------- Python environment
FROM python:${PYTHON_VERSION}-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first: this layer is reused until pyproject.toml or uv.lock change.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --locked --no-dev --no-install-project
COPY pyproject.toml uv.lock ./
COPY shogai_relay ./shogai_relay
# --reinstall-package: uv's cache keys a local project by its pyproject.toml, so without it a rebuild after a
# code change could install the previously built (stale) package.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable --reinstall-package shogai-relay

# -------------------------------------------------------------- runtime
FROM python:${PYTHON_VERSION}-slim AS runtime
RUN useradd --create-home --uid 1000 --user-group relay \
 && mkdir -p /data \
 && chown relay:relay /data
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SHOGAI_RELAY_DATA_DIR=/data
USER relay
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

# One process: rate limits live in memory. --forwarded-allow-ips trusts the client address the reverse proxy
# (Caddy in docker-compose.yml) passes on; keep the relay's own port off the internet.
CMD ["uvicorn", "shogai_relay.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
