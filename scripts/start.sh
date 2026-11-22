#!/usr/bin/env bash
# Single-server mode: build the UI once, then FastAPI serves UI + API on :8000.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -f backend/.env ] || { echo "Missing backend/.env — copy backend/.env.example and set GEMINI_API_KEY"; exit 1; }
[ -d frontend/node_modules ] || npm --prefix frontend install
npm --prefix frontend run build
cd backend && uv sync --quiet && exec uv run shogai
