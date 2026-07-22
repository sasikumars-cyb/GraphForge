#!/usr/bin/env bash
# Starts the containerized dev stack with the local demo environment enabled
# (see demo/DEMO_GUIDE.md) - the backend talks to the four local demo repos
# under demo/repositories/ instead of real GitHub.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

docker compose \
  -f "$ROOT_DIR/docker/docker-compose.yml" \
  -f "$ROOT_DIR/docker/docker-compose.demo.yml" \
  up --build
