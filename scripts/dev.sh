#!/usr/bin/env bash
# Hot-reloading dev mode: API on :8000, Vite UI on :5173 (proxies /api), public page on :8001.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -f backend/.env ] || { echo "Missing backend/.env — copy backend/.env.example and set GEMINI_API_KEY"; exit 1; }
[ -d frontend/node_modules ] || npm --prefix frontend install
(cd backend && uv sync --quiet)
trap 'kill 0' EXIT INT TERM
uv run --project backend uvicorn shogai.main:app --app-dir backend --reload --reload-dir backend/shogai --port 8000 &
npm --prefix frontend run dev &
echo "Shōgai dev → http://localhost:5173"
wait
