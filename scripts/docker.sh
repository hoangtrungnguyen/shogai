#!/usr/bin/env bash
# Docker mode: build and run Shōgai stamped with its version (VERSION file), git commit and build time.
#   ./scripts/docker.sh                 build shogai:<VERSION> (+ shogai:latest) and start it on :8000
#   ./scripts/docker.sh build           build only
#   ./scripts/docker.sh logs -f | ps | down     anything else goes to `docker compose`
# Run an older image instead: SHOGAI_VERSION=0.1.0 docker compose up -d --no-build
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -f backend/.env ] || { echo "Missing backend/.env — copy backend/.env.example and set GEMINI_API_KEY"; exit 1; }

VERSION="$(tr -d '[:space:]' < VERSION)"
if ! [[ "$VERSION" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
  echo "VERSION must be a valid image tag (letters, digits, '.', '_', '-'), got: '$VERSION'" >&2
  exit 1
fi
if [ -n "${SHOGAI_VERSION:-}" ] && [ "$SHOGAI_VERSION" != "$VERSION" ]; then
  echo "This script builds the version in VERSION ($VERSION). To run the existing $SHOGAI_VERSION image:" >&2
  echo "  SHOGAI_VERSION=$SHOGAI_VERSION docker compose up -d --no-build" >&2
  exit 1
fi

SHOGAI_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then SHOGAI_COMMIT="$SHOGAI_COMMIT-dirty"; fi
SHOGAI_BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export SHOGAI_VERSION="$VERSION" SHOGAI_COMMIT SHOGAI_BUILD_DATE

if [ $# -eq 0 ]; then
  docker compose up -d --build
  echo "Shōgai $SHOGAI_VERSION ($SHOGAI_COMMIT) → http://127.0.0.1:${SHOGAI_PORT:-8000}"
  exit 0
fi
exec docker compose "$@"
