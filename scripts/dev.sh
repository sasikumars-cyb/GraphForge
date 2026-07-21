#!/usr/bin/env bash
# Runs the full stack locally: Postgres via Docker Compose, backend and
# frontend as native dev servers (so both get hot-reload).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cleanup() {
  echo ""
  echo "==> Stopping dev servers"
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT

echo "==> Starting Postgres (docker compose)"
docker compose -f "$ROOT_DIR/docker/docker-compose.yml" up -d db

echo "==> Starting backend (http://localhost:8000)"
(
  cd "$ROOT_DIR/backend"
  # shellcheck disable=SC1091
  source .venv/bin/activate
  uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
) &
BACKEND_PID=$!

echo "==> Starting frontend (http://localhost:5173)"
(
  cd "$ROOT_DIR/frontend"
  npm run dev
) &
FRONTEND_PID=$!

wait "$BACKEND_PID" "$FRONTEND_PID"
