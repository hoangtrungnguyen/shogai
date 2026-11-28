# syntax=docker/dockerfile:1
#
# Shōgai in one image: Node builds the React UI, then FastAPI (uvicorn) serves UI + API on :8000
# and the public page (your public notes, read-only) on :8001.
# Build it with ./scripts/docker.sh (stamps version, commit and build time) — see README.md.

ARG NODE_VERSION=24
ARG PYTHON_VERSION=3.13
ARG UV_VERSION=0.11.16

# ------------------------------------------------------------------- UI
FROM node:${NODE_VERSION}-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --------------------------------------------------- Python environment
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim AS backend
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app/backend
# Dependencies first: this layer is reused until pyproject.toml or uv.lock change.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=backend/pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=backend/uv.lock,target=uv.lock \
    uv sync --locked --no-dev --no-install-project
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/shogai ./shogai
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

# -------------------------------------------------------------- runtime
FROM python:${PYTHON_VERSION}-slim AS runtime
RUN useradd --create-home --uid 1000 --user-group shogai \
 && mkdir -p /app/data \
 && chown shogai:shogai /app/data
WORKDIR /app
COPY --from=backend /app/backend /app/backend
COPY --from=web /web/dist /app/frontend/dist
COPY VERSION /app/VERSION
ENV PATH="/app/backend/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SHOGAI_DATA_DIR=/app/data \
    SHOGAI_FRONTEND_DIST=/app/frontend/dist
USER shogai
EXPOSE 8000 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]

# Build stamp. Kept last so a new version or commit only rebuilds these metadata layers.
# The app reads its version from /app/VERSION; scripts/docker.sh passes the same value as APP_VERSION.
ARG APP_VERSION=""
ARG GIT_COMMIT=""
ARG BUILD_DATE=""
ENV SHOGAI_COMMIT=${GIT_COMMIT} \
    SHOGAI_BUILD_DATE=${BUILD_DATE}
LABEL org.opencontainers.image.title="Shōgai" \
      org.opencontainers.image.description="Personal relationship advisor (FastAPI + Gemini, Markdown-folder database)" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${GIT_COMMIT}" \
      org.opencontainers.image.created="${BUILD_DATE}"

# A single worker on purpose: background jobs and their progress streams live in memory.
CMD ["uvicorn", "shogai.main:app", "--host", "0.0.0.0", "--port", "8000"]
