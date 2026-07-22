#!/usr/bin/env bash
# Builds and runs the production-style stack: backend without --reload,
# frontend as a static build served by Nginx.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

docker compose -f "$ROOT_DIR/docker/docker-compose.prod.yml" up --build
