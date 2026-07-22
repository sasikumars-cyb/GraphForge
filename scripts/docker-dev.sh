#!/usr/bin/env bash
# One command, fully containerized: Postgres + backend (uvicorn --reload) +
# frontend (Vite dev server), source bind-mounted for hot reload. Requires
# only Docker - no local Python or Node install needed.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

docker compose -f "$ROOT_DIR/docker/docker-compose.yml" up --build
